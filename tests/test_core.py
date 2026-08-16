import unittest

from t50.bitmap import center_in_printhead, create_test_pattern, raster_to_column_major
from t50.raster import crop_content, decode_pwg_packbits, layout_on_tape, parse_pwg_raster
from t50.buffer import build_page_reg_bits, build_print_buffer, split_into_buffers
from t50.compress import calc_speed, compress_lzma
from t50.protocol import make_cmd, make_cmd_two, parse_status


class ProtocolTests(unittest.TestCase):
    def test_make_usb_cmd(self):
        frame = make_cmd(0x12, 0)
        self.assertEqual(frame, bytes([0xC0, 0x40, 0x00, 0x00, 0x12, 0x00, 0x08, 0x00]))
        frame = make_cmd(0x11, 0x1234)
        self.assertEqual(frame[2], 0x12)
        self.assertEqual(frame[3], 0x34)
        frame = make_cmd_two(0x5C, 512, 3)
        self.assertEqual(frame[2], 0x02)
        self.assertEqual(frame[3], 0x00)
        self.assertEqual(frame[8], 0x00)
        self.assertEqual(frame[9], 0x03)

    def test_parse_status_ready(self):
        resp = bytes([0x11, 0x00, 0x00, 0x40, 0x00, 0x00, 0x00, 0x00])
        st = parse_status(resp)
        self.assertTrue(st.printing)
        self.assertFalse(st.buf_full)
        self.assertFalse(st.has_error())

    def test_parse_status_errors(self):
        resp = bytes([0x11, 0x02, 0x00, 0x08, 0x01, 0x05, 0x00, 0x00])
        st = parse_status(resp)
        self.assertTrue(st.label_rw_error)
        self.assertTrue(st.cover_open)
        self.assertTrue(st.label_not_installed)
        self.assertTrue(st.has_error())
        self.assertEqual(st.error_keys(), ["err_no_tape", "err_tape", "err_cover"])
        self.assertEqual(st.print_count, 5)


class BitmapTests(unittest.TestCase):
    def test_raster_to_column_major_simple(self):
        data, cols, bpl = raster_to_column_major(bytes([0xFF, 0x00]), 8, 2)
        self.assertEqual(cols, 2)
        self.assertEqual(bpl, 1)
        self.assertEqual(data[0], 0xFF)
        self.assertEqual(data[1], 0x00)

    def test_center_in_printhead(self):
        data, bpl = center_in_printhead(bytes([0xFF, 0xFF]), 2, 8, 24)
        self.assertEqual(bpl, 3)
        self.assertEqual(data[0], 0x00)
        self.assertEqual(data[1], 0xFF)
        self.assertEqual(data[2], 0x00)

    def test_create_test_pattern_dimensions(self):
        data, w, h, bpl = create_test_pattern(40, 30)
        self.assertEqual(w, 384)
        self.assertEqual(h, 240)
        self.assertEqual(bpl, 48)
        self.assertEqual(len(data), 240 * 48)

    def test_crop_content_trims_whitespace(self):
        width, height = 32, 32
        bpl = 4
        data = bytearray(height * bpl)
        for y in range(8):
            data[y * bpl] = 0xFF
        cropped, cw, ch = crop_content(bytes(data), width, height, pad=2)
        self.assertLess(cw, width)
        self.assertLess(ch, height)
        self.assertGreaterEqual(cw, 8)
        self.assertGreaterEqual(ch, 8)
        self.assertEqual(len(cropped), ((cw + 7) // 8) * ch)


class RasterTests(unittest.TestCase):
    def test_pwg_packbits_white_rows(self):
        # two identical 8-byte white rows: lineRepeat=1, control=7, pixel=0xFF
        raw, used = decode_pwg_packbits(bytes([1, 7, 0xFF]), 8, 2, 8, 8)
        self.assertEqual(used, 3)
        self.assertEqual(raw, b"\xff" * 16)

    def test_parse_pwg_windows_packbits(self):
        import struct

        header = bytearray(1796)
        struct.pack_into(">I", header, 372, 8)  # width
        struct.pack_into(">I", header, 376, 2)  # height
        struct.pack_into(">I", header, 384, 8)  # bpc
        struct.pack_into(">I", header, 388, 8)  # bpp
        struct.pack_into(">I", header, 392, 8)  # bpl
        payload = b"RaS2" + bytes(header) + bytes([1, 7, 0x00])  # black rows
        pages = parse_pwg_raster(payload)
        self.assertEqual(len(pages), 1)
        bits, w, h = pages[0]
        self.assertEqual((w, h), (8, 2))
        self.assertTrue(any(bits))


class LayoutTests(unittest.TestCase):
    def test_layout_rotates_tall_content_without_downscale(self):
        from PIL import Image, ImageDraw

        from t50.raster import _unpack_1bpp_msb

        img = Image.new("L", (203, 285), 255)
        ImageDraw.Draw(img).rectangle([20, 20, 182, 264], fill=0)
        bits, w, h = layout_on_tape(img, 320, 240)
        self.assertEqual((w, h), (320, 240))
        bw = _unpack_1bpp_msb(bits, w, h)
        mask = bw.point(lambda p: 255 if p == 0 else 0)
        bbox = mask.getbbox()
        self.assertIsNotNone(bbox)
        ink_w = bbox[2] - bbox[0]
        ink_h = bbox[3] - bbox[1]
        self.assertGreater(max(ink_w, ink_h), 230)
        self.assertGreater(min(ink_w, ink_h), 150)
        self.assertGreater(ink_w, ink_h)

    def test_layout_rotate_270_keeps_reading_order(self):
        from PIL import Image, ImageDraw

        from t50.raster import _unpack_1bpp_msb

        # Tall frame keeps the crop portrait; ROTATE_270 puts the top blob
        # on the right, then FLIP_LEFT_RIGHT moves it to the left.
        img = Image.new("L", (80, 200), 255)
        draw = ImageDraw.Draw(img)
        draw.rectangle([1, 1, 78, 198], outline=0, width=2)
        draw.rectangle([20, 8, 60, 28], fill=0)
        bits, w, h = layout_on_tape(img, 320, 240)
        bw = _unpack_1bpp_msb(bits, w, h)
        px = bw.load()
        left = right = 0
        for y in range(h):
            for x in range(w):
                if px[x, y] != 0:
                    continue
                if x >= w / 2:
                    right += 1
                else:
                    left += 1
        self.assertGreater(left, right)

    def test_layout_does_not_rotate_landscape_that_fits(self):
        from PIL import Image, ImageDraw

        from t50.raster import _unpack_1bpp_msb

        img = Image.new("L", (280, 200), 255)
        ImageDraw.Draw(img).rectangle([10, 10, 269, 189], fill=0)
        bits, w, h = layout_on_tape(img, 320, 240)
        self.assertEqual((w, h), (320, 240))
        bw = _unpack_1bpp_msb(bits, w, h)
        mask = bw.point(lambda p: 255 if p == 0 else 0)
        bbox = mask.getbbox()
        self.assertIsNotNone(bbox)
        ink_w = bbox[2] - bbox[0]
        ink_h = bbox[3] - bbox[1]
        self.assertGreater(ink_w, ink_h)
        self.assertGreater(ink_w, 240)
        self.assertGreater(ink_h, 160)

    def test_antialiased_gray_becomes_black(self):
        from PIL import Image

        from t50.raster import _l_to_1bit

        img = Image.new("L", (32, 32), 255)
        for y in range(8, 24):
            for x in range(8, 24):
                img.putpixel((x, y), 170)
        bw = _l_to_1bit(img)
        self.assertEqual(bw.getpixel((16, 16)), 0)


class BufferTests(unittest.TestCase):
    def test_page_reg_bits(self):
        bits = build_page_reg_bits(nodu=4, mat=1)
        self.assertEqual(bits[0], 0x00)
        self.assertEqual(bits[1], 0x50)
        bits = build_page_reg_bits(page_st=True, page_end=True, prt_end=True, nodu=4, mat=1)
        self.assertEqual(bits[0], 0x0E)
        self.assertEqual(bits[1], 0x50)

    def test_print_buffer_checksum(self):
        data = bytes(84 * 48)
        buf = build_print_buffer(
            data, 48, 84,
            page_st=True, page_end=True, prt_end=True,
            margin_top=8, margin_bottom=8, density=4,
        )
        self.assertEqual(buf[6], 48)
        self.assertEqual(buf[4], 84)
        self.assertEqual(buf[8], 8)
        self.assertEqual(buf[12], 4)
        chk = buf[0] | (buf[1] << 8)
        self.assertGreater(chk, 0)

    def test_split_into_buffers(self):
        image = bytes(240 * 48)
        bufs = split_into_buffers(image, 48, 240, 8, 8, 4)
        self.assertEqual(len(bufs), 3)


class CompressTests(unittest.TestCase):
    def test_lzma_header(self):
        compressed = compress_lzma(bytes(4096))
        self.assertEqual(compressed[0], 0x5D)
        self.assertEqual(compressed[1:5], (8192).to_bytes(4, "little"))
        self.assertEqual(compressed[5:13], (4096).to_bytes(8, "little"))

    def test_calc_speed(self):
        self.assertEqual(calc_speed(4000), 10)
        self.assertEqual(calc_speed(3000), 15)
        self.assertEqual(calc_speed(100), 60)


if __name__ == "__main__":
    unittest.main()
