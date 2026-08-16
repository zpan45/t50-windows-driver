"""Small always-on window that keeps the IPP server running."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from t50.hidwin import HidError
from t50.i18n import t
from t50.printer import PrinterError
from t50.serve import PRINTER_NAME, T50Server
from t50.winsetup import (
    crop_whitespace_enabled,
    elevate_and_wait,
    printer_installed,
    set_crop_whitespace,
    set_startup,
    startup_enabled,
)

log = logging.getLogger("t50.app")


class T50App:
    def __init__(self) -> None:
        self.server: T50Server | None = None
        self.root = tk.Tk()
        self.root.title(t("app_title"))
        self.root.geometry("440x340")
        self.root.resizable(False, False)
        self.status = tk.StringVar(value=t("starting"))
        self.tape = tk.StringVar(value="")
        self.startup_var = tk.BooleanVar(value=startup_enabled())
        self.crop_var = tk.BooleanVar(value=crop_whitespace_enabled())
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self._boot)

    def _build(self) -> None:
        pad = {"padx": 16, "pady": 6}
        ttk.Label(self.root, text=t("app_title"), font=("Segoe UI", 14, "bold")).pack(anchor="w", **pad)
        ttk.Label(self.root, textvariable=self.status, wraplength=388).pack(anchor="w", padx=16)
        ttk.Label(self.root, textvariable=self.tape, foreground="#555").pack(anchor="w", padx=16)
        ttk.Checkbutton(
            self.root,
            text=t("start_with_windows"),
            variable=self.startup_var,
            command=self._toggle_startup,
        ).pack(anchor="w", padx=16, pady=(10, 2))
        ttk.Checkbutton(
            self.root,
            text=t("crop_whitespace"),
            variable=self.crop_var,
            command=self._toggle_crop,
        ).pack(anchor="w", padx=16, pady=(0, 8))
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", padx=16, pady=8)
        ttk.Button(btns, text=t("test_print"), command=self._test_print).pack(side="left")
        ttk.Button(btns, text=t("quit"), command=self._on_close).pack(side="right")
        ttk.Button(self.root, text=t("repair_printer"), command=self._repair_printer).pack(
            fill="x", padx=16, pady=(4, 4)
        )
        ttk.Label(
            self.root,
            text=t("repair_hint"),
            foreground="#666",
            wraplength=408,
        ).pack(anchor="w", padx=16, pady=(0, 12))

    def _boot(self) -> None:
        try:
            if not printer_installed():
                self.status.set(t("need_admin"))
                self.root.update_idletasks()
                code = elevate_and_wait("setup")
                if code != 0 or not printer_installed():
                    raise RuntimeError(t("setup_failed"))
            self.status.set(t("connecting"))
            self.root.update_idletasks()
            self.server = T50Server()
            self.server.crop_whitespace = bool(self.crop_var.get())
            self.server.start_background(require_device=True)
            if not startup_enabled():
                set_startup(True)
                self.startup_var.set(True)
            self._refresh_status()
            self.root.after(4000, self._poll)
        except (HidError, PrinterError, RuntimeError, OSError) as exc:
            log.exception("app boot failed")
            msg = self._ui_exc(exc)
            self.status.set(t("start_failed", err=msg))
            messagebox.showerror(t("app_title"), msg, parent=self.root)

    def _refresh_status(self) -> None:
        if not self.server or not self.server.device:
            self.status.set(t("ipp_no_device"))
            return
        try:
            st = self.server.device.query_status()
            mat = self.server.device.query_material()
            self.server.model.width_mm = min(mat.width_mm or self.server.model.width_mm, 48)
            self.server.model.height_mm = mat.height_mm or self.server.model.height_mm
            if st.has_error():
                keys = list(st.error_keys())
                if mat.width_mm and mat.device_sn:
                    keys = [k for k in keys if k != "err_no_tape"]
                if keys:
                    err = ", ".join(t(k) for k in keys)
                    self.status.set(t("printer_error", err=err))
                else:
                    self.status.set(t("ready", name=PRINTER_NAME))
            else:
                self.status.set(t("ready", name=PRINTER_NAME))
            self.tape.set(t("current_tape", tape=mat))
        except Exception as exc:
            self.status.set(t("printer_offline", err=exc))

    def _poll(self) -> None:
        if self.server:
            try:
                self._refresh_status()
            except Exception:
                log.debug("poll failed", exc_info=True)
            self.root.after(4000, self._poll)

    def _ui_exc(self, exc: BaseException) -> str:
        msg = str(exc)
        if isinstance(exc, HidError) and "No SUPVAN T50 HID" in msg:
            return t("no_hid")
        return msg

    def _repair_printer(self) -> None:
        self.status.set(t("repairing"))
        self.root.update_idletasks()
        try:
            code = elevate_and_wait("setup")
            if code != 0 or not printer_installed():
                raise RuntimeError(t("setup_failed"))
            self.status.set(t("repair_ok"))
            self._refresh_status()
        except Exception as exc:
            log.exception("printer repair failed")
            msg = self._ui_exc(exc)
            self.status.set(t("setup_failed"))
            messagebox.showerror(t("app_title"), msg, parent=self.root)

    def _toggle_crop(self) -> None:
        enabled = bool(self.crop_var.get())
        try:
            set_crop_whitespace(enabled)
        except Exception as exc:
            messagebox.showerror(t("app_title"), str(exc), parent=self.root)
            self.crop_var.set(crop_whitespace_enabled())
            return
        if self.server:
            self.server.crop_whitespace = enabled

    def _toggle_startup(self) -> None:
        try:
            set_startup(bool(self.startup_var.get()))
        except Exception as exc:
            messagebox.showerror(t("app_title"), str(exc), parent=self.root)
            self.startup_var.set(startup_enabled())

    def _test_print(self) -> None:
        if not self.server or not self.server.device:
            messagebox.showwarning(t("app_title"), t("printer_not_connected"), parent=self.root)
            return

        def worker() -> None:
            try:
                mat = self.server.device.test_print()  # type: ignore[union-attr]
                self.root.after(0, lambda: self.status.set(t("test_printed", tape=mat)))
            except Exception as exc:
                log.exception("test print")
                self.root.after(
                    0,
                    lambda: messagebox.showerror(t("test_print_failed"), str(exc), parent=self.root),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self) -> None:
        if self.server:
            self.server.shutdown()
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_app() -> int:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, True, "T50LabelSingleton")
    if kernel32.GetLastError() == 183:
        from t50.winsetup import message_box

        message_box(t("already_running"), title=t("app_title"))
        return 0
    return T50App().run()
