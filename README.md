# T50 Label for Windows

Unofficial Windows printing for **SUPVAN T50M Pro** / **Katasymbol T50M Pro** label printers.

The official Windows stack (`Supvan_T50_Printer` + `Supvan_T50_Service`) is a broken Unidrv GPD and a HID service that does not speak USB Printer Class. This project registers a normal Windows printer named **T50 Label**. Word, Chrome, Acrobat, and anything else that can print can send jobs to it.

[中文说明](docs/zh.md) · [Architecture](docs/architecture.md) · [USB protocol](docs/protocol.md) · [Windows / Acrobat](docs/windows-printing.md) · [Development](docs/development.md)

**This is not affiliated with SUPVAN, Katasymbol, or Microsoft.** It talks to the printer over a reverse-engineered USB HID protocol. Use at your own risk.

## Download (simplest)

You do **not** need Python or a git clone.

1. Download `T50Label.exe` from the [latest GitHub Release](https://github.com/zpan45/t50-windows-driver/releases).
2. Double-click it. On first run, accept UAC so Windows can register the printer queue.
3. Leave the **T50 Label** window open while you print.
4. In Word, Chrome, Acrobat, or any other app, print to **T50 Label**.

### Paper size (label tape)

The Microsoft IPP Class Driver normally only shows **A4** / **Letter**. On first run or **Repair printer…**, T50 Label:

1. Reads the loaded tape size from the printer (for example 40×30 mm)
2. Creates a Windows Print Server form named **T50 40x30 mm** (or the current tape size)
3. Sets that form as the only paper size on the **T50 Label** queue (A4/Letter are removed from the queue GPD)

Then in Word, Chrome, Acrobat, 3uTools, and so on:

1. Choose printer **T50 Label**
2. Set paper / page size to **T50 40x30 mm** (or your tape’s form name)
3. Print **Actual size** / 实际大小 — not Fit / Shrink

If you change rolls to a different size, run **Repair printer…** again so the form matches the new tape.

### Crop whitespace

**Crop whitespace** (on by default in the T50 Label window) trims blank margins before centering on the tape. Turn it **off** if you need app margins (for example 2 mm vs 3 mm in 3uTools) to stay on the label.

The official **Katasymbol Editor** and this app **cannot share the HID device**. Quit the vendor app first; this project stops `Supvan_T50_Service` on start.

If the queue is missing or jobs never arrive, click **Repair printer…** and accept UAC. That reinstalls the Windows queue and recreates the tape-size paper form.

Check **Start with Windows** to launch at login.

## What you get

- A small always-on window (**T50 Label**) that holds the USB device and an IPP server on `http://127.0.0.1:8631/ipp/print`
- A Windows queue using the **Microsoft IPP Class Driver** (`Add-Printer -IppURL`)
- Setup creates a Print Server form for the loaded tape and makes it the only paper size (A4/Letter removed from the queue GPD)
- Automatic rotate / mirror / optional whitespace crop so a label-sized page lands on the tape
- Test print, Start with Windows, Crop whitespace, and **Repair printer…** (re-registers the queue)

The printer itself is:

| | |
|---|---|
| USB | VID `0x1820` PID `0x207F` — HID + mass storage, **not** USB Printer Class |
| Head | 203 DPI, 384 dots / 48 mm |
| Tape | RFID-tagged rolls (for example 40×30 mm gap labels) |
| Language | Proprietary HID (not ESC/POS, not ZPL) |

## Requirements

- Windows 10 or 11 (tested on Windows 10 Pro 22H2)
- The T50 plugged in over USB-C
- Administrator once, to create the printer queue (UAC)
- Python 3.10+ (3.12/3.14 work) **only if you run from source**. The downloaded exe does not need Python. Pillow is the only extra package for source installs.

## Install from source (developers)

```powershell
git clone https://github.com/zpan45/t50-windows-driver.git
cd t50-windows-driver
py -m pip install -r requirements.txt
```

Double-click `start.bat`, or:

```powershell
pythonw -m t50
```

The first run asks for administrator permission, registers **T50 Label**, and disables the unused vendor printer/service. Leave the T50 Label window open while you print, same as with the exe.

Uninstall:

```powershell
python -m t50 uninstall
```

### Build a single-file exe

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

That writes `dist\T50Label.exe`. Copy it to another Windows 10/11 PC and double-click. First run still needs UAC. End users should prefer the [Releases](https://github.com/zpan45/t50-windows-driver/releases) download instead of building this themselves.

## Commands

```text
python -m t50              # window (default)
python -m t50 probe        # list HID interfaces, status, tape RFID
python -m t50 status       # status + tape
python -m t50 test         # geometry test pattern
python -m t50 print FILE   # print a PNG/JPEG onto the loaded tape
python -m t50 setup        # register / repair the Windows queue (admin)
python -m t50 uninstall    # remove the queue, restore vendor driver
python -m t50 serve        # IPP only, foreground (debug)
```

Log and last job dump:

```text
%LOCALAPPDATA%\T50Label\t50.log
%LOCALAPPDATA%\T50Label\last-job.bin
```

## How it works (short)

```text
Word / Chrome / Acrobat
        │  GDI / XPS
        ▼
   Microsoft IPP Class Driver  ── PWG raster (often A4 @ 203 DPI, PackBits)
        │  HTTP POST 127.0.0.1:8631  (chunked)
        ▼
t50 IPP server  →  optional whitespace crop, rotate 270°, flip L/R, sharpen, 1-bit
        ▼
column-major LSB bitmap → LZMA → USB HID reports
        ▼
T50M Pro printhead
```

Details: [docs/architecture.md](docs/architecture.md), [docs/windows-printing.md](docs/windows-printing.md).

## Known limits

- **Paper size in the print dialog** is the loaded tape. Setup creates a Print Server form named **T50 40x30 mm** (or the current tape size), sets it as default, and drops A4/Letter from the queue GPD. Do not use `apply_label_paper()` (that PDC patch breaks `DeviceCapabilities`).
- **Crop whitespace** (default on) removes blank page margins before centering. Turn it off in the T50 Label window if you need those margins to print.
- **203 DPI thermal** is the physical limit. Vector PDFs are rasterized by Windows with gray edges; the app sharpens and thresholds. Asking Windows for 406 DPI (when the driver honors it) and then downsampling can look a bit cleaner.
- **Orientation** is tuned for landscape labels (for example 40×30 mm). Other page shapes may need a layout tweak.
- Not a signed WHQL driver. Some enterprise policies block unsigned local IPP printers.
- Only the T50 HID family above is supported.

## Credits

USB HID framing, bitmap layout, and LZMA parameters come from **[heeen/supvan-cups](https://github.com/heeen/supvan-cups)** (MIT), reverse-engineered from the Katasymbol Android app.

## License

[MIT](LICENSE)
