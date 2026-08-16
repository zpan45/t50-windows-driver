"""Turn images / PWG raster pages into 1-bit row-major MSB-first bitmaps."""

from __future__ import annotations

import logging
import struct
from io import BytesIO

from t50 import DOTS_PER_MM, PRINTHEAD_WIDTH_DOTS

log = logging.getLogger("t50.raster")

try:
    from PIL import Image, ImageFilter, ImageOps
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageOps = None  # type: ignore


def _require_pil() -> None:
    if Image is None:
        raise RuntimeError("Pillow is required. Run: python -m pip install pillow")


def image_to_1bpp(
    image: "Image.Image",
    width_dots: int,
    height_dots: int,
    *,
    dither: bool = True,
) -> bytes:
    _require_pil()
    img = image.convert("L")
    img = ImageOps.fit(img, (width_dots, height_dots), method=Image.Resampling.LANCZOS)
    if dither:
        bw = img.convert("1")
    else:
        bw = img.point(lambda x: 0 if x < 160 else 255, mode="1")
    return _pil_1_to_msb(bw)


def _pil_1_to_msb(img: "Image.Image") -> bytes:
    """PIL mode '1' is 0=black. Printer 1-bit MSB: 1 = burn = black."""
    width, height = img.size
    bpl = (width + 7) // 8
    out = bytearray(bpl * height)
    pixels = img.tobytes()
    # PIL '1' packed bits are MSB-first already, but 0=black. Invert.
    for i, b in enumerate(pixels):
        out[i] = (~b) & 0xFF
    # PIL may pad to full bytes already matching bpl.
    if len(out) != bpl * height:
        # Fall back to getdata()
        out = bytearray(bpl * height)
        data = list(img.getdata())
        for y in range(height):
            for x in range(width):
                if data[y * width + x] == 0:  # black
                    out[y * bpl + (x // 8)] |= 0x80 >> (x % 8)
    return bytes(out[: bpl * height])


def load_image_file(path: str, width_dots: int, height_dots: int, *, dither: bool = True) -> bytes:
    _require_pil()
    with Image.open(path) as img:
        return image_to_1bpp(img, width_dots, height_dots, dither=dither)


def load_image_bytes(data: bytes, width_dots: int, height_dots: int, *, dither: bool = True) -> bytes:
    _require_pil()
    with Image.open(BytesIO(data)) as img:
        return image_to_1bpp(img, width_dots, height_dots, dither=dither)


# cups_page_header2_t offsets (big-endian unsigned 32-bit unless noted)
_PWG_WIDTH = 372
_PWG_HEIGHT = 376
_PWG_BPC = 384
_PWG_BPP = 388
_PWG_BPL = 392
_PWG_CSPACE = 400
_PWG_HEADER = 1796


def _iter_pwg_pages(data: bytes) -> list[tuple[bytes, int, int, int, int]]:
    """Yield decoded PWG pages as (raw, width, height, bpp, bpl)."""
    if len(data) < 4:
        raise ValueError("empty raster")
    magic = data[:4]
    be = magic in (b"RaS2", b"tSaR", b"rinU")
    if magic not in (b"RaS2", b"RaSt", b"tSaR", b"Unir", b"rinU"):
        raise ValueError(f"not a PWG/CUPS raster (magic={magic!r})")
    pages = []
    off = 4
    endian = ">" if be else "<"
    while off + _PWG_HEADER <= len(data):
        header = data[off : off + _PWG_HEADER]
        off += _PWG_HEADER
        width = struct.unpack_from(endian + "I", header, _PWG_WIDTH)[0]
        height = struct.unpack_from(endian + "I", header, _PWG_HEIGHT)[0]
        bpp = struct.unpack_from(endian + "I", header, _PWG_BPP)[0]
        bpl = struct.unpack_from(endian + "I", header, _PWG_BPL)[0]
        if width == 0 or height == 0 or bpl == 0 or height * bpl > 50_000_000:
            break
        nbytes = bpl * height
        remaining = len(data) - off
        compression = struct.unpack_from(endian + "I", header, 404)[0]
        log.info(
            "PWG page magic=%s w=%s h=%s bpp=%s bpl=%s compress=%s need=%s have=%s",
            magic, width, height, bpp, bpl, compression, nbytes, remaining,
        )
        if remaining >= nbytes:
            raw = data[off : off + nbytes]
            off += nbytes
        else:
            raw, consumed = decode_pwg_packbits(data[off:], width, height, bpp, bpl)
            log.info("decoded Windows PackBits PWG (%s -> %s bytes)", consumed, len(raw))
            off = len(data)
        pages.append((raw, width, height, bpp, bpl))
    if not pages:
        raise ValueError("no pages in PWG raster")
    return pages


def parse_pwg_raster(data: bytes) -> list[tuple[bytes, int, int]]:
    """Return a list of (row-major MSB 1bpp, width, height) pages."""
    return [(_pwg_page_to_1bpp(raw, w, h, bpp, bpl), w, h) for raw, w, h, bpp, bpl in _iter_pwg_pages(data)]


def parse_pwg_images(data: bytes) -> list["Image.Image"]:
    """Decode PWG pages to 8-bit grayscale images (keeps Windows 8-bit data)."""
    _require_pil()
    return [_pwg_raw_to_l(raw, w, h, bpp, bpl) for raw, w, h, bpp, bpl in _iter_pwg_pages(data)]


def decode_pwg_packbits(
    data: bytes,
    width: int,
    height: int,
    bpp: int,
    bpl: int,
) -> tuple[bytes, int]:
    """Wi-Fi P2PS / Windows IPP PackBits (not RFC 1978).

    Each band is: line-repeat byte, then control codes until the row is full.
    control < 128: repeat next pixel (control+1) times
    control >= 128: (257-control) literal pixels
    """
    bytes_per_pixel = 1 if bpp <= 8 else bpp // 8
    bytes_per_line = bpl if bpl else bytes_per_pixel * width
    inp = memoryview(data)
    pos = 0
    out = bytearray()
    lines = 0

    def read_n(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(inp):
            raise ValueError(f"truncated PackBits PWG at {pos}+{n} of {len(data)}")
        chunk = bytes(inp[pos : pos + n])
        pos += n
        return chunk

    while lines < height:
        line_repeat = read_n(1)[0]
        row = bytearray()
        while len(row) < bytes_per_line:
            control = read_n(1)[0]
            if control < 128:
                pix = read_n(bytes_per_pixel)
                row.extend(pix * (control + 1))
            else:
                n = 257 - control
                row.extend(read_n(bytes_per_pixel * n))
        row = row[:bytes_per_line]
        copies = min(line_repeat + 1, height - lines)
        out.extend(row * copies)
        lines += copies
    return bytes(out), pos


def _pwg_raw_to_l(raw: bytes, width: int, height: int, bpp: int, bpl: int) -> "Image.Image":
    """Convert PWG raw pixels to an 8-bit grayscale Pillow image."""
    _require_pil()
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid PWG size {width}x{height}")
    if bpp == 8:
        if bpl == width:
            return Image.frombytes("L", (width, height), raw[: width * height])
        rows = bytearray(width * height)
        for y in range(height):
            src = raw[y * bpl : y * bpl + width]
            dest = y * width
            rows[dest : dest + len(src)] = src
        return Image.frombytes("L", (width, height), bytes(rows))
    if bpp == 1:
        img = Image.new("L", (width, height), 255)
        px = img.load()
        for y in range(height):
            row = raw[y * bpl : (y + 1) * bpl]
            for x in range(width):
                if x // 8 >= len(row):
                    break
                if row[x // 8] & (0x80 >> (x % 8)):
                    px[x, y] = 0
        return img
    if bpp == 24:
        row_bytes = width * 3
        if bpl == row_bytes:
            rgb = Image.frombytes("RGB", (width, height), raw[: row_bytes * height])
        else:
            rows = bytearray(row_bytes * height)
            for y in range(height):
                src = raw[y * bpl : y * bpl + row_bytes]
                dest = y * row_bytes
                rows[dest : dest + len(src)] = src
            rgb = Image.frombytes("RGB", (width, height), bytes(rows))
        return rgb.convert("L")
    raise ValueError(f"unsupported PWG bits-per-pixel: {bpp}")


def _pwg_page_to_1bpp(raw: bytes, width: int, height: int, bpp: int, bpl: int) -> bytes:
    out_bpl = (width + 7) // 8
    out = bytearray(out_bpl * height)
    if bpp == 1:
        for y in range(height):
            row = raw[y * bpl : y * bpl + out_bpl]
            # CUPS/PWG 1-bit is typically 1=black, already MSB-first.
            out[y * out_bpl : y * out_bpl + len(row)] = row[:out_bpl]
        return bytes(out)
    if bpp == 8:
        for y in range(height):
            row = raw[y * bpl : (y + 1) * bpl]
            for x in range(min(width, len(row))):
                if row[x] < 128:
                    out[y * out_bpl + (x // 8)] |= 0x80 >> (x % 8)
        return bytes(out)
    if bpp in (24, 32):
        spp = 3 if bpp == 24 else 4
        for y in range(height):
            row = raw[y * bpl : (y + 1) * bpl]
            for x in range(width):
                i = x * spp
                if i + 2 >= len(row):
                    break
                r, g, b = row[i], row[i + 1], row[i + 2]
                grey = (r * 299 + g * 587 + b * 114) // 1000
                if grey < 128:
                    out[y * out_bpl + (x // 8)] |= 0x80 >> (x % 8)
        return bytes(out)
    raise ValueError(f"unsupported PWG bits-per-pixel: {bpp}")


def mm_to_dots(mm: int) -> int:
    return int(mm) * DOTS_PER_MM


def _unpack_1bpp_msb(data: bytes, width: int, height: int) -> "Image.Image":
    _require_pil()
    img = Image.new("1", (width, height), 1)
    px = img.load()
    bpl = (width + 7) // 8
    for y in range(height):
        row = y * bpl
        for x in range(width):
            if row + (x // 8) >= len(data):
                continue
            if data[row + (x // 8)] & (0x80 >> (x % 8)):
                px[x, y] = 0
    return img


def crop_content(
    data: bytes,
    width: int,
    height: int,
    pad: int = 4,
) -> tuple[bytes, int, int]:
    """Trim white margin so an Actual-size 40x30 page on A4 paper is not shrunk."""
    _require_pil()
    if width <= 0 or height <= 0:
        return data, width, height
    img = _unpack_1bpp_msb(data, width, height)
    mask = img.point(lambda p: 255 if p == 0 else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return data, width, height
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(width, right + pad)
    bottom = min(height, bottom + pad)
    if right - left >= width - 2 and bottom - top >= height - 2:
        return data, width, height
    cropped = img.crop((left, top, right, bottom)).convert("1")
    return _pil_1_to_msb(cropped), cropped.width, cropped.height


def fit_to_tape(
    data: bytes,
    width: int,
    height: int,
    tape_width_dots: int,
    tape_height_dots: int,
) -> tuple[bytes, int, int]:
    """Scale an oversized page (e.g. A4 from Acrobat) down onto the loaded tape."""
    _require_pil()
    if width <= 0 or height <= 0:
        return data, width, height
    img = _unpack_1bpp_msb(data, width, height).convert("L")
    if img.width > tape_width_dots or img.height > tape_height_dots:
        img.thumbnail((tape_width_dots, tape_height_dots), Image.Resampling.LANCZOS)
    canvas = Image.new("L", (tape_width_dots, tape_height_dots), 255)
    canvas.paste(
        img,
        ((tape_width_dots - img.width) // 2, (tape_height_dots - img.height) // 2),
    )
    bw = canvas.convert("1")
    return _pil_1_to_msb(bw), tape_width_dots, tape_height_dots


_THRESHOLD = 185
_MIDTONE_FRACTION = 0.35


def _sharpen_gray(img: "Image.Image") -> "Image.Image":
    """Pull anti-aliased Acrobat edges toward black/white before thresholding."""
    return img.filter(ImageFilter.UnsharpMask(radius=1.25, percent=160, threshold=2))


def _l_to_1bit(img: "Image.Image") -> "Image.Image":
    """Threshold label text; Floyd–Steinberg only when the page has many midtones."""
    hist = img.histogram()
    total = img.width * img.height
    midtones = sum(hist[32:224]) if len(hist) >= 224 else 0
    if total > 0 and midtones / total > _MIDTONE_FRACTION:
        return img.convert("1")
    return img.point(lambda x: 0 if x < _THRESHOLD else 255, mode="1")


def _fit_scale(width: int, height: int, tape_w: int, tape_h: int) -> float:
    if width <= 0 or height <= 0:
        return 0.0
    return min(tape_w / width, tape_h / height)


def layout_on_tape(
    img: "Image.Image",
    tape_width_dots: int,
    tape_height_dots: int,
    pad: int = 8,
) -> tuple[bytes, int, int]:
    """Crop, optionally rotate, and center an 8-bit page onto the tape; 1-bit last."""
    _require_pil()
    img = img.convert("L")
    src_w, src_h = img.size
    ink = ImageOps.invert(img)
    bbox = ink.getbbox()
    if bbox is not None:
        left, top, right, bottom = bbox
        left = max(0, left - pad)
        top = max(0, top - pad)
        right = min(src_w, right + pad)
        bottom = min(src_h, bottom + pad)
        if not (right - left >= src_w - 2 and bottom - top >= src_h - 2):
            img = img.crop((left, top, right, bottom))

    w, h = img.size
    scale_0 = _fit_scale(w, h, tape_width_dots, tape_height_dots)
    scale_90 = _fit_scale(h, w, tape_width_dots, tape_height_dots)
    rotated = scale_90 > scale_0
    if rotated:
        # Windows places the landscape 40x30 page as a tall crop on A4.
        # ROTATE_90 looks sideways and mirrored; ROTATE_270 restores reading order.
        img = img.transpose(Image.Transpose.ROTATE_270)
        w, h = img.size

    scaled = False
    if w > tape_width_dots or h > tape_height_dots:
        scale = min(tape_width_dots / w, tape_height_dots / h)
        new_w = max(1, min(tape_width_dots, int(round(w * scale))))
        new_h = max(1, min(tape_height_dots, int(round(h * scale))))
        img = img.resize((new_w, new_h), Image.Resampling.BOX)
        w, h = img.size
        scaled = True

    img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    img = _sharpen_gray(img)
    log.info(
        "layout_on_tape: content %sx%s rotate=%s mirror=lr scaled=%s onto %sx%s tape",
        w,
        h,
        270 if rotated else 0,
        scaled,
        tape_width_dots,
        tape_height_dots,
    )
    bw = _l_to_1bit(img)
    canvas = Image.new("1", (tape_width_dots, tape_height_dots), 1)
    canvas.paste(bw, ((tape_width_dots - w) // 2, (tape_height_dots - h) // 2))
    return _pil_1_to_msb(canvas), tape_width_dots, tape_height_dots
