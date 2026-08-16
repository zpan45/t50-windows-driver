import unittest

from t50.ipp import (
    OP_GET_PRINTER_ATTRIBUTES,
    OP_PRINT_JOB,
    TAG_CHARSET,
    TAG_LANGUAGE,
    TAG_MIME,
    TAG_URI,
    build_ipp,
    parse_ipp,
)


class IppTests(unittest.TestCase):
    def test_ipp_roundtrip_get_printer_attributes(self):
        req = build_ipp(
            (2, 0),
            OP_GET_PRINTER_ATTRIBUTES,
            1,
            [
                (
                    0x01,
                    [
                        (TAG_CHARSET, "attributes-charset", "utf-8"),
                        (TAG_LANGUAGE, "attributes-natural-language", "en"),
                        (TAG_URI, "printer-uri", "ipp://127.0.0.1:8631/ipp/print"),
                        (TAG_MIME, "document-format", "image/pwg-raster"),
                    ],
                )
            ],
        )
        parsed = parse_ipp(req)
        self.assertEqual(parsed.operation, OP_GET_PRINTER_ATTRIBUTES)
        self.assertEqual(parsed.request_id, 1)
        self.assertEqual(parsed.attributes["attributes-charset"], ["utf-8"])
        self.assertTrue(parsed.attributes["printer-uri"][0].endswith("/ipp/print"))

    def test_encode_range_and_resolution(self):
        from t50.ipp import TAG_RANGE, TAG_RESOLUTION, encode_attr
        raw = encode_attr(TAG_RANGE, "copies-supported", (1, 99))
        self.assertEqual(raw[0], TAG_RANGE)
        raw = encode_attr(TAG_RESOLUTION, "printer-resolution-default", (203, 203, 3))
        self.assertEqual(raw[0], TAG_RESOLUTION)
        self.assertGreater(len(raw), 9)
        header = build_ipp(
            (2, 0),
            OP_PRINT_JOB,
            7,
            [
                (
                    0x01,
                    [
                        (TAG_CHARSET, "attributes-charset", "utf-8"),
                        (TAG_LANGUAGE, "attributes-natural-language", "en"),
                        (TAG_MIME, "document-format", "image/png"),
                    ],
                )
            ],
        )
        parsed = parse_ipp(header + b"PNGDATA")
        self.assertEqual(parsed.operation, OP_PRINT_JOB)
        self.assertEqual(parsed.document, b"PNGDATA")
        self.assertEqual(parsed.attributes["document-format"], ["image/png"])

    def test_read_chunked_body(self):
        import io

        from t50.ipp import read_chunked_body

        payload = b"RaS2" + b"\x00" * 20
        rest = payload[5:]
        chunked = b"5\r\n" + payload[:5] + b"\r\n" + f"{len(rest):X}\r\n".encode() + rest + b"\r\n0\r\n\r\n"
        self.assertEqual(read_chunked_body(io.BytesIO(chunked)), payload)


if __name__ == "__main__":
    unittest.main()
