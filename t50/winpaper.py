"""Create one T50 Print Server form and expose it as the queue paper size.

Setup adds a user form named like "T50 40x30 mm", writes that name into the
per-queue GPD, and drops A4/Letter from the GPD. It does not patch pdc.xml
(apply_label_paper) — that emptied DeviceCapabilities.
"""

from __future__ import annotations

import ctypes
import logging
import re
import time
import winreg
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("t50.winpaper")

PRINTER_NAME = "T50 Label"

IPP_SCHEMA_NS = "http://schemas.microsoft.com/windows/2018/04/printing/printschemakeywords/Ipp"
# IPP class driver GPD: A4 210 mm → PrintableArea 3780000
MASTER_UNITS_PER_MM = 18000
DC_PAPERS = 2
DC_PAPERSIZE = 3
DC_PAPERNAMES = 16
DC_BINNAMES = 12

FORM_USER = 0x0000
FORM_BUILTIN = 0x0001
FORM_PRINTER = 0x0002

winspool = ctypes.WinDLL("winspool.drv")
DeviceCapabilitiesW = winspool.DeviceCapabilitiesW
DeviceCapabilitiesW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.WORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
DeviceCapabilitiesW.restype = ctypes.c_int
OpenPrinterW = winspool.OpenPrinterW
OpenPrinterW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
OpenPrinterW.restype = wintypes.BOOL
ClosePrinter = winspool.ClosePrinter
ClosePrinter.argtypes = [wintypes.HANDLE]
ClosePrinter.restype = wintypes.BOOL
EnumFormsW = winspool.EnumFormsW
EnumFormsW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
EnumFormsW.restype = wintypes.BOOL
AddFormW = winspool.AddFormW
AddFormW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
AddFormW.restype = wintypes.BOOL
SetFormW = winspool.SetFormW
SetFormW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
SetFormW.restype = wintypes.BOOL
GetFormW = winspool.GetFormW
GetFormW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
GetFormW.restype = wintypes.BOOL

PRINTER_ACCESS_ADMINISTER = 0x00000004
PRINTER_ACCESS_USE = 0x00000008
STANDARD_RIGHTS_REQUIRED = 0x000F0000
PRINTER_ALL_ACCESS = STANDARD_RIGHTS_REQUIRED | PRINTER_ACCESS_ADMINISTER | PRINTER_ACCESS_USE


class PRINTER_DEFAULTSW(ctypes.Structure):
    _fields_ = [
        ("pDatatype", wintypes.LPWSTR),
        ("pDevMode", ctypes.c_void_p),
        ("DesiredAccess", wintypes.DWORD),
    ]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class RECTL(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class FORM_INFO_1W(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("pName", wintypes.LPWSTR),
        ("Size", SIZE),
        ("ImageableArea", RECTL),
    ]


def option_keyword(width_mm: int, height_mm: int) -> str:
    return f"T50_{int(width_mm)}x{int(height_mm)}"


def display_name(width_mm: int, height_mm: int) -> str:
    return f"{int(width_mm)} x {int(height_mm)} mm"


def label_form_name(width_mm: int, height_mm: int) -> str:
    return f"T50 {int(width_mm)}x{int(height_mm)} mm"


def _microns(mm: int) -> int:
    return int(mm) * 1000


def _mu(mm: int) -> int:
    return int(mm) * MASTER_UNITS_PER_MM


def patch_gpd_customsize(
    text: str,
    *,
    min_width_mm: int = 10,
    min_height_mm: int = 10,
    max_width_mm: int = 48,
    max_height_mm: int = 300,
) -> str:
    """Insert CUSTOMSIZE into the IPP class driver GPD PaperSize feature."""
    if "*Option: CUSTOMSIZE" in text:
        return text
    min_w, min_h = _mu(min_width_mm), _mu(min_height_mm)
    max_w, max_h = _mu(max_width_mm), _mu(max_height_mm)
    custom_block = (
        "*Option: CUSTOMSIZE\n"
        "{\n"
        "*rcNameID: =USER_DEFINED_SIZE_DISPLAY\n"
        f"*MinSize: PAIR({min_w}, {min_h})\n"
        f"*MaxSize: PAIR({max_w}, {max_h})\n"
        f"*MaxPrintableWidth: {max_w}\n"
        "*TopMargin: 0\n"
        "*BottomMargin: 0\n"
        "*MinLeftMargin: 0\n"
        "*CenterPrintable?: TRUE\n"
        "}\n"
    )
    start = text.find("*Feature: PaperSize")
    if start < 0:
        raise RuntimeError("GPD has no PaperSize feature to patch")
    next_feat = text.find("*Feature:", start + 1)
    if next_feat < 0:
        raise RuntimeError("GPD PaperSize feature has no following feature")
    insert_at = text.rfind("}", start, next_feat)
    if insert_at < 0:
        raise RuntimeError("GPD PaperSize feature has no closing brace")
    return text[:insert_at] + custom_block + text[insert_at:]


def form_option_keyword(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    if not s:
        s = "Custom"
    if s[0].isdigit():
        s = "F_" + s
    return ("FORM_" + s)[:63]


def _gpd_escape(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _remove_option_at(text: str, start: int) -> str:
    brace = text.find("{", start)
    if brace < 0:
        return text
    depth = 0
    i = brace
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                return text[:start] + text[end:]
        i += 1
    return text


def _remove_gpd_options(text: str, names: list[str] | None = None, prefix: str | None = None) -> str:
    if names:
        for name in names:
            needle = f"*Option: {name}"
            while True:
                start = text.find(needle)
                if start < 0:
                    break
                text = _remove_option_at(text, start)
    if prefix:
        pat = re.compile(rf"\*Option:\s*{re.escape(prefix)}[A-Za-z0-9_]*")
        while True:
            m = pat.search(text)
            if not m:
                break
            text = _remove_option_at(text, m.start())
    return text


def _named_form_option(keyword: str, name: str, width_mm: int, height_mm: int) -> str:
    w_mu, h_mu = _mu(width_mm), _mu(height_mm)
    shown = _gpd_escape(name)
    return (
        f"*Option: {keyword}\n"
        "{\n"
        f'*Name: "{shown}"\n'
        f"*PageDimensions: PAIR({w_mu}, {h_mu})\n"
        "*switch: PageBorderless\n"
        "{\n"
        "*case: Borderless\n"
        "{\n"
        "*PrintableOrigin: PAIR(0, 0)\n"
        f"*PrintableArea: PAIR({w_mu}, {h_mu})\n"
        "}\n"
        "*case: None\n"
        "{\n"
        "*PrintableOrigin: PAIR(0, 0)\n"
        f"*PrintableArea: PAIR({w_mu}, {h_mu})\n"
        "}\n"
        "}\n"
        "}\n"
    )


def patch_gpd_forms(
    text: str,
    forms: list[tuple[str, int, int]],
    *,
    drop_office_sizes: bool = True,
) -> str:
    """Insert named PaperSize options for Print Server Property forms. GPD only."""
    drop = ["CUSTOMSIZE"]
    if drop_office_sizes:
        drop.extend(["A4", "LETTER"])
    text = _remove_gpd_options(text, names=drop, prefix="FORM_")
    if not forms:
        return text
    used: set[str] = set()
    blocks: list[str] = []
    default = ""
    for name, width_mm, height_mm in forms:
        kw = form_option_keyword(name)
        base = kw
        n = 2
        while kw in used:
            kw = f"{base}_{n}"[:63]
            n += 1
        used.add(kw)
        blocks.append(_named_form_option(kw, name, width_mm, height_mm))
        if not default:
            default = kw
    start = text.find("*Feature: PaperSize")
    if start < 0:
        raise RuntimeError("GPD has no PaperSize feature to patch")
    next_feat = text.find("*Feature:", start + 1)
    if next_feat < 0:
        raise RuntimeError("GPD PaperSize feature has no following feature")
    feature = text[start:next_feat]
    if default:
        feature = re.sub(
            r"\*DefaultOption:\s*\S+",
            f"*DefaultOption: {default}",
            feature,
            count=1,
        )
    insert_at = feature.rfind("}")
    if insert_at < 0:
        raise RuntimeError("GPD PaperSize feature has no closing brace")
    feature = feature[:insert_at] + "".join(blocks) + feature[insert_at:]
    return text[:start] + feature + text[next_feat:]


def list_printer_forms(printer_name: str = PRINTER_NAME) -> list[tuple[str, int, int, int]]:
    handle = wintypes.HANDLE()
    if not OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise RuntimeError(f"OpenPrinter failed for {printer_name}")
    try:
        needed = wintypes.DWORD()
        returned = wintypes.DWORD()
        EnumFormsW(handle, 1, None, 0, ctypes.byref(needed), ctypes.byref(returned))
        if needed.value <= 0:
            return []
        buf = ctypes.create_string_buffer(needed.value)
        if not EnumFormsW(handle, 1, buf, needed.value, ctypes.byref(needed), ctypes.byref(returned)):
            raise RuntimeError("EnumForms failed")
        arr = ctypes.cast(buf, ctypes.POINTER(FORM_INFO_1W))
        out: list[tuple[str, int, int, int]] = []
        for i in range(returned.value):
            form = arr[i]
            name = form.pName or ""
            if not name:
                continue
            width_mm = max(1, round(form.Size.cx / 1000))
            height_mm = max(1, round(form.Size.cy / 1000))
            out.append((name, width_mm, height_mm, int(form.Flags)))
        return out
    finally:
        ClosePrinter(handle)


def label_forms(
    printer_name: str = PRINTER_NAME,
    *,
    min_width_mm: int = 10,
    min_height_mm: int = 10,
    max_width_mm: int = 48,
    max_height_mm: int = 300,
) -> list[tuple[str, int, int]]:
    """User and printer forms that fit the T50 head / tape range."""
    picked: list[tuple[str, int, int]] = []
    for name, width_mm, height_mm, flags in list_printer_forms(printer_name):
        if flags not in (FORM_USER, FORM_PRINTER):
            continue
        if not (min_width_mm <= width_mm <= max_width_mm):
            continue
        if not (min_height_mm <= height_mm <= max_height_mm):
            continue
        picked.append((name, width_mm, height_mm))
    picked.sort(key=lambda item: (item[1] != 40 or item[2] != 30, item[0].lower()))
    return picked


_A4_GPD_OPTIONS = """*Option: A4
{
*rcNameID: =RCID_DMPAPER_SYSTEM_NAME
*PrintSchemaKeywordMap: "ISOA4"
*switch: PageBorderless
{
*case: Borderless
{
*PrintableOrigin: PAIR(0, 0)
*PrintableArea: PAIR(3780000, 5346000)
}
*case: None
{
*PrintableOrigin: PAIR(0, 0)
*PrintableArea: PAIR(3780000, 5346000)
}
}
}
*Option: LETTER
{
*rcNameID: =RCID_DMPAPER_SYSTEM_NAME
*PrintSchemaKeywordMap: "NorthAmericaLetter"
*switch: PageBorderless
{
*case: Borderless
{
*PrintableOrigin: PAIR(0, 0)
*PrintableArea: PAIR(3886200, 5029200)
}
*case: None
{
*PrintableOrigin: PAIR(0, 0)
*PrintableArea: PAIR(3886200, 5029200)
}
}
}
"""


def _label_pdc_option(width_mm: int, height_mm: int) -> str:
    kw = option_keyword(width_mm, height_mm)
    name = display_name(width_mm, height_mm)
    w_um, h_um = _microns(width_mm), _microns(height_mm)
    return (
        f"        <ns0000:{kw} psf2:psftype=\"Option\" psf2:default=\"true\">\n"
        f"            <psf:DisplayName xsi:type=\"xsd:string\" psf2:psftype=\"Property\">{name}</psf:DisplayName>\n"
        f"            <psk:MediaSizeWidth xsi:type=\"xsd:integer\" psf2:psftype=\"ScoredProperty\">{w_um}</psk:MediaSizeWidth>\n"
        f"            <psk:MediaSizeHeight xsi:type=\"xsd:integer\" psf2:psftype=\"ScoredProperty\">{h_um}</psk:MediaSizeHeight>\n"
        f"            <psk12:PortraitImageableSize xsi:type=\"psf2:ImageableAreaType\" psf2:psftype=\"Property\">0,0,{w_um},{h_um}</psk12:PortraitImageableSize>\n"
        f"            <psk12:BorderlessImageableSize xsi:type=\"psf2:ImageableAreaType\" psf2:psftype=\"Property\">0,0,{w_um},{h_um}</psk12:BorderlessImageableSize>\n"
        f"        </ns0000:{kw}>\n"
    )


def patch_pdc_text(text: str, width_mm: int, height_mm: int) -> str:
    inner = re.search(
        r"(<psk:PageMediaSize psf2:psftype=\"Feature\">)(.*?)(</psk:PageMediaSize>)",
        text,
        flags=re.S,
    )
    if not inner:
        raise RuntimeError("pdc.xml has no PageMediaSize feature to patch")
    body = re.sub(r"\s*<ns0000:T50_\d+x\d+[\s\S]*?</ns0000:T50_\d+x\d+>", "", inner.group(2))
    body = body.replace('psf2:default="true"', 'psf2:default="false"')
    if "<psk:ISOA4" not in body:
        body += (
            "        <psk:ISOA4 psf2:psftype=\"Option\" psf2:default=\"false\">\n"
            "            <psk:MediaSizeWidth xsi:type=\"xsd:integer\" psf2:psftype=\"ScoredProperty\">210000</psk:MediaSizeWidth>\n"
            "            <psk:MediaSizeHeight xsi:type=\"xsd:integer\" psf2:psftype=\"ScoredProperty\">297000</psk:MediaSizeHeight>\n"
            "            <psk12:PortraitImageableSize xsi:type=\"psf2:ImageableAreaType\" psf2:psftype=\"Property\">0,0,210000,297000</psk12:PortraitImageableSize>\n"
            "            <psk12:BorderlessImageableSize xsi:type=\"psf2:ImageableAreaType\" psf2:psftype=\"Property\">0,0,210000,297000</psk12:BorderlessImageableSize>\n"
            "        </psk:ISOA4>\n"
            "        <psk:NorthAmericaLetter psf2:psftype=\"Option\" psf2:default=\"false\">\n"
            "            <psk:MediaSizeWidth xsi:type=\"xsd:integer\" psf2:psftype=\"ScoredProperty\">215900</psk:MediaSizeWidth>\n"
            "            <psk:MediaSizeHeight xsi:type=\"xsd:integer\" psf2:psftype=\"ScoredProperty\">279400</psk:MediaSizeHeight>\n"
            "            <psk12:PortraitImageableSize xsi:type=\"psf2:ImageableAreaType\" psf2:psftype=\"Property\">0,0,215900,279400</psk12:PortraitImageableSize>\n"
            "            <psk12:BorderlessImageableSize xsi:type=\"psf2:ImageableAreaType\" psf2:psftype=\"Property\">0,0,215900,279400</psk12:BorderlessImageableSize>\n"
            "        </psk:NorthAmericaLetter>\n"
        )
    patched = (
        text[: inner.start()]
        + inner.group(1)
        + "\n"
        + _label_pdc_option(width_mm, height_mm)
        + body.lstrip("\n")
        + inner.group(3)
        + text[inner.end() :]
    )
    stamp = str(int(time.time()) & 0xFFFFFFFF)
    patched = re.sub(
        r"(<psf2:CapabilitiesChangeID[^>]*>)[^<]+",
        rf"\g<1>{stamp}",
        patched,
        count=1,
    )
    return patched


def patch_gpd_text(text: str, width_mm: int, height_mm: int) -> str:
    kw = option_keyword(width_mm, height_mm)
    name = display_name(width_mm, height_mm)
    w_mu, h_mu = _mu(width_mm), _mu(height_mm)
    label_opt = (
        f"*Option: {kw}\n"
        "{\n"
        f"*Name: \"{name}\"\n"
        f"*PrintSchemaKeywordMap: \"{kw}\"\n"
        f"*PrintSchemaNamespace: \"{IPP_SCHEMA_NS}\"\n"
        f"*PageDimensions: PAIR({w_mu}, {h_mu})\n"
        "*switch: PageBorderless\n"
        "{\n"
        "*case: Borderless\n"
        "{\n"
        "*PrintableOrigin: PAIR(0, 0)\n"
        f"*PrintableArea: PAIR({w_mu}, {h_mu})\n"
        "}\n"
        "*case: None\n"
        "{\n"
        "*PrintableOrigin: PAIR(0, 0)\n"
        f"*PrintableArea: PAIR({w_mu}, {h_mu})\n"
        "}\n"
        "}\n"
        "}\n"
    )
    start = text.find("*Feature: PaperSize")
    if start < 0:
        raise RuntimeError("GPD has no PaperSize feature to patch")
    next_feat = text.find("*Feature:", start + 1)
    if next_feat < 0:
        raise RuntimeError("GPD PaperSize feature has no following feature")
    feature = (
        "*Feature: PaperSize\n"
        "{\n"
        "*rcNameID: =PAPER_SIZE_DISPLAY\n"
        "*PrintSchemaKeywordMap: \"PageMediaSize\"\n"
        f"*DefaultOption: {kw}\n"
        f"{label_opt}"
        f"{_A4_GPD_OPTIONS}"
        "}\n"
    )
    return text[:start] + feature + text[next_feat:]


def v4_driver_dir(printer_name: str = PRINTER_NAME) -> Path:
    key = rf"SYSTEM\CurrentControlSet\Control\Print\Printers\{printer_name}"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
        guid, _ = winreg.QueryValueEx(k, "PrintQueueV4DriverDirectory")
    path = Path(r"C:\Windows\System32\spool\V4Dirs") / str(guid)
    if not path.is_dir():
        raise RuntimeError(f"V4 driver directory missing: {path}")
    return path


def _stop_spooler() -> None:
    from t50.winsetup import run_hidden

    run_hidden(["net", "stop", "spooler"], timeout=60)
    time.sleep(0.4)


def _start_spooler() -> None:
    from t50.winsetup import run_hidden

    r = run_hidden(["net", "start", "spooler"], timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "could not start spooler").strip())


def _clear_user_devmode(printer_name: str) -> None:
    for sub in ("DevModes2", "DevModePerUser"):
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                rf"Printers\{sub}",
                0,
                winreg.KEY_SET_VALUE,
            ) as k:
                winreg.DeleteValue(k, printer_name)
                log.info("cleared HKCU\\Printers\\%s\\%s", sub, printer_name)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("could not clear %s: %s", sub, exc)


def tape_mm_from_ipp(ipp_http_url: str) -> tuple[int, int] | None:
    import urllib.request

    from t50.ipp import (
        OP_GET_PRINTER_ATTRIBUTES,
        TAG_CHARSET,
        TAG_KEYWORD,
        TAG_LANGUAGE,
        TAG_URI,
        build_ipp,
        first,
        parse_ipp,
    )

    body = build_ipp(
        (2, 0),
        OP_GET_PRINTER_ATTRIBUTES,
        1,
        [
            (
                0x01,
                [
                    (TAG_CHARSET, "attributes-charset", "utf-8"),
                    (TAG_LANGUAGE, "attributes-natural-language", "en"),
                    (TAG_URI, "printer-uri", ipp_http_url.replace("http://", "ipp://")),
                    (TAG_KEYWORD, "requested-attributes", ["media-default", "media-col-default"]),
                ],
            )
        ],
    )
    req = urllib.request.Request(
        ipp_http_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/ipp"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            parsed = parse_ipp(resp.read())
    except Exception:
        log.debug("IPP media probe failed", exc_info=True)
        return None
    media = str(first(parsed, "media-default") or "")
    m = re.search(r"(\d+)x(\d+)", media)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def printer_paper_names(printer_name: str = PRINTER_NAME) -> list[str]:
    n = DeviceCapabilitiesW(printer_name, None, DC_PAPERNAMES, None, None)
    if n <= 0:
        return []
    buf = ctypes.create_unicode_buffer(n * 64)
    got = DeviceCapabilitiesW(printer_name, None, DC_PAPERNAMES, ctypes.byref(buf), None)
    if got <= 0:
        return []
    raw = ctypes.wstring_at(ctypes.addressof(buf), got * 64)
    names = []
    for i in range(got):
        chunk = raw[i * 64 : (i + 1) * 64]
        names.append(chunk.split("\x00", 1)[0])
    return names


def printer_paper_sizes_mm(printer_name: str = PRINTER_NAME) -> list[tuple[int, int]]:
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    n = DeviceCapabilitiesW(printer_name, None, DC_PAPERSIZE, None, None)
    if n <= 0:
        return []
    arr = (POINT * n)()
    got = DeviceCapabilitiesW(printer_name, None, DC_PAPERSIZE, ctypes.byref(arr), None)
    if got <= 0:
        return []
    # POINT values are tenths of a millimetre
    return [(max(1, round(arr[i].x / 10)), max(1, round(arr[i].y / 10))) for i in range(got)]


def _set_print_ticket(printer_name: str, width_mm: int, height_mm: int) -> None:
    from t50.winsetup import data_dir, run_hidden

    kw = option_keyword(width_mm, height_mm)
    w_um, h_um = _microns(width_mm), _microns(height_mm)
    ticket = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<psf:PrintTicket xmlns:psf="http://schemas.microsoft.com/windows/2003/08/printing/printschemaframework" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:psk="http://schemas.microsoft.com/windows/2003/08/printing/printschemakeywords" '
        f'xmlns:ns0000="{IPP_SCHEMA_NS}" version="1">'
        '<psf:Feature name="psk:PageMediaSize">'
        f'<psf:Option name="ns0000:{kw}">'
        '<psf:ScoredProperty name="psk:MediaSizeWidth">'
        f'<psf:Value xsi:type="xsd:integer">{w_um}</psf:Value>'
        "</psf:ScoredProperty>"
        '<psf:ScoredProperty name="psk:MediaSizeHeight">'
        f'<psf:Value xsi:type="xsd:integer">{h_um}</psf:Value>'
        "</psf:ScoredProperty>"
        "</psf:Option>"
        "</psf:Feature>"
        "</psf:PrintTicket>"
    )
    path = data_dir() / "t50-printticket.xml"
    path.write_text(ticket, encoding="utf-8")
    r = run_hidden(
        [
            "powershell",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"Set-PrintConfiguration -PrinterName '{printer_name}' -PrintTicketXml (Get-Content -Raw -Encoding UTF8 '{path}')",
        ],
        timeout=30,
    )
    if r.returncode != 0:
        log.warning("Set-PrintConfiguration: %s", (r.stderr or r.stdout or "").strip())
    else:
        log.info("default PrintTicket set to %s", kw)


def resolve_label_mm() -> tuple[int, int]:
    from t50 import DEFAULT_LABEL_HEIGHT_MM, PRINTHEAD_WIDTH_MM

    try:
        from t50.printer import Printer

        with Printer.open() as printer:
            mat = printer.query_material()
            width = min(int(mat.width_mm or PRINTHEAD_WIDTH_MM), PRINTHEAD_WIDTH_MM)
            height = max(int(mat.height_mm or DEFAULT_LABEL_HEIGHT_MM), 10)
            log.info("tape from HID: %sx%s mm", width, height)
            return width, height
    except Exception:
        log.debug("HID tape probe failed", exc_info=True)
    from t50.winsetup import IPP_URL

    probed = tape_mm_from_ipp(IPP_URL)
    if probed:
        width = min(int(probed[0]), PRINTHEAD_WIDTH_MM)
        height = max(int(probed[1]), 10)
        log.info("tape from IPP: %sx%s mm", width, height)
        return width, height
    return 40, DEFAULT_LABEL_HEIGHT_MM


def _form_info(name: str, width_mm: int, height_mm: int) -> FORM_INFO_1W:
    w_um, h_um = _microns(width_mm), _microns(height_mm)
    info = FORM_INFO_1W()
    info.Flags = FORM_USER
    info.pName = name
    info.Size.cx = w_um
    info.Size.cy = h_um
    info.ImageableArea.left = 0
    info.ImageableArea.top = 0
    info.ImageableArea.right = w_um
    info.ImageableArea.bottom = h_um
    return info


def _open_printer_admin(printer_name: str) -> wintypes.HANDLE:
    handle = wintypes.HANDLE()
    defaults = PRINTER_DEFAULTSW(None, None, PRINTER_ALL_ACCESS)
    if not OpenPrinterW(printer_name, ctypes.byref(handle), ctypes.byref(defaults)):
        raise RuntimeError(f"OpenPrinter(admin) failed for {printer_name} ({ctypes.GetLastError()})")
    return handle


def ensure_label_form(printer_name: str, name: str, width_mm: int, height_mm: int) -> None:
    """Create or update a Print Server Property user form."""
    info = _form_info(name, width_mm, height_mm)
    handle = _open_printer_admin(printer_name)
    try:
        needed = wintypes.DWORD()
        GetFormW(handle, name, 1, None, 0, ctypes.byref(needed))
        err = ctypes.GetLastError()
        exists = needed.value > 0 or err == 122  # ERROR_INSUFFICIENT_BUFFER
        if exists:
            if not SetFormW(handle, name, 1, ctypes.byref(info)):
                raise RuntimeError(f"SetForm({name!r}) failed ({ctypes.GetLastError()})")
            log.info("updated form %s %sx%s mm", name, width_mm, height_mm)
            return
        if not AddFormW(handle, 1, ctypes.byref(info)):
            raise RuntimeError(f"AddForm({name!r}) failed ({ctypes.GetLastError()})")
        log.info("created form %s %sx%s mm", name, width_mm, height_mm)
    finally:
        ClosePrinter(handle)


def apply_custom_paper_support(
    printer_name: str = PRINTER_NAME,
    *,
    min_width_mm: int = 10,
    min_height_mm: int = 10,
    max_width_mm: int = 48,
    max_height_mm: int = 300,
) -> list[str]:
    """Create one T50 form and make it the only paper size on the IPP queue."""
    del min_width_mm, min_height_mm, max_width_mm, max_height_mm
    v4 = v4_driver_dir(printer_name)
    gpd = next(v4.glob("*.gpd"), None)
    if gpd is None:
        raise RuntimeError(f"no GPD in {v4}")

    width_mm, height_mm = resolve_label_mm()
    form_name = label_form_name(width_mm, height_mm)
    ensure_label_form(printer_name, form_name, width_mm, height_mm)
    forms = [(form_name, width_mm, height_mm)]
    log.info("label form for GPD: %s", forms)
    _stop_spooler()
    try:
        original = gpd.read_text(encoding="utf-8", errors="replace")
        gpd.write_text(patch_gpd_forms(original, forms, drop_office_sizes=True), encoding="utf-8")
        for bud in v4.glob("*.BUD"):
            try:
                bud.unlink()
            except OSError as exc:
                log.warning("could not delete %s: %s", bud, exc)
    finally:
        _start_spooler()
        time.sleep(1.0)

    _clear_user_devmode(printer_name)
    names: list[str] = []
    for _ in range(8):
        names = printer_paper_names(printer_name)
        if form_name in names:
            break
        time.sleep(0.5)
    log.info("paper names after form patch: %s", names)
    if form_name not in names:
        log.warning("expected %s in paper list; got %s", form_name, names)
    return names


def apply_label_paper(
    printer_name: str,
    width_mm: int,
    height_mm: int,
) -> list[str]:
    width_mm = int(width_mm)
    height_mm = int(height_mm)
    v4 = v4_driver_dir(printer_name)
    gpd = next(v4.glob("*.gpd"), None)
    if gpd is None:
        raise RuntimeError(f"no GPD in {v4}")
    pdc_files = [p for p in (v4 / "pdc.xml", v4 / "device_bidi_pdc.xml") if p.is_file()]
    if not pdc_files:
        raise RuntimeError(f"no pdc.xml in {v4}")

    log.info("patching %s to %sx%s mm", v4, width_mm, height_mm)
    _stop_spooler()
    try:
        gpd.write_text(patch_gpd_text(gpd.read_text(encoding="utf-8", errors="replace"), width_mm, height_mm), encoding="utf-8")
        for pdc in pdc_files:
            pdc.write_text(patch_pdc_text(pdc.read_text(encoding="utf-8", errors="replace"), width_mm, height_mm), encoding="utf-8")
        for bud in v4.glob("*.BUD"):
            try:
                bud.unlink()
            except OSError as exc:
                log.warning("could not delete %s: %s", bud, exc)
    finally:
        _start_spooler()
        time.sleep(1.5)

    _clear_user_devmode(printer_name)
    _set_print_ticket(printer_name, width_mm, height_mm)
    names: list[str] = []
    sizes: list[tuple[int, int]] = []
    wanted = display_name(width_mm, height_mm)
    for _ in range(8):
        names = printer_paper_names(printer_name)
        sizes = printer_paper_sizes_mm(printer_name)
        if names or sizes:
            break
        time.sleep(0.5)
    log.info("paper names after patch: %s sizes_mm=%s", names, sizes)
    if wanted not in names and not any(abs(w - width_mm) <= 1 and abs(h - height_mm) <= 1 for w, h in sizes):
        if not names and not sizes:
            raise RuntimeError(
                f"printer capabilities unavailable after paper patch (names={names!r} sizes={sizes!r})"
            )
        log.warning("custom %s not listed yet; names=%s sizes=%s", wanted, names, sizes)
    return names
