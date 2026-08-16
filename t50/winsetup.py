"""Windows printer queue, vendor-driver, and startup helpers."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import winreg
from ctypes import wintypes
from pathlib import Path

from t50.serve import DEFAULT_PORT, PRINTER_NAME

log = logging.getLogger("t50.winsetup")

VENDOR_PRINTER = "Supvan_T50_Printer"
VENDOR_SERVICE = "Supvan_T50_Service"
RUN_VALUE = "T50Label"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
IPP_URL = f"http://127.0.0.1:{DEFAULT_PORT}/ipp/print"

CREATE_NO_WINDOW = 0x08000000
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
INFINITE = 0xFFFFFFFF
ERROR_CANCELLED = 1223

winspool = ctypes.WinDLL("winspool.drv")
OpenPrinterW = winspool.OpenPrinterW
OpenPrinterW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
OpenPrinterW.restype = wintypes.BOOL
ClosePrinter = winspool.ClosePrinter
ClosePrinter.argtypes = [wintypes.HANDLE]
ClosePrinter.restype = wintypes.BOOL


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", wintypes.LPVOID),
        ("lpClass", wintypes.LPCWSTR),
        ("hKeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "T50Label"
    root.mkdir(parents=True, exist_ok=True)
    return root


def log_path() -> Path:
    return data_dir() / "t50.log"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_hidden(args: list[str], timeout: int | None = 60) -> subprocess.CompletedProcess[str]:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        startupinfo=startupinfo,
        creationflags=CREATE_NO_WINDOW,
    )


def _ps(command: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return run_hidden(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", command],
        timeout=timeout,
    )


def printer_installed(name: str = PRINTER_NAME) -> bool:
    handle = wintypes.HANDLE()
    if OpenPrinterW(name, ctypes.byref(handle), None):
        ClosePrinter(handle)
        return True
    return False


def add_ipp_printer() -> None:
    if printer_installed():
        log.info("removing existing %s", PRINTER_NAME)
        _ps(f"Remove-Printer -Name '{PRINTER_NAME}'")
    r = _ps(f"Add-Printer -Name '{PRINTER_NAME}' -IppURL '{IPP_URL}'")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "Add-Printer failed").strip())
    log.info("Add-Printer ok: %s", (r.stdout or "").strip())
    try:
        from t50.winpaper import apply_custom_paper_support

        names = apply_custom_paper_support()
        log.info("custom paper enabled (%s sizes)", len(names))
    except Exception:
        log.exception("custom paper patch failed; only A4/Letter will appear")


def remove_ipp_printer() -> None:
    _ps(
        f"if (Get-Printer -Name '{PRINTER_NAME}' -ErrorAction SilentlyContinue) {{ "
        f"Remove-Printer -Name '{PRINTER_NAME}' }}"
    )


def stop_vendor_stack() -> None:
    run_hidden(["sc", "stop", VENDOR_SERVICE], timeout=30)
    _ps(
        f"Set-Service -Name '{VENDOR_SERVICE}' -StartupType Manual -ErrorAction SilentlyContinue; "
        f"$p = Get-CimInstance Win32_Printer -Filter \"Name='{VENDOR_PRINTER}'\" -ErrorAction SilentlyContinue; "
        f"if ($p) {{ $p | Set-CimInstance -Property @{{ WorkOffline = $true }} }}; "
        f"if (Get-Printer -Name '{VENDOR_PRINTER}' -ErrorAction SilentlyContinue) {{ "
        f"Set-Printer -Name '{VENDOR_PRINTER}' -Comment 'Disabled vendor driver — use {PRINTER_NAME}' }}"
    )


def restore_vendor_stack() -> None:
    _ps(
        f"$p = Get-CimInstance Win32_Printer -Filter \"Name='{VENDOR_PRINTER}'\" -ErrorAction SilentlyContinue; "
        f"if ($p) {{ $p | Set-CimInstance -Property @{{ WorkOffline = $false }} }}; "
        f"if (Get-Printer -Name '{VENDOR_PRINTER}' -ErrorAction SilentlyContinue) {{ "
        f"Set-Printer -Name '{VENDOR_PRINTER}' -Comment '' }}; "
        f"Set-Service -Name '{VENDOR_SERVICE}' -StartupType Automatic -ErrorAction SilentlyContinue; "
        f"Start-Service -Name '{VENDOR_SERVICE}' -ErrorAction SilentlyContinue"
    )


def startup_command() -> str:
    if is_frozen():
        return f'"{Path(sys.executable).resolve()}"'
    root = project_root()
    pyw = Path(sys.executable).with_name("pythonw.exe")
    py = pyw if pyw.exists() else Path(sys.executable)
    bat = data_dir() / "start.bat"
    bat.write_text(f'@echo off\r\ncd /d "{root}"\r\nstart "" "{py}" -m t50\r\n', encoding="utf-8")
    return str(bat)


def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_startup(enabled: bool) -> None:
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass


def elevate_and_wait(subcommand: str = "setup") -> int:
    """Re-launch this program as admin and wait. Shows UAC only, no console."""
    exe = str(Path(sys.executable).resolve())
    params = subcommand if is_frozen() else f"-m t50 {subcommand}"
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params
    info.lpDirectory = str(project_root())
    info.nShow = SW_HIDE
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.GetLastError()
        if err == ERROR_CANCELLED:
            return 1
        raise OSError(f"ShellExecuteEx failed ({err})")
    try:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 180000)
        code = wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(info.hProcess)


def message_box(text: str, title: str = "T50 Label", error: bool = False) -> None:
    flags = 0x10 if error else 0x40
    ctypes.windll.user32.MessageBoxW(None, text, title, flags)
