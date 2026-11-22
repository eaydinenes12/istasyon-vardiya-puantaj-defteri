"""Open an isolated export in Excel and read the holiday labels from its monochrome PDF."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

from pypdf import PdfReader

def run(tree, folder):
    sys.path.insert(0, str(tree))
    from scheduling import export_attendance_xlsx

    xlsx, pdf = (folder / name for name in ("attendance.xlsx", "attendance.pdf"))
    export_attendance_xlsx(xlsx, "2026-05", {"rows": [{"employee_id": 1, "name": "Print Fixture", "codes": {}}]})
    command = r"""
$ErrorActionPreference = 'Stop'
$excel = New-Object -ComObject Excel.Application
$book = $null
try {
    if ($excel.Workbooks.Count -ne 0 -or $excel.UserControl) {throw 'Not an isolated Excel instance'}
    $excel.DisplayAlerts = $false
    $excel.EnableEvents = $false
    $excel.AutomationSecurity = 3
    $book = $excel.Workbooks.Open($env:INPUT_XLSX, 0, $true, [Type]::Missing, [Type]::Missing,
        [Type]::Missing, $true, [Type]::Missing, [Type]::Missing, $false, $false, [Type]::Missing, $false)
    $sheet = $book.Worksheets.Item(1)
    $sheet.PageSetup.BlackAndWhite = $true
    $report = [ordered]@{version=$excel.Version; official=$sheet.Cells.Item(2,21).Text;
        religious=$sheet.Cells.Item(2,29).Text; weekday=$sheet.Cells.Item(2,20).Text}
    $sheet.ExportAsFixedFormat(0, $env:OUTPUT_PDF)
    $report | ConvertTo-Json -Compress
} finally {
    if ($null -ne $book) { $book.Close($false) }
    if (-not $excel.UserControl -and $excel.Workbooks.Count -eq 0) { $excel.Quit() }
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)
}
"""
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                               capture_output=True, text=True, timeout=80,
                               env={**os.environ, "INPUT_XLSX": str(xlsx), "OUTPUT_PDF": str(pdf)})
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    result = json.loads(completed.stdout)
    assert [result[key] for key in ("official", "religious", "weekday")] == ["19R", "27D", "18"], result
    with pdf.open("rb") as stream:
        reader = PdfReader(stream)
        text = " ".join((p.extract_text() or "") for p in reader.pages)
        for label in ("19R", "27D", "28D", "29D", "30D", "R: Resmî tatil", "D: Dinî bayram", "Print Fixture"):
            assert re.sub(r"\s", "", label) in re.sub(r"\s", "", text), (label, text[:2800])
        result.update(pages=len(reader.pages), black_and_white=True, physical_printer=False)
    return result


if __name__ == "__main__":
    tree = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix="print-acceptance-") as folder:
        result = run(tree, Path(folder))
    print(json.dumps(result))
