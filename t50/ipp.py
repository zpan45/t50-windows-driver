"""Minimal IPP/1.1 + IPP/2.0 server that Windows IPP Class Driver can print to."""

from __future__ import annotations

import logging
import struct
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, BinaryIO, Callable
from urllib.parse import urlparse

log = logging.getLogger("t50.ipp")

# IPP delimiters / value tags (RFC 8010)
TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_PRINTER = 0x04
TAG_UNSUPPORTED = 0x05
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_OCTET = 0x30
TAG_RANGE = 0x32
TAG_RESOLUTION = 0x33
TAG_TEXT = 0x41
TAG_NAME = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_CHARSET = 0x47
TAG_LANGUAGE = 0x48
TAG_MIME = 0x49
TAG_BEG_COLLECTION = 0x34
TAG_END_COLLECTION = 0x37
TAG_MEMBER_NAME = 0x4A

OP_PRINT_JOB = 0x0002
OP_VALIDATE_JOB = 0x0004
OP_CREATE_JOB = 0x0005
OP_SEND_DOCUMENT = 0x0006
OP_CANCEL_JOB = 0x0008
OP_GET_JOB_ATTRIBUTES = 0x0009
OP_GET_JOBS = 0x000A
OP_GET_PRINTER_ATTRIBUTES = 0x000B
OP_CLOSE_JOB = 0x003B
OP_IDENTIFY_PRINTER = 0x003C
OP_GET_PRINTER_SUPPORTED_VALUES = 0x001B

STATUS_OK = 0x0000
STATUS_CLIENT_ERROR = 0x0400
STATUS_BAD_REQUEST = 0x0400
STATUS_NOT_FOUND = 0x0406
STATUS_NOT_POSSIBLE = 0x0404
STATUS_SERVER_ERROR = 0x0500
STATUS_OPERATION_NOT_SUPPORTED = 0x0501

PRINTER_IDLE = 3
PRINTER_PROCESSING = 4
PRINTER_STOPPED = 5
JOB_PENDING = 3
JOB_PROCESSING = 5
JOB_COMPLETED = 9
JOB_CANCELED = 7
JOB_ABORTED = 8


def read_exact(fp: BinaryIO, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = fp.read(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def read_chunked_body(fp: BinaryIO) -> bytes:
    chunks: list[bytes] = []
    sizes: list[int] = []
    while True:
        line = fp.readline()
        if not line:
            break
        size_s = line.split(b";", 1)[0].strip()
        if not size_s:
            continue
        size = int(size_s, 16)
        sizes.append(size)
        if size == 0:
            while True:
                extra = fp.readline()
                if extra in (b"\r\n", b"\n", b""):
                    break
            break
        chunks.append(read_exact(fp, size))
        fp.read(2)
    body = b"".join(chunks)
    log.info("chunked %s chunks sizes=%s total=%s", len(sizes), sizes[:20], len(body))
    return body


def read_http_body(handler: BaseHTTPRequestHandler) -> bytes:
    te = (handler.headers.get("Transfer-Encoding") or "").lower()
    if "chunked" in te:
        return read_chunked_body(handler.rfile)
    raw_len = handler.headers.get("Content-Length")
    if raw_len:
        return read_exact(handler.rfile, int(raw_len))
    return b""


def _u16(n: int) -> bytes:
    return struct.pack(">H", n & 0xFFFF)


def _u32(n: int) -> bytes:
    return struct.pack(">I", n & 0xFFFFFFFF)


def _s16(s: str | bytes) -> bytes:
    b = s.encode("utf-8") if isinstance(s, str) else s
    return _u16(len(b)) + b


def encode_attr(tag: int, name: str, value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, list):
        if not value:
            return b""
        out = encode_attr(tag, name, value[0])
        for extra in value[1:]:
            out += encode_attr(tag, "", extra)
        return out
    if tag == TAG_BEG_COLLECTION:
        # value is dict[str, tuple[tag, val]]
        out = bytes([TAG_BEG_COLLECTION]) + _s16(name) + _u16(0)
        assert isinstance(value, dict)
        for member, (mtag, mval) in value.items():
            out += bytes([TAG_MEMBER_NAME]) + _s16("") + _s16(member)
            out += encode_attr(mtag, "", mval)
        out += bytes([TAG_END_COLLECTION]) + _s16("") + _u16(0)
        return out
    payload = _encode_value(tag, value)
    return bytes([tag]) + _s16(name) + _s16(payload)


def _encode_value(tag: int, value: Any) -> bytes:
    if tag in (TAG_TEXT, TAG_NAME, TAG_KEYWORD, TAG_URI, TAG_CHARSET, TAG_LANGUAGE, TAG_MIME, TAG_MEMBER_NAME):
        return value.encode("utf-8") if isinstance(value, str) else value
    if tag in (TAG_INTEGER, TAG_ENUM):
        return _u32(int(value))
    if tag == TAG_BOOLEAN:
        return bytes([1 if value else 0])
    if tag == TAG_RESOLUTION:
        x, y, units = value
        return struct.pack(">IIB", x, y, units)
    if tag == TAG_RANGE:
        lo, hi = value
        return struct.pack(">ii", lo, hi)
    if tag == TAG_OCTET:
        return value if isinstance(value, (bytes, bytearray)) else bytes(value)
    raise TypeError(f"unsupported IPP tag 0x{tag:02x}")


@dataclass
class IppRequest:
    version: tuple[int, int]
    operation: int
    request_id: int
    attributes: dict[str, list[Any]]
    document: bytes = b""


def parse_ipp(data: bytes) -> IppRequest:
    if len(data) < 8:
        raise ValueError("IPP message too short")
    major, minor = data[0], data[1]
    operation = struct.unpack_from(">H", data, 2)[0]
    request_id = struct.unpack_from(">I", data, 4)[0]
    attrs: dict[str, list[Any]] = {}
    i = 8
    current_name = ""
    while i < len(data):
        tag = data[i]
        i += 1
        if tag == TAG_END:
            break
        if tag in (TAG_OPERATION, TAG_JOB, TAG_PRINTER, TAG_UNSUPPORTED):
            continue
        if i + 2 > len(data):
            break
        nlen = struct.unpack_from(">H", data, i)[0]
        i += 2
        name = data[i : i + nlen].decode("utf-8", "replace")
        i += nlen
        if i + 2 > len(data):
            break
        vlen = struct.unpack_from(">H", data, i)[0]
        i += 2
        raw = data[i : i + vlen]
        i += vlen
        if name:
            current_name = name
        if not current_name:
            continue
        parsed = _decode_value(tag, raw)
        attrs.setdefault(current_name, []).append(parsed)
    document = data[i:]
    return IppRequest((major, minor), operation, request_id, attrs, document)


def _decode_value(tag: int, raw: bytes) -> Any:
    if tag in (TAG_INTEGER, TAG_ENUM) and len(raw) == 4:
        return struct.unpack(">I", raw)[0]
    if tag == TAG_BOOLEAN and raw:
        return raw[0] != 0
    if tag == TAG_RESOLUTION and len(raw) == 9:
        x, y, units = struct.unpack(">IIB", raw)
        return (x, y, units)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw


def build_ipp(
    version: tuple[int, int],
    status: int,
    request_id: int,
    groups: list[tuple[int, list[tuple[int, str, Any]]]],
) -> bytes:
    out = bytearray()
    out += bytes(version)
    out += _u16(status)
    out += _u32(request_id)
    for gtag, attrs in groups:
        out.append(gtag)
        for tag, name, value in attrs:
            out += encode_attr(tag, name, value)
    out.append(TAG_END)
    return bytes(out)


def first(req: IppRequest, name: str, default: Any = None) -> Any:
    vals = req.attributes.get(name)
    return vals[0] if vals else default


@dataclass
class Job:
    job_id: int
    state: int = JOB_PENDING
    document: bytes = b""
    document_format: str = "application/octet-stream"
    name: str = ""


@dataclass
class PrinterModel:
    name: str
    uri: str
    make_and_model: str
    width_mm: int
    height_mm: int
    dpi: int = 203
    state: int = PRINTER_IDLE
    state_reasons: str = "none"
    accepting: bool = True
    jobs: dict[int, Job] = field(default_factory=dict)
    next_job_id: int = 1
    lock: threading.Lock = field(default_factory=threading.Lock)

    def media_hundredths(self) -> tuple[int, int]:
        # IPP units: hundredths of a millimetre
        return self.width_mm * 100, max(self.height_mm, 1) * 100

    def media_size_name(self) -> str:
        # PWG 5101.1 self-describing name. "40x30mm" is not a valid keyword,
        # so Windows IPP Class Driver ignored it and fell back to A4.
        x_um = self.width_mm * 1000
        y_um = max(self.height_mm, 1) * 1000
        return f"om_{self.width_mm}x{self.height_mm}-label_{x_um}x{y_um}um"


PrintHandler = Callable[[Job, "PrinterModel"], None]


class IppPrinterApp:
    def __init__(self, model: PrinterModel, on_print: PrintHandler, host: str = "127.0.0.1", port: int = 8631):
        self.model = model
        self.on_print = on_print
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None

    def serve_forever(self, ready_event: threading.Event | None = None) -> None:
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            timeout = 120

            def log_message(self, fmt: str, *args) -> None:
                log.info("%s - " + fmt, self.address_string(), *args)

            def do_GET(self) -> None:  # noqa: N802
                body = b"t50 ipp printer\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                data = read_http_body(self)
                log.info(
                    "IPP POST path=%s cl=%s te=%s body=%s",
                    self.path,
                    self.headers.get("Content-Length"),
                    self.headers.get("Transfer-Encoding"),
                    len(data),
                )
                try:
                    if len(data) < 8:
                        raise ValueError(
                            f"IPP message too short ({len(data)} bytes); "
                            f"headers={dict(self.headers)}"
                        )
                    req = parse_ipp(data)
                    resp = app.handle(req)
                except Exception:
                    log.exception("IPP request failed")
                    req_id = 1
                    if len(data) >= 8:
                        req_id = struct.unpack_from(">I", data, 4)[0]
                    resp = build_ipp(
                        (2, 0),
                        STATUS_SERVER_ERROR,
                        req_id,
                        [
                            (
                                TAG_OPERATION,
                                [
                                    (TAG_CHARSET, "attributes-charset", "utf-8"),
                                    (TAG_LANGUAGE, "attributes-natural-language", "en"),
                                    (TAG_TEXT, "status-message", "server-error"),
                                ],
                            )
                        ],
                    )
                self.send_response(200)
                self.send_header("Content-Type", "application/ipp")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
                self.wfile.flush()

        ThreadingHTTPServer.allow_reuse_address = True
        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        log.info("IPP listening on ipp://%s:%s/ipp/print", self.host, self.port)
        if ready_event is not None:
            ready_event.set()
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def handle(self, req: IppRequest) -> bytes:
        log.info("IPP op=0x%04x id=%s attrs=%s doc=%s", req.operation, req.request_id, list(req.attributes), len(req.document))
        if req.operation == OP_GET_PRINTER_ATTRIBUTES:
            return self._printer_attrs(req)
        if req.operation in (OP_PRINT_JOB, OP_VALIDATE_JOB):
            return self._print_job(req, validate=req.operation == OP_VALIDATE_JOB)
        if req.operation == OP_CREATE_JOB:
            return self._create_job(req)
        if req.operation == OP_SEND_DOCUMENT:
            return self._send_document(req)
        if req.operation == OP_GET_JOBS:
            return self._get_jobs(req)
        if req.operation == OP_GET_JOB_ATTRIBUTES:
            return self._get_job_attrs(req)
        if req.operation == OP_CANCEL_JOB:
            return self._cancel_job(req)
        if req.operation in (OP_CLOSE_JOB, OP_IDENTIFY_PRINTER, OP_GET_PRINTER_SUPPORTED_VALUES):
            return self._ok(req)
        return self._status(req, STATUS_OPERATION_NOT_SUPPORTED, "operation-not-supported")

    def _op_group(self) -> list[tuple[int, str, Any]]:
        return [
            (TAG_CHARSET, "attributes-charset", "utf-8"),
            (TAG_LANGUAGE, "attributes-natural-language", "en"),
        ]

    def _ok(self, req: IppRequest, extra: list[tuple[int, list[tuple[int, str, Any]]]] | None = None) -> bytes:
        groups = [(TAG_OPERATION, self._op_group())]
        if extra:
            groups.extend(extra)
        return build_ipp(req.version, STATUS_OK, req.request_id, groups)

    def _status(self, req: IppRequest, status: int, message: str) -> bytes:
        return build_ipp(
            req.version,
            status,
            req.request_id,
            [(TAG_OPERATION, self._op_group() + [(TAG_TEXT, "status-message", message)])],
        )

    def _media_col(self) -> dict[str, tuple[int, Any]]:
        x, y = self.model.media_hundredths()
        return {
            "media-size": (
                TAG_BEG_COLLECTION,
                {
                    "x-dimension": (TAG_INTEGER, x),
                    "y-dimension": (TAG_INTEGER, y),
                },
            ),
            "media-size-name": (TAG_KEYWORD, self.model.media_size_name()),
            "media-top-margin": (TAG_INTEGER, 0),
            "media-bottom-margin": (TAG_INTEGER, 0),
            "media-left-margin": (TAG_INTEGER, 0),
            "media-right-margin": (TAG_INTEGER, 0),
            "media-source": (TAG_KEYWORD, "main"),
            "media-type": (TAG_KEYWORD, "labels"),
        }

    def _printer_attrs(self, req: IppRequest) -> bytes:
        m = self.model
        x, y = m.media_hundredths()
        size_name = m.media_size_name()
        media_col = self._media_col()
        printer_attrs: list[tuple[int, str, Any]] = [
            (TAG_URI, "printer-uri-supported", m.uri),
            (TAG_KEYWORD, "uri-security-supported", "none"),
            (TAG_KEYWORD, "uri-authentication-supported", "none"),
            (TAG_NAME, "printer-name", m.name),
            (TAG_TEXT, "printer-info", m.name),
            (TAG_TEXT, "printer-make-and-model", m.make_and_model),
            (TAG_ENUM, "printer-state", m.state),
            (TAG_KEYWORD, "printer-state-reasons", m.state_reasons),
            (TAG_BOOLEAN, "printer-is-accepting-jobs", m.accepting),
            (TAG_INTEGER, "printer-up-time", 1),
            (TAG_KEYWORD, "ipp-versions-supported", ["1.1", "2.0"]),
            (TAG_ENUM, "operations-supported", [
                OP_PRINT_JOB, OP_VALIDATE_JOB, OP_CREATE_JOB, OP_SEND_DOCUMENT,
                OP_CANCEL_JOB, OP_GET_JOB_ATTRIBUTES, OP_GET_JOBS,
                OP_GET_PRINTER_ATTRIBUTES, OP_CLOSE_JOB,
            ]),
            (TAG_CHARSET, "charset-configured", "utf-8"),
            (TAG_CHARSET, "charset-supported", "utf-8"),
            (TAG_LANGUAGE, "natural-language-configured", "en"),
            (TAG_LANGUAGE, "generated-natural-language-supported", "en"),
            (TAG_MIME, "document-format-default", "image/pwg-raster"),
            (TAG_URI, "printer-uuid", "urn:uuid:8f3c1b7e-5c2a-4d91-9e4f-00a50d50e000"),
            (TAG_MIME, "document-format-supported", [
                "image/pwg-raster",
                "image/jpeg",
                "image/png",
                "application/octet-stream",
            ]),
            (TAG_KEYWORD, "pdl-override-supported", "attempted"),
            (TAG_KEYWORD, "compression-supported", "none"),
            (TAG_BOOLEAN, "color-supported", False),
            (TAG_KEYWORD, "print-color-mode-supported", ["monochrome", "auto"]),
            (TAG_KEYWORD, "print-color-mode-default", "monochrome"),
            (TAG_KEYWORD, "sides-supported", "one-sided"),
            (TAG_KEYWORD, "sides-default", "one-sided"),
            (TAG_INTEGER, "copies-default", 1),
            (TAG_RANGE, "copies-supported", (1, 99)),
            (TAG_RANGE, "job-k-octets-supported", (0, 2_097_152)),
            (TAG_RANGE, "job-impressions-supported", (1, 99_999)),
            (TAG_RANGE, "job-media-sheets-supported", (1, 99_999)),
            (TAG_RESOLUTION, "printer-resolution-default", (406, 406, 3)),
            (TAG_RESOLUTION, "printer-resolution-supported", [(406, 406, 3), (m.dpi, m.dpi, 3)]),
            (TAG_RESOLUTION, "pwg-raster-document-resolution-supported", [(406, 406, 3), (m.dpi, m.dpi, 3)]),
            (TAG_ENUM, "print-quality-default", 5),
            (TAG_ENUM, "print-quality-supported", [4, 5]),
            (TAG_KEYWORD, "pwg-raster-document-type-supported", ["black_1", "sgray_8"]),
            (TAG_KEYWORD, "pwg-raster-document-sheet-back", "normal"),
            (TAG_KEYWORD, "ipp-features-supported", "ipp-everywhere"),
            (TAG_KEYWORD, "media-default", size_name),
            (TAG_KEYWORD, "media-supported", size_name),
            (TAG_KEYWORD, "media-ready", size_name),
            (TAG_BEG_COLLECTION, "media-col-default", media_col),
            (TAG_BEG_COLLECTION, "media-col-ready", media_col),
            (TAG_BEG_COLLECTION, "media-col-database", media_col),
            (TAG_KEYWORD, "media-col-supported", [
                "media-size", "media-top-margin", "media-bottom-margin",
                "media-left-margin", "media-right-margin", "media-source", "media-type",
            ]),
            (TAG_INTEGER, "media-bottom-margin-supported", 0),
            (TAG_INTEGER, "media-top-margin-supported", 0),
            (TAG_INTEGER, "media-left-margin-supported", 0),
            (TAG_INTEGER, "media-right-margin-supported", 0),
            (TAG_RANGE, "media-size-supported", None),  # replaced below
        ]
        # media-size-supported as collection
        printer_attrs = [a for a in printer_attrs if a[1] != "media-size-supported"]
        printer_attrs.append(
            (
                TAG_BEG_COLLECTION,
                "media-size-supported",
                [
                    {"x-dimension": (TAG_INTEGER, x), "y-dimension": (TAG_INTEGER, y)},
                    {
                        "x-dimension": (TAG_RANGE, (1200, 4800)),
                        "y-dimension": (TAG_RANGE, (1000, 30000)),
                    },
                ],
            )
        )
        requested = req.attributes.get("requested-attributes")
        if requested:
            wanted = set(requested)
            if "all" not in wanted and "printer-description" not in wanted:
                printer_attrs = [
                    a for a in printer_attrs
                    if a[1] in wanted
                    or a[1].startswith("media")
                    or a[1].startswith("job-")
                    or a[1].startswith("document-")
                    or a[1].startswith("pwg-")
                    or a[1].startswith("printer-resolution")
                    or a[1].startswith("print-quality")
                ]
        return build_ipp(
            req.version,
            STATUS_OK,
            req.request_id,
            [(TAG_OPERATION, self._op_group()), (TAG_PRINTER, printer_attrs)],
        )

    def _new_job(self, req: IppRequest, document: bytes) -> Job:
        with self.model.lock:
            job_id = self.model.next_job_id
            self.model.next_job_id += 1
            job = Job(
                job_id=job_id,
                document=document,
                document_format=str(first(req, "document-format", "application/octet-stream")),
                name=str(first(req, "job-name", f"job-{job_id}")),
            )
            self.model.jobs[job_id] = job
            return job

    def _job_attrs(self, job: Job) -> list[tuple[int, str, Any]]:
        return [
            (TAG_URI, "job-uri", f"{self.model.uri}/jobs/{job.job_id}"),
            (TAG_INTEGER, "job-id", job.job_id),
            (TAG_ENUM, "job-state", job.state),
            (TAG_KEYWORD, "job-state-reasons", "none"),
            (TAG_NAME, "job-name", job.name),
            (TAG_URI, "job-printer-uri", self.model.uri),
        ]

    def _print_job(self, req: IppRequest, *, validate: bool) -> bytes:
        if validate:
            return self._ok(req)
        job = self._new_job(req, req.document)
        try:
            self._run_job(job)
        except Exception:
            return self._status(req, STATUS_SERVER_ERROR, "server-error-job-failed")
        return self._ok(req, [(TAG_JOB, self._job_attrs(job))])

    def _create_job(self, req: IppRequest) -> bytes:
        job = self._new_job(req, b"")
        return self._ok(req, [(TAG_JOB, self._job_attrs(job))])

    def _send_document(self, req: IppRequest) -> bytes:
        job_id = int(first(req, "job-id", 0))
        job = self.model.jobs.get(job_id)
        if not job:
            return self._status(req, STATUS_NOT_FOUND, "client-error-not-found")
        job.document = req.document
        job.document_format = str(first(req, "document-format", job.document_format))
        last = first(req, "last-document", True)
        if last:
            try:
                self._run_job(job)
            except Exception:
                return self._status(req, STATUS_SERVER_ERROR, "server-error-job-failed")
        return self._ok(req, [(TAG_JOB, self._job_attrs(job))])

    def _get_jobs(self, req: IppRequest) -> bytes:
        groups: list[tuple[int, list[tuple[int, str, Any]]]] = [(TAG_OPERATION, self._op_group())]
        with self.model.lock:
            jobs = list(self.model.jobs.values())[-20:]
        for job in jobs:
            groups.append((TAG_JOB, self._job_attrs(job)))
        return build_ipp(req.version, STATUS_OK, req.request_id, groups)

    def _get_job_attrs(self, req: IppRequest) -> bytes:
        job_id = int(first(req, "job-id", 0))
        job = self.model.jobs.get(job_id)
        if not job:
            return self._status(req, STATUS_NOT_FOUND, "client-error-not-found")
        return self._ok(req, [(TAG_JOB, self._job_attrs(job))])

    def _cancel_job(self, req: IppRequest) -> bytes:
        job_id = int(first(req, "job-id", 0))
        job = self.model.jobs.get(job_id)
        if not job:
            return self._status(req, STATUS_NOT_FOUND, "client-error-not-found")
        job.state = JOB_CANCELED
        return self._ok(req, [(TAG_JOB, self._job_attrs(job))])

    def _run_job(self, job: Job) -> None:
        job.state = JOB_PROCESSING
        self.model.state = PRINTER_PROCESSING
        try:
            self.on_print(job, self.model)
            job.state = JOB_COMPLETED
        except Exception:
            log.exception("print job %s failed", job.job_id)
            job.state = JOB_ABORTED
            raise
        finally:
            self.model.state = PRINTER_IDLE
