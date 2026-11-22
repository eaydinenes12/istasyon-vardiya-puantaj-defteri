import json
import tempfile
import unittest
from pathlib import Path

from validate_build_inputs import REQUIRED_FILES, validate_build_inputs


class BuildValidationTests(unittest.TestCase):
    def test_isolated_application_has_all_packaging_inputs(self):
        self.assertEqual(validate_build_inputs(Path(__file__).resolve().parents[1]), [])

    def test_missing_manifest_template_blocks_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative_path in REQUIRED_FILES:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"placeholder")
            manifest = root / "templates" / "forms" / "manifest.json"
            manifest.write_text(json.dumps([{
                "id": "missing", "file": "missing.docx", "extension": "docx",
            }]), encoding="utf-8")

            self.assertEqual(
                validate_build_inputs(root),
                ["Form şablonu eksik: templates/forms/missing.docx"],
            )

    def test_manifest_rejects_paths_outside_form_template_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative_path in REQUIRED_FILES:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"placeholder")
            manifest = root / "templates" / "forms" / "manifest.json"
            manifest.write_text(json.dumps([{
                "id": "outside", "file": "..\\outside.docx", "extension": "docx",
            }]), encoding="utf-8")

            self.assertIn(
                "Form manifest kaydı 1 için dosya adı veya uzantı geçersiz.",
                validate_build_inputs(root),
            )


if __name__ == "__main__":
    unittest.main()
