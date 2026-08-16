# Architecture

T50 Label is a user-mode Windows application, not a kernel printer driver.

```mermaid
flowchart LR
  apps["Word / Chrome / Acrobat"] --> spooler["Windows spooler"]
  spooler --> ippdrv["Microsoft IPP Class Driver"]
  ippdrv -->|"HTTP POST /ipp/print<br/>PWG raster, often chunked"| ippsrv["t50 IPP server<br/>127.0.0.1:8631"]
  ippsrv --> layout["raster.layout_on_tape"]
  layout --> pack["bitmap.pack_page"]
  pack --> lzma["LZMA1 buffers"]
  lzma --> hid["USB HID VID 1820 / PID 207F"]
  hid --> head["203 DPI / 384-dot head"]
```

## Processes and files

| Piece | Role |
|---|---|
| `pythonw -m t50` / `T50Label.exe` | Tk window + IPP thread + HID |
| Mutex `T50LabelSingleton` | One instance |
| `http://127.0.0.1:8631/ipp/print` | IPP/1.1 + IPP/2.0 |
| Queue **T50 Label** | `Add-Printer -IppURL` → Microsoft IPP Class Driver |
| `%LOCALAPPDATA%\T50Label\` | `t50.log`, `last-job.bin` |
| HKCU `...\Run\T50Label` | Start with Windows |

`Add-Printer -IppURL` **ignores** `-Name`. The queue name comes from IPP `printer-name` (`T50 Label`).

## Package map

| Module | Responsibility |
|---|---|
| `t50.app` | Tk UI, status poll, Repair / Test print |
| `t50.i18n` | English by default; Chinese only if Windows UI language is Chinese |
| `t50.serve` | Glue: IPP jobs → layout → `Printer.print_page` |
| `t50.ipp` | Minimal IPP server, chunked HTTP body, printer attributes |
| `t50.raster` | PWG decode (including Windows PackBits), crop/rotate/flip, 1-bit |
| `t50.bitmap` | Row-major MSB → column-major LSB, center on 384-dot head |
| `t50.buffer` | 4096-byte print-buffer framing + checksum |
| `t50.compress` | LZMA1 (`dict=8192`, `lc=3`, `lp=0`, `pb=2`) |
| `t50.protocol` | HID command frames, status / material parse |
| `t50.printer` | Print job state machine over HID (`RLock` on I/O) |
| `t50.hidwin` | SetupAPI + `hid.dll` overlapped I/O (no extra native deps) |
| `t50.winsetup` | UAC, `Add-Printer`, stop vendor service, Run key |
| `t50.winpaper` | **Unused.** Experimental V4 GPD/PDC patch; it breaks the paper list |

## Print pipeline

1. Windows sends `Validate-Job` / `Create-Job` / `Send-Document`.
2. `Send-Document` is often `Transfer-Encoding: chunked`. The body is IPP attributes plus a packed PWG page (`RaS2`).
3. Typical Windows page: **A4 at 203 DPI, 8-bit sGray**, PackBits-compressed (~20 KB → ~4 MB).
4. `layout_on_tape`:
   - Crop ink bbox (the 40×30 mm page is centered on A4).
   - If the crop is taller than the tape, **rotate 270°**.
   - **Flip left-right** (printhead X vs PDF).
   - Unsharp mask, threshold (~185), optional Floyd–Steinberg only for heavy midtones.
   - Center on the tape bitmap (width = across the head, height = feed).
5. `pack_page` converts to the firmware’s column-major LSB format and pads to 384 dots.
6. Buffers are LZMA-compressed and sent with `NEXT_ZIPPEDBULK`.

HID access is serialized with `Printer._io` (`RLock`) so the UI status poll cannot interleave with a print.

## Vendor stack

On start, the app runs `sc stop Supvan_T50_Service`. Setup also marks `Supvan_T50_Printer` offline. Uninstall restores both.

## What this is not

- Not USB Printer Class / WSD / IPP-over-USB
- Not ESC/POS or ZPL
- Not a signed v3/v4 Unidrv inf
