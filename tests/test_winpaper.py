import unittest

from t50.winpaper import (
    display_name,
    form_option_keyword,
    label_form_name,
    option_keyword,
    patch_gpd_customsize,
    patch_gpd_forms,
    patch_gpd_text,
    patch_pdc_text,
)


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

    def test_patch_gpd_customsize_inserts_option(self):
        out = patch_gpd_customsize(GPD)
        self.assertIn("*Option: CUSTOMSIZE", out)
        self.assertIn("*rcNameID: =USER_DEFINED_SIZE_DISPLAY", out)
        self.assertIn("*MinSize: PAIR(180000, 180000)", out)
        self.assertIn("*MaxSize: PAIR(864000, 5400000)", out)
        self.assertIn("*MaxPrintableWidth: 864000", out)
        self.assertIn("*Option: A4", out)
        self.assertIn("*Option: LETTER", out)

    def test_patch_gpd_customsize_idempotent(self):
        once = patch_gpd_customsize(GPD)
        twice = patch_gpd_customsize(once)
        self.assertEqual(once.count("*Option: CUSTOMSIZE"), 1)
        self.assertEqual(once, twice)

    def test_form_option_keyword(self):
        self.assertEqual(form_option_keyword("Supvan Label"), "FORM_Supvan_Label")
        self.assertEqual(label_form_name(40, 30), "T50 40x30 mm")
        self.assertEqual(
            form_option_keyword("Supval Label(40mmX30mm)"),
            "FORM_Supval_Label_40mmX30mm",
        )

    def test_patch_gpd_forms_uses_form_name(self):
        out = patch_gpd_forms(GPD, [("T50 40x30 mm", 40, 30)])
        self.assertIn("*Option: FORM_T50_40x30_mm", out)
        self.assertIn('*Name: "T50 40x30 mm"', out)
        self.assertIn("*PageDimensions: PAIR(720000, 540000)", out)
        self.assertIn("*DefaultOption: FORM_T50_40x30_mm", out)
        self.assertNotIn("*Option: A4", out)
        self.assertNotIn("*Option: LETTER", out)
        self.assertNotIn("*Option: CUSTOMSIZE", out)
        self.assertIn("*Feature: PageBorderless", out)

    def test_patch_gpd_forms_replaces_customsize(self):
        with_custom = patch_gpd_customsize(GPD)
        out = patch_gpd_forms(with_custom, [("T50 40x30 mm", 40, 30)])
        self.assertEqual(out.count("*Option: CUSTOMSIZE"), 0)
        self.assertEqual(out.count("*Option: FORM_T50_40x30_mm"), 1)
        self.assertNotIn("*Option: A4", out)


if __name__ == "__main__":
    unittest.main()
