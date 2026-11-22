from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pypdfium2 as pdfium


_OCR_PROBE = (
    "Add-Type -AssemblyName System.Runtime.WindowsRuntime;"
    "[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null;"
    "[Windows.Globalization.Language, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null;"
    "if ([Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('tr'))) { 'yes' } else { 'no' }"
)
_ocr_available: bool | None = None


def turkish_ocr_available() -> bool:
    """Whether Windows' offline Turkish OCR engine can be created (cached)."""
    global _ocr_available
    if _ocr_available is None:
        _ocr_available = False
        if os.name == "nt":
            powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            try:
                result = subprocess.run(
                    [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _OCR_PROBE],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                _ocr_available = result.stdout.strip().endswith(b"yes")
            except (OSError, subprocess.SubprocessError):
                _ocr_available = False
    return _ocr_available


def recognize_pdf_pages(data: bytes, page_indices: list[int]) -> list[list[tuple[str, ...]]]:
    return _recognize_pdf_pages(data, page_indices, include_evidence=False)


def recognize_pdf_pages_with_evidence(data: bytes, page_indices: list[int]) -> dict:
    return _recognize_pdf_pages(data, page_indices, include_evidence=True)


def _recognize_pdf_pages(data: bytes, page_indices: list[int], include_evidence: bool):
    if os.name != "nt":
        raise ValueError("Taranmış PDF için Windows Türkçe çevrimdışı metin tanıma gereklidir.")
    if len(page_indices) > 20:
        raise ValueError("Bir dosyada en fazla 20 taranmış sayfa okunabilir.")
    pdf = pdfium.PdfDocument(data)
    try:
        with tempfile.TemporaryDirectory(prefix="vardiya-ocr-") as directory:
            paths = []
            for index in page_indices:
                page = pdf[index]
                try:
                    width, height = page.get_size()
                    if width <= 0 or height <= 0:
                        raise ValueError(f"{index + 1}. PDF sayfasının boyutu geçersiz.")
                    scale = min(3.0, 3500 / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        path = Path(directory) / f"page-{index + 1}.png"
                        bitmap.to_pil().save(path)
                        paths.append(str(path))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            script = (Path(__file__).resolve().parent / "ocr_windows.ps1").read_text(encoding="utf-8")
            command = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            try:
                result = subprocess.run(
                    [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                    input=json.dumps({"images": paths}, ensure_ascii=False).encode("utf-8"),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=max(60, len(paths) * 15),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("Taranmış PDF okunurken zaman aşımı oluştu. Daha az sayfa deneyin.") from exc
            if result.returncode:
                raise ValueError("Taranmış PDF okunamadı. Windows'ta Türkçe metin tanıma dilinin kurulu olduğunu ve dosyanın okunaklı olduğunu kontrol edin.")
            pages = json.loads(result.stdout.decode("utf-8"))["pages"]
            if len(pages) != len(page_indices):
                raise ValueError("PDF sayfalarının tümü okunamadı.")
            if not include_evidence:
                return [_lines_to_rows(page) for page in pages]
            from PIL import Image

            tables = []
            evidence = []
            for page_index, lines, image_path in zip(page_indices, pages, paths):
                rows = _lines_to_rows(lines, include_regions=True)
                tables.append([row["cells"] for row in rows])
                with Image.open(image_path) as page_image:
                    for row_index, row in enumerate(rows, 1):
                        image = _crop_ocr_region(page_image, row["bounds"])
                        evidence.append({
                            "page": page_index + 1,
                            "row": row_index,
                            "text": " | ".join(row["cells"]),
                            "image": image,
                        })
                        if len(evidence) > 1000:
                            raise ValueError("Görüntü kanıtıyla en fazla 1000 OCR satırı gösterilebilir. Dosyayı daha küçük parçalara bölüp yeniden deneyin.")
            return {"tables": tables, "evidence": evidence}
    finally:
        pdf.close()


def _crop_ocr_region(image, bounds) -> str:
    from PIL import Image

    left, top, right, bottom = bounds
    padding = max(10, int((bottom - top) * 0.35))
    box = (
        max(0, int(left) - padding),
        max(0, int(top) - padding),
        min(image.width, int(right + 0.999) + padding),
        min(image.height, int(bottom + 0.999) + padding),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("OCR alanının görüntü konumu geçersiz.")
    region = image.crop(box)
    try:
        region.thumbnail((1400, 360))
        output = io.BytesIO()
        region.save(output, format="JPEG", quality=76, optimize=True)
    finally:
        region.close()
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _lines_to_rows(lines: list, include_regions: bool = False) -> list:
    grouped = []
    for word in sorted((word for line in lines for word in line), key=lambda item: item["y"]):
        middle = word["y"] + word["height"] / 2
        matching = next((line for line in grouped
                         if abs(line["middle"] - middle) <= max(line["height"], word["height"]) * 0.6), None)
        if matching is None:
            matching = {"middle": middle, "height": word["height"], "words": []}
            grouped.append(matching)
        matching["words"].append(word)
    rows = []
    for line in grouped:
        cells = []
        row_words = []
        current = ""
        previous = None

        def finish_cell():
            nonlocal current
            if current:
                cells.append(current)
            current = ""

        for word in sorted(line["words"], key=lambda item: item["x"]):
            if previous is not None:
                gap = word["x"] - (previous["x"] + previous["width"])
                if gap > max(12, 1.25 * max(word["height"], previous["height"])):
                    finish_cell()
            current = (current + " " + word["text"]).strip()
            row_words.append(word)
            previous = word
        finish_cell()
        if cells:
            if include_regions:
                rows.append({
                    "cells": tuple(cells),
                    "bounds": (
                        min(word["x"] for word in row_words),
                        min(word["y"] for word in row_words),
                        max(word["x"] + word["width"] for word in row_words),
                        max(word["y"] + word["height"] for word in row_words),
                    ),
                })
            else:
                rows.append(tuple(cells))
    return rows
