# USB HID protocol (T50 family)

Framing, bitmap packing, and LZMA parameters were reverse-engineered by
[heeen/supvan-cups](https://github.com/heeen/supvan-cups) (MIT) from the
Katasymbol Android app. This document describes what **this** repo implements.

## Device

- USB VID `0x1820`, PID `0x207F`
- HID interface (vendor usage page) plus a mass-storage interface
- 64-byte HID reports
- Printhead: 384 dots (48 mm) at 8 dots/mm (203 DPI)

Commands are written as HID output reports; replies are input reports.

## Command frame

USB uses `0xC0 0x40` framing, big-endian 16-bit parameters:

```text
C0 40  p1_hi p1_lo  cmd  00  08  00  [p2_hi p2_lo]
```

| cmd | name | purpose |
|-----|------|---------|
| `0x10` | `BUF_FULL` | After bulk: remaining length + speed |
| `0x11` | `INQUIRY_STA` | Status bits + print count |
| `0x12` | `CHECK_DEVICE` | Presence / handshake |
| `0x13` | `START_PRINT` | Enter print mode |
| `0x14` | `STOP_PRINT` | Abort |
| `0x2E` | `PAPER_SKIP` | Feed (unused in the Windows app) |
| `0x30` | `RETURN_MAT` | RFID tape: size, gap, type, serial |
| `0x5C` | `NEXT_ZIPPEDBULK` | Compressed payload length; then raw 64-byte chunks |

## Status (`INQUIRY_STA`)

Reply is at least 7 bytes after HID unpack. Layout:

```text
[0] type (often 0x08 — not the command id)
[1] MSTA lo
[2] MSTA hi
[3] FSTA lo
[4] FSTA hi
[5..6] print count (little-endian)
```

Implemented bits (see `t50.protocol.parse_status`):

| byte | mask | meaning |
|------|------|---------|
| `[1]` | `0x01` | buffer full |
| `[1]` | `0x02` | label RW error |
| `[1]` | `0x04` | label end |
| `[1]` | `0x08` | label mode error |
| `[1]` | `0x10` | ribbon RW error |
| `[1]` | `0x20` | ribbon end |
| `[1]` | `0x40` | low battery |
| `[2]` | `0x04` | device busy |
| `[2]` | `0x08` | head too hot |
| `[3]` | `0x08` | cover open |
| `[3]` | `0x10` | USB inserted |
| `[3]` | `0x40` | printing |
| `[4]` | `0x01` | label not installed |

Do **not** assume byte `[0]` equals `0x11`. On T50M Pro it is typically `0x08`. Treating any other first byte as “not status” will drop a valid reply and look like `no response to INQUIRY_STA`.

## Material (`RETURN_MAT`)

64-byte-ish HID payload:

| offset | field |
|--------|--------|
| 19 | width mm |
| 20 | height mm (0 = continuous) |
| 21 | gap mm |
| 22 | label type |
| 31–32 | numeric SN |
| 40+ | ASCII device serial, NUL-terminated |

## Bitmap

Host input is **row-major, MSB-first, 1 = black**.

Firmware wants **column-major, LSB-first**:

- Image **width** = across the head (≤ 384 dots)
- Image **height** = feed direction (one HID “column” per feed step)
- If width &lt; 384, the page is centered on the head

`t50.bitmap.create_test_pattern` draws this coordinate system (border, diagonals, buffer regions).

## Print buffers

Each buffer is 4096 bytes: 14-byte header + up to 4074 data bytes + checksum every 256 bytes (`t50.buffer`). Density 0–15 lives in the header.

## Compression

LZMA1, `format=ALONE`:

- `dict_size=8192`, `lc=3`, `lp=0`, `pb=2`, `nice_len=128`
- Bytes 5–12 of the stream are replaced with the uncompressed length (little-endian u64), matching the firmware

Speed byte sent with `BUF_FULL` is derived from compressed size (`t50.compress.calc_speed`).

## Host I/O notes

- One `RLock` around HID (`Printer._io`) so the UI poll cannot race a job.
- Do not “drain” the HID pipe with short cancelled reads before a command: on Windows overlapped I/O that cancels the *next* reply.
- After a job, leftover input can still exist; a later status poll must parse a real status packet, not a bulk ack. Serializing I/O is the main fix; matching on `resp[0] == 0x11` is the wrong fix.
