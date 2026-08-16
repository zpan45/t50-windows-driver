"""IPP printer application: receive Windows print jobs and send them to the T50."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from t50 import DEFAULT_DENSITY, DEFAULT_LABEL_HEIGHT_MM, PRINTHEAD_WIDTH_MM
from t50.ipp import IppPrinterApp, Job, PrinterModel
from t50.printer import Printer, PrinterError
from t50.raster import layout_on_tape, mm_to_dots, parse_pwg_images

log = logging.getLogger("t50.serve")

PRINTER_NAME = "T50 Label"
DEFAULT_PORT = 8631


CREATE_NO_WINDOW = 0x08000000


def stop_vendor_service() -> None:
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        subprocess.run(
            ["sc", "stop", "Supvan_T50_Service"],
            capture_output=True,
            text=True,
            check=False,
            startupinfo=startupinfo,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        log.warning("could not stop vendor service: %s", exc)


def _looks_like_pwg(data: bytes) -> bool:
    return data[:4] in (b"RaS2", b"RaSt", b"tSaR", b"Unir", b"rinU")


def _looks_like_image(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"


def _open_page_image(data: bytes):
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as img:
        return img.convert("L")


class T50Server:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, density: int = DEFAULT_DENSITY):
        self.host = host
        self.port = port
        self.density = density
        self.crop_whitespace = True
        self.print_lock = threading.Lock()
        self.device: Printer | None = None
        uri = f"ipp://{host}:{port}/ipp/print"
        self.model = PrinterModel(
            name=PRINTER_NAME,
            uri=uri,
            make_and_model="SUPVAN T50M Pro",
            width_mm=PRINTHEAD_WIDTH_MM,
            height_mm=DEFAULT_LABEL_HEIGHT_MM,
        )
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self.app = IppPrinterApp(self.model, self._on_print, host=host, port=port)

    def _refresh_media(self) -> None:
        if not self.device:
            return
        try:
            mat = self.device.query_material()
        except Exception:
            log.exception("material query failed")
            return
        width = mat.width_mm or PRINTHEAD_WIDTH_MM
        height = mat.height_mm or DEFAULT_LABEL_HEIGHT_MM
        width = min(int(width), PRINTHEAD_WIDTH_MM)
        height = max(int(height), 10)
        self.model.width_mm = width
        self.model.height_mm = height
        log.info("loaded tape: %s", mat)

    def _on_print(self, job: Job, model: PrinterModel) -> None:
        if not job.document:
            raise PrinterError("empty print job")
        fmt = (job.document_format or "").lower()
        try:
            from t50.winsetup import data_dir

            dump = data_dir() / "last-job.bin"
            dump.write_bytes(job.document or b"")
            log.info(
                "job %s fmt=%s magic=%s bytes=%s saved %s",
                job.job_id, fmt, (job.document or b"")[:8], len(job.document or b""), dump,
            )
        except OSError:
            log.exception("could not save last-job.bin")
        tape_w = mm_to_dots(model.width_mm)
        tape_h = mm_to_dots(model.height_mm)
        if "pwg-raster" in fmt or _looks_like_pwg(job.document):
            images = parse_pwg_images(job.document)
        elif "jpeg" in fmt or "png" in fmt or "jpg" in fmt or _looks_like_image(job.document):
            images = [_open_page_image(job.document)]
        else:
            if _looks_like_pwg(job.document):
                images = parse_pwg_images(job.document)
            elif _looks_like_image(job.document):
                images = [_open_page_image(job.document)]
            else:
                raise PrinterError(f"unsupported document-format: {job.document_format!r}")

        if not self.device:
            raise PrinterError("printer not open")
        with self.print_lock:
            for i, img in enumerate(images, 1):
                bits, width, height = layout_on_tape(
                    img, tape_w, tape_h, crop_whitespace=self.crop_whitespace
                )
                log.info("printing page %s/%s %sx%s", i, len(images), width, height)
                self.device.print_page(bits, width, height, density=self.density)

    def run(self, require_device: bool = True) -> None:
        stop_vendor_service()
        try:
            self.device = Printer.open()
            log.info("HID opened: %s", self.device.dev.info.path)
            if not self.device.check_device():
                raise PrinterError("printer did not answer CHECK_DEVICE")
            st = self.device.query_status()
            log.info("status: %s", st)
            self._refresh_media()
        except Exception:
            self.device = None
            if require_device:
                raise
            log.exception("printer not available; IPP will still listen")
        try:
            self.app.serve_forever(ready_event=self._ready)
        finally:
            if self.device:
                self.device.close()
                self.device = None

    def start_background(self, require_device: bool = True) -> None:
        def worker() -> None:
            try:
                self.run(require_device=require_device)
            except BaseException as exc:
                self._error = exc
                log.exception("server stopped")
                self._ready.set()

        self._thread = threading.Thread(target=worker, name="t50-ipp", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise PrinterError("IPP server failed to start")
        if self._error:
            raise PrinterError(str(self._error)) from self._error

    def wait_http(self, timeout: float = 10.0) -> bool:
        import urllib.request

        deadline = time.time() + timeout
        url = f"http://{self.host}:{self.port}/ipp/print"
        while time.time() < deadline:
            try:
                urllib.request.urlopen(url, timeout=1)
                return True
            except Exception:
                time.sleep(0.2)
        return False

    def shutdown(self) -> None:
        try:
            self.app.shutdown()
        except Exception:
            log.debug("httpd shutdown", exc_info=True)
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None
