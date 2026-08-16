# Windows printing (IPP Class Driver + Acrobat)

The T50 is not USB Printer Class. Windows will not bind a standard USBPRINT driver. This project therefore pretends to be a network IPP printer on localhost.

## Queue

```powershell
Add-Printer -IppURL http://127.0.0.1:8631/ipp/print
```

The Microsoft **IPP Class Driver** (v4) is attached. `-Name` is ignored; the queue is named from IPP `printer-name` → **T50 Label**.

Repair (`python -m t50 setup` / the UI button) is: stop vendor service → `Remove-Printer` → `Add-Printer -IppURL` → create a Print Server form for the loaded tape → patch the queue GPD so that form is the only paper size.

## Custom label sizes (40×30 mm, etc.)

Setup creates a user form named **T50 40x30 mm** (size comes from the loaded tape, or 40×30 if the printer is not open). It writes that name into the per-queue GPD, sets it as the default, and removes A4/Letter from the GPD. Extra forms you created by hand (for example **Supvan Label**) are left in Print Server Properties but are not listed on **T50 Label**.

Do not patch `pdc.xml`. If you change tape size, run **Repair printer…** again.

## Why Acrobat still defaults to A4 sometimes

The class driver only exposes a handful of office sizes from IPP (**A4**, **Letter**) until the GPD patch above runs. Custom PWG self-describing names such as `om_40x30-label_40000x30000um` are still ignored by Windows. `Get-PrintConfiguration` may stay on A4 until you pick a custom form in preferences.

We tried rewriting:

```text
C:\Windows\System32\spool\V4Dirs\<guid>\pdc.xml
```

plus the generated GPD to inject a fixed 40×30 mm PrintSchema option. That emptied `DeviceCapabilities` (no paper list, `Get-PrintConfiguration` 0x80004005). **Abandoned.** `apply_label_paper()` in `t50/winpaper.py` remains for tests only.

Practical rule: create a Print Server Property form at the tape size, select it in **T50 Label** preferences, and print **Actual size**. Windows paints that page; this app crops the ink onto the tape.

## Job payload

Windows `Send-Document` typically:

1. Uses **HTTP/1.1 chunked** transfer (`[IPP attrs][packed PWG][0]`). Reading only `Content-Length` yields an empty body and Acrobat reports “document could not be printed”.
2. Sends **`image/pwg-raster`**, magic `RaS2`, even when `cupsCompression=0`.
3. Compresses with **Wi-Fi P2PS PackBits** (HP jipp / Windows), not uncompressed rows and not RFC 1978 PackBits.

PackBits (see `decode_pwg_packbits`):

```text
line-repeat byte
then until the row is full:
  control < 128  → repeat next pixel (control+1) times
  control >= 128 → (257-control) literal pixels
```

A ~19 KB job expands to a full A4 8-bit page (for example 1678×2373).

## Layout (A4 → tape)

Example: 40×30 mm tape is 320×240 dots. Acrobat’s 40×30 mm page is centered on A4; the ink bbox is a **tall** rectangle (Windows/Acrobat rotated the landscape page onto portrait A4).

`layout_on_tape` then:

1. Crop whitespace
2. **ROTATE_270** if that increases the fit scale (landscape tape)
3. **FLIP_LEFT_RIGHT**
4. Unsharp mask + threshold (stay in 8-bit until the last step; never LANCZOS a 1-bit image)
5. Center on the tape canvas

If you change tape aspect or Windows starts sending a true 40×30 raster, revisit rotation/flip before adding more scaling.

## Resolution

IPP advertises 203 and 406 DPI (`pwg-raster-document-resolution-supported`). The head is 203 DPI. If Windows honors 406, the app downsamples with area (BOX) resampling, which is cleaner than thresholding a 203 DPI anti-aliased page.

`print-quality` 5 (high) is advertised; the class driver may ignore it.

## Acrobat checklist

- Page size in Document Properties = tape size (inches or mm)
- Print: **T50 Label**, **Actual size**, not Fit
- Ignore A4 in the paper dropdown
- Keep the T50 Label window running
- If the job errors immediately, check `t50.log` for chunked body size and `decoded Windows PackBits`
