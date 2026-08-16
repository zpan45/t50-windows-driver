import unittest

from t50.winpaper import display_name, option_keyword, patch_gpd_text, patch_pdc_text


GPD = """*MasterUnits: PAIR(457200, 457200)
*Feature: PaperSize
{
*rcNameID: =PAPER_SIZE_DISPLAY
*PrintSchemaKeywordMap: "PageMediaSize"
*DefaultOption: A4
*Option: A4
{
*rcNameID: =RCID_DMPAPER_SYSTEM_NAME
*PrintSchemaKeywordMap: "ISOA4"
}
*Option: LETTER
{
*PrintSchemaKeywordMap: "NorthAmericaLetter"
}
}
*Feature: PageBorderless
{
*DefaultOption: None
}
"""

PDC = """    <psf2:CapabilitiesChangeID xsi:type="xsd:string">111</psf2:CapabilitiesChangeID>
    <psk:PageMediaSize psf2:psftype="Feature">
        <psk:ISOA4 psf2:psftype="Option" psf2:default="false">
            <psk:MediaSizeWidth xsi:type="xsd:integer" psf2:psftype="ScoredProperty">210000</psk:MediaSizeWidth>
            <psk:MediaSizeHeight xsi:type="xsd:integer" psf2:psftype="ScoredProperty">297000</psk:MediaSizeHeight>
        </psk:ISOA4>
        <psk:NorthAmericaLetter psf2:psftype="Option" psf2:default="false">
            <psk:MediaSizeWidth xsi:type="xsd:integer" psf2:psftype="ScoredProperty">215900</psk:MediaSizeWidth>
            <psk:MediaSizeHeight xsi:type="xsd:integer" psf2:psftype="ScoredProperty">279400</psk:MediaSizeHeight>
        </psk:NorthAmericaLetter>
    </psk:PageMediaSize>
"""


class WinPaperTests(unittest.TestCase):
    def test_keyword(self):
        self.assertEqual(option_keyword(40, 30), "T50_40x30")
        self.assertEqual(display_name(40, 30), "40 x 30 mm")

    def test_patch_gpd_replaces_a4_default(self):
        out = patch_gpd_text(GPD, 40, 30)
        self.assertIn("*DefaultOption: T50_40x30", out)
        self.assertIn('*Name: "40 x 30 mm"', out)
        self.assertIn("*PageDimensions: PAIR(720000, 540000)", out)
        self.assertIn("*Option: A4", out)
        self.assertIn("*Option: LETTER", out)
        self.assertNotIn("*DefaultOption: A4", out)
        self.assertIn("*Feature: PageBorderless", out)

    def test_patch_pdc_uses_microns(self):
        out = patch_pdc_text(PDC, 40, 30)
        self.assertIn("<ns0000:T50_40x30", out)
        self.assertIn(">40000</psk:MediaSizeWidth>", out)
        self.assertIn(">30000</psk:MediaSizeHeight>", out)
        self.assertIn("ISOA4", out)
        self.assertIn("psf2:default=\"true\"", out)
        self.assertNotIn(">111<", out)


if __name__ == "__main__":
    unittest.main()
