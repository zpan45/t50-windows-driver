"""UI strings: English by default, Chinese only when Windows UI language is Chinese."""

from __future__ import annotations

import ctypes

_LANG_CHINESE = 0x04

_EN = {
    "app_title": "T50 Label",
    "starting": "Starting…",
    "start_with_windows": "Start with Windows",
    "test_print": "Test print",
    "repair_printer": "Repair printer…",
    "repair_hint": (
        "Reinstalls the Windows “T50 Label” queue. Use this if the printer is missing "
        "from Print dialogs. It does not change paper size or print quality."
    ),
    "quit": "Quit",
    "need_admin": "Administrator permission needed to register the printer…",
    "repairing": "Repairing the Windows printer queue…",
    "repair_ok": "Printer queue re-registered.",
    "setup_failed": "Could not register the printer. Run again as administrator, or check that UAC was not cancelled.",
    "connecting": "Connecting to the printer…",
    "start_failed": "Couldn't start: {err}",
    "ipp_no_device": "IPP is running, but the printer is not connected",
    "printer_error": "Printer error: {err}",
    "ready": "Ready — print to “{name}”",
    "current_tape": "Loaded tape: {tape}",
    "printer_offline": "Printer disconnected: {err}",
    "printer_not_connected": "Printer is not connected.",
    "test_printed": "Test page printed ({tape})",
    "test_print_failed": "Test print failed",
    "already_running": "T50 Label is already running.",
    "setup_failed_box": "Could not register the printer: {err}",
    "err_no_tape": "no tape",
    "err_tape_end": "tape end",
    "err_tape": "tape error",
    "err_cover": "cover open",
    "err_tape_mode": "tape mode error",
    "err_ribbon_end": "ribbon end",
    "err_ribbon": "ribbon error",
    "err_hot": "head too hot",
    "err_battery": "low battery",
    "err_ok": "ok",
    "no_hid": (
        "No SUPVAN T50 HID device found (VID 1820 / PID 207F). "
        "Is the USB-C cable connected? Stop Supvan_T50_Service if it is running."
    ),
}

_ZH = {
    "app_title": "T50 标签打印机",
    "starting": "正在启动…",
    "start_with_windows": "开机自动启动",
    "test_print": "测试打印",
    "repair_printer": "安装/修复打印机…",
    "repair_hint": (
        "重新登记 Windows 里的「T50 标签打印机」队列。打印对话框里找不到这台打印机时再用。"
        "不会改纸张尺寸或打印质量。"
    ),
    "quit": "退出",
    "need_admin": "需要一次管理员权限，用来登记系统打印机…",
    "repairing": "正在修复 Windows 打印机队列…",
    "repair_ok": "打印机队列已重新登记。",
    "setup_failed": "没有登记成功。请用管理员再运行一次，或检查是否点了 UAC 取消。",
    "connecting": "正在连接打印机…",
    "start_failed": "启动失败：{err}",
    "ipp_no_device": "IPP 已启动，但没连上打印机",
    "printer_error": "打印机异常：{err}",
    "ready": "就绪 — 系统打印机「{name}」可用",
    "current_tape": "当前标签：{tape}",
    "printer_offline": "打印机掉线：{err}",
    "printer_not_connected": "打印机未连接。",
    "test_printed": "测试页已打印（{tape}）",
    "test_print_failed": "测试打印失败",
    "already_running": "T50 标签打印机已经在运行。",
    "setup_failed_box": "登记打印机失败：{err}",
    "err_no_tape": "未装标签",
    "err_tape_end": "标签用尽",
    "err_tape": "标签错误",
    "err_cover": "盖子打开",
    "err_tape_mode": "标签模式错误",
    "err_ribbon_end": "色带用尽",
    "err_ribbon": "色带错误",
    "err_hot": "打印头过热",
    "err_battery": "电量低",
    "err_ok": "正常",
    "no_hid": (
        "未找到 SUPVAN T50 HID 设备（VID 1820 / PID 207F）。"
        "请确认 USB-C 已连接。如果官方服务 Supvan_T50_Service 正在运行，请先停止。"
    ),
}

_ui_chinese: bool | None = None


def _detect_chinese() -> bool:
    try:
        langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        return (langid & 0x3FF) == _LANG_CHINESE
    except Exception:
        return False


def is_chinese() -> bool:
    global _ui_chinese
    if _ui_chinese is None:
        _ui_chinese = _detect_chinese()
    return _ui_chinese


def t(key: str, **kwargs: object) -> str:
    table = _ZH if is_chinese() else _EN
    text = table.get(key) or _EN[key]
    if kwargs:
        return text.format(**kwargs)
    return text


tr = t
