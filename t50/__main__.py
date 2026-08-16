"""CLI / double-click entry: python -m t50  →  tray-style window."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from t50 import DEFAULT_DENSITY, DEFAULT_LABEL_HEIGHT_MM, PRINTHEAD_WIDTH_MM
from t50.i18n import t
from t50.hidwin import HidError, list_devices
from t50.printer import Printer, PrinterError
from t50.raster import load_image_file, mm_to_dots
from t50.serve import DEFAULT_PORT, PRINTER_NAME, T50Server, stop_vendor_service
from t50.winsetup import (
    add_ipp_printer,
    elevate_and_wait,
    is_admin,
    log_path,
    message_box,
    printer_installed,
    remove_ipp_printer,
    restore_vendor_stack,
    set_startup,
    stop_vendor_stack,
)


def _setup_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.FileHandler(log_path(), encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers or None,
    )


def cmd_probe(_args: argparse.Namespace) -> int:
    stop_vendor_service()
    devices = list_devices()
    if not devices:
        print("No T50 HID device found (VID 1820 / PID 207F).")
        return 1
    for i, d in enumerate(devices):
        print(
            f"[{i}] {d.path}\n"
            f"    usage_page=0x{d.usage_page:04X} usage=0x{d.usage:04X} "
            f"in={d.input_report_len} out={d.output_report_len} ver=0x{d.version:04X}"
        )
    with Printer.open() as p:
        ok = p.check_device()
        print(f"CHECK_DEVICE: {'ok' if ok else 'FAILED'}")
        if not ok:
            return 1
        print(f"status:   {p.query_status()}")
        print(f"material: {p.query_material()}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    stop_vendor_service()
    with Printer.open() as p:
        print(f"status:   {p.query_status()}")
        print(f"material: {p.query_material()}")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    stop_vendor_service()
    with Printer.open() as p:
        mat = p.test_print(density=args.density)
        print(f"printed test pattern for {mat}")
    return 0


def cmd_print(args: argparse.Namespace) -> int:
    stop_vendor_service()
    with Printer.open() as p:
        mat = p.query_material()
        width_mm = min(mat.width_mm or PRINTHEAD_WIDTH_MM, PRINTHEAD_WIDTH_MM)
        height_mm = args.height or mat.height_mm or DEFAULT_LABEL_HEIGHT_MM
        w = mm_to_dots(width_mm)
        h = mm_to_dots(int(height_mm))
        bits = load_image_file(args.file, w, h, dither=not args.threshold)
        print(f"printing {args.file} as {width_mm}x{height_mm} mm ({w}x{h} dots)")
        p.print_page(bits, w, h, density=args.density)
        print("done")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    server = T50Server(host=args.host, port=args.port, density=args.density)
    print(f"Starting {PRINTER_NAME} at {server.model.uri}")
    server.run()
    return 0


def cmd_setup(_args: argparse.Namespace) -> int:
    if not is_admin():
        return elevate_and_wait("setup")
    logging.getLogger("t50").info("setup as admin")
    stop_vendor_stack()
    import urllib.request

    from t50.winsetup import IPP_URL

    server = None
    try:
        try:
            urllib.request.urlopen(IPP_URL, timeout=1)
            existing = True
        except Exception:
            existing = False
        if not existing:
            server = T50Server()
            server.start_background(require_device=False)
            if not server.wait_http(20):
                raise RuntimeError("IPP server did not start")
        add_ipp_printer()
        set_startup(True)
        return 0
    except Exception as exc:
        logging.getLogger("t50").exception("setup failed")
        message_box(t("setup_failed_box", err=exc), title=t("app_title"), error=True)
        return 1
    finally:
        if server:
            server.shutdown()
            time.sleep(0.4)


def cmd_uninstall(_args: argparse.Namespace) -> int:
    if not is_admin():
        return elevate_and_wait("uninstall")
    remove_ipp_printer()
    restore_vendor_stack()
    set_startup(False)
    return 0


def cmd_app(_args: argparse.Namespace) -> int:
    from t50.app import run_app

    return run_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="t50", description="T50M Pro Windows printer application")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--density", type=int, default=DEFAULT_DENSITY, help="print density 0-15 (default 3)")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("app", help="start the background window (default)")
    sub.add_parser("probe", help="find the USB HID device and query status")
    sub.add_parser("status", help="query tape and printer status")
    sub.add_parser("test", help="print a geometry test pattern")
    sub.add_parser("setup", help="register or repair the Windows printer (admin)")
    sub.add_parser("uninstall", help="remove the Windows printer and restore the vendor driver")

    p_print = sub.add_parser("print", help="print a PNG/JPEG file")
    p_print.add_argument("file")
    p_print.add_argument("--height", type=int, default=0, help="label height in mm (default: tape / 30)")
    p_print.add_argument("--threshold", action="store_true", help="use hard threshold instead of dither")

    p_serve = sub.add_parser("serve", help="run IPP in the foreground (debug)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)

    args = parser.parse_args(argv)
    if not args.cmd:
        args.cmd = "app"
    _setup_logging(args.verbose)
    try:
        if args.cmd == "app":
            return cmd_app(args)
        if args.cmd == "probe":
            return cmd_probe(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "test":
            return cmd_test(args)
        if args.cmd == "print":
            return cmd_print(args)
        if args.cmd == "serve":
            return cmd_serve(args)
        if args.cmd == "setup":
            return cmd_setup(args)
        if args.cmd == "uninstall":
            return cmd_uninstall(args)
        parser.error("unknown command")
        return 2
    except (HidError, PrinterError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
