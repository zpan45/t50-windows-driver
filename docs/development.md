# Development

## Setup

```powershell
cd t50-windows-driver
py -m pip install -r requirements.txt
```

Python 3.10+ on Windows. HID and printer APIs are Windows-only (`t50.hidwin`, `t50.winsetup`). Unit tests that do not open HID can still run on the code logic.

## Tests

```powershell
python -m unittest tests.test_core tests.test_ipp tests.test_winpaper -v
```

`tests.test_core` covers protocol frames, bitmap packing, PWG PackBits, and `layout_on_tape` (including rotate 270° + left-right flip). `tests.test_ipp` covers IPP encoding and chunked HTTP. `tests.test_winpaper` covers GPD/PDC text patches that are **not** applied at runtime.

There is no hardware-in-the-loop CI. Use:

```powershell
python -m t50 probe
python -m t50 test
```

with the printer attached.

## Layout / orientation

Physical mapping:

- Bitmap **X** / width = across the 384-dot head (tape width)
- Bitmap **Y** / height = feed (label length)

Windows A4 PWG for a landscape 40×30 mm PDF is a tall crop. Current transform: **ROTATE_270** then **FLIP_LEFT_RIGHT**. If a new Windows build or a different label aspect looks wrong, change only that block in `t50.raster.layout_on_tape` and add a regression in `tests.test_core.LayoutTests`.

Do not scale 1-bit bitmaps. Stay 8-bit through crop/rotate/resize; threshold last.

## Logging

`%LOCALAPPDATA%\T50Label\t50.log`  
Each job also writes `last-job.bin` (raw IPP document, usually PWG).

```powershell
python -m t50 -v probe
```

## Packaging

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

Produces a windowed `dist\T50Label.exe` (PyInstaller onefile). The first run on a new PC still needs UAC for `Add-Printer`.

## Scripts

| File | Use |
|------|-----|
| `start.bat` | `pythonw -m t50` with no console flash |
| `scripts/build.ps1` | PyInstaller |
| `scripts/install-printer.ps1` | Optional manual `Add-Printer` if IPP is already listening |
| `scripts/uninstall-printer.ps1` | Optional manual queue removal |

Prefer `python -m t50 setup` / **Repair printer…** over the PowerShell helpers.

## What not to do

- Do not call `t50.winpaper.apply_label_paper()` from setup. It breaks the IPP class driver’s paper list.
- Setup calls `apply_custom_paper_support()`, which creates one **T50 WxH mm** form, makes it the GPD default, and removes A4/Letter. Do not use `CUSTOMSIZE` alone — the IPP class driver shows that as **User Defined Size**.
- Do not add ESC/POS or ZPL — the T50 will ignore it.
- Do not share the HID handle with `Supvan_T50_Service` or Katasymbol Editor.
