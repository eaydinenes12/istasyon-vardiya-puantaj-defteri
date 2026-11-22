from __future__ import annotations

import base64
import binascii
import html
import json
import re
import uuid
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parent / "templates" / "forms"
MAX_BYTES = 5_000_000
TYPES = {"G", "S", "Ü", "GENEL"}
TOKEN_PATTERN = re.compile(
    r"\{\{(personel|bolum|aciklama|baslangic|bitis|aralik|donem|tarih|durum|olay_saati)\}\}"
    r"|\[Olay saati\]"
)


def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS form_templates (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
        extension TEXT NOT NULL, content BLOB NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('draft','active','retired'))
    )""")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(form_templates)")}
    if "status" not in columns:
        connection.execute(
            "ALTER TABLE form_templates ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
    connection.execute("BEGIN IMMEDIATE")
    try:
        initialized = connection.execute(
            "SELECT 1 FROM app_settings WHERE name='form_templates_initialized'"
        ).fetchone()
        if not initialized:
            for item in json.loads((ROOT / "manifest.json").read_text(encoding="utf-8")):
                connection.execute(
                    """INSERT OR IGNORE INTO form_templates
                       (id,name,category,extension,content,status) VALUES (?, ?, ?, ?, ?, 'active')""",
                    (item["id"], item["name"], item["category"], item["extension"],
                     (ROOT / item["file"]).read_bytes()),
                )
            connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('form_templates_initialized', 'true')"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def list_templates(connection):
    return [dict(row) for row in connection.execute(
        "SELECT id, name, category, extension, status FROM form_templates ORDER BY name COLLATE NOCASE"
    )]


def get_template(connection, template_id):
    if not isinstance(template_id, str):
        raise ValueError("Form seçimi geçersiz.")
    row = connection.execute("SELECT * FROM form_templates WHERE id=?", (template_id,)).fetchone()
    if row is None:
        raise ValueError("Form şablonu bulunamadı.")
    return row


def validate_content(extension, content):
    if extension not in ("docx", "xlsx"):
        raise ValueError("Yalnızca .docx veya .xlsx dosyaları desteklenir.")
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Form dosyası boş veya 5 MB sınırından büyük.")
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 50_000_000:
                raise ValueError("Formun açılmış boyutu 50 MB sınırını aşıyor.")
        if extension == "docx":
            Document(BytesIO(content))
        else:
            workbook = load_workbook(BytesIO(content))
    except (BadZipFile, KeyError, ValueError, OSError) as exc:
        raise ValueError("Dosya geçerli bir Word veya Excel belgesi değil.") from exc
    if extension == "xlsx" and any(sheet.max_row > 5000 or sheet.max_column > 150 for sheet in workbook):
        raise ValueError("Excel şablonu en fazla 5000 satır ve 150 sütun içerebilir.")


def save_template(connection, payload):
    if set(payload) != {"id", "name", "category", "filename", "content"}:
        raise ValueError("Form bilgisi geçersiz.")
    template_id, name, category = payload["id"], payload["name"], payload["category"]
    if not isinstance(template_id, str) or not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ValueError("Form adı 1-100 karakter olmalıdır.")
    if not isinstance(category, str) or category not in TYPES or not isinstance(payload["filename"], str):
        raise ValueError("Form türü veya dosya adı geçersiz.")
    extension = Path(payload["filename"]).suffix.lower().lstrip(".")
    try:
        content = base64.b64decode(payload["content"], validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("Form dosyası okunamadı.") from exc
    validate_content(extension, content)
    if template_id:
        get_template(connection, template_id)
        with connection:
            connection.execute(
                "UPDATE form_templates SET name=?,category=?,extension=?,content=? WHERE id=?",
                (name.strip(), category, extension, content, template_id),
            )
    else:
        template_id = uuid.uuid4().hex
        with connection:
            connection.execute(
                """INSERT INTO form_templates (id,name,category,extension,content,status)
                   VALUES (?, ?, ?, ?, ?, 'draft')""",
                (template_id, name.strip(), category, extension, content),
            )
    return template_id


def set_template_status(connection, template_id, status):
    row = get_template(connection, template_id)
    if status not in ("active", "retired"):
        raise ValueError("Şablon durumu geçersiz.")
    if status == row["status"]:
        return status
    transitions = {"draft": "active", "active": "retired", "retired": None}
    if transitions.get(row["status"]) != status:
        raise ValueError("Şablon durumu yalnız taslaktan kullanıma, sonra kullanımdan kaldırılabilir.")
    with connection:
        connection.execute("UPDATE form_templates SET status=? WHERE id=?", (status, template_id))
    return status


def delete_template(connection, template_id):
    get_template(connection, template_id)
    with connection:
        connection.execute("DELETE FROM form_templates WHERE id=?", (template_id,))


def _replace_paragraph(paragraph, values):
    # Placeholders can span runs; retain the formatting of the first matching run.
    runs = paragraph.runs
    text = "".join(run.text for run in runs)
    for match in reversed(list(TOKEN_PATTERN.finditer(text))):
        start, end = match.span()
        cursor = 0
        first = None
        for run in runs:
            run_start, run_end = cursor, cursor + len(run.text)
            cursor = run_end
            if run_end <= start or run_start >= end:
                continue
            before = run.text[:max(0, start - run_start)]
            after = run.text[max(0, end - run_start):]
            value = values[match.group(1) or "olay_saati"]
            if first is None:
                first = run
                run.text = before + value + after
            else:
                run.text = before + after


def _paragraphs(container):
    yield from container.paragraphs
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from _paragraphs(cell)


def _template_tokens(template):
    found = set()
    if template["extension"] == "docx":
        doc = Document(BytesIO(template["content"]))
        text = "\n".join(paragraph.text for paragraph in _paragraphs(doc))
        for section in doc.sections:
            for part in (section.header, section.footer):
                text += "\n" + "\n".join(paragraph.text for paragraph in _paragraphs(part))
    else:
        workbook = load_workbook(BytesIO(template["content"]), data_only=False)
        text = "\n".join(
            str(cell.value)
            for sheet in workbook
            for row in sheet
            for cell in row
            if cell.data_type == "s" and isinstance(cell.value, str)
        )
    found.update(match.group(1) or "olay_saati" for match in TOKEN_PATTERN.finditer(text))
    return found


def generate(template, person, start_text, end_text, category, section="", description="", event_time=""):
    if "status" in template.keys() and template["status"] != "active":
        raise ValueError("Yalnız kullanıma açık form şablonlarıyla belge oluşturulabilir.")
    if category not in ("G", "S", "Ü"):
        raise ValueError("Puantaj durumu geçersiz.")
    if template["category"] not in (category, "GENEL"):
        raise ValueError("Seçilen form bu durum için tanımlı değil.")
    if not isinstance(person, str) or not person.strip() or len(person) > 80:
        raise ValueError("Personel bilgisi geçersiz.")
    if not isinstance(section, str) or len(section) > 80:
        raise ValueError("Bölüm bilgisi en fazla 80 karakter olabilir.")
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError("Açıklama en fazla 500 karakter olabilir.")
    if not isinstance(event_time, str) or (event_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", event_time)):
        raise ValueError("Olay saati SS:DD biçiminde olmalıdır.")
    required = _template_tokens(template)
    if "bolum" in required and not section.strip():
        raise ValueError("Şablonda bölüm alanı kullanılıyor; bölüm bilgisini doldurun.")
    if "aciklama" in required and not description.strip():
        raise ValueError("Şablonda açıklama alanı kullanılıyor; açıklamayı doldurun.")
    try:
        start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
    except (ValueError, TypeError) as exc:
        raise ValueError("Başlangıç ve bitiş tarihlerini seçin.") from exc
    if end < start:
        raise ValueError("Bitiş tarihi başlangıçtan önce olamaz.")
    first_date, last_date = start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y")
    date_range = first_date if start == end else f"{first_date} - {last_date}"
    values = {
        "personel": person.strip(), "bolum": section.strip(), "aciklama": description.strip(),
        "baslangic": first_date, "bitis": last_date,
        "aralik": date_range,
        "donem": f"{date_range} tarihinde" if start == end else f"{date_range} tarihleri arasında",
        "tarih": date.today().strftime("%d.%m.%Y"),
        "olay_saati": event_time,
        "durum": {"G": "İşe gelmedi", "S": "Yıllık izin", "Ü": "Ücretsiz izin"}[category],
    }
    output = BytesIO()
    if template["extension"] == "docx":
        doc = Document(BytesIO(template["content"]))
        for paragraph in _paragraphs(doc):
            _replace_paragraph(paragraph, values)
        for section in doc.sections:
            for part in (section.header, section.footer):
                for paragraph in _paragraphs(part):
                    _replace_paragraph(paragraph, values)
        doc.save(output)
    else:
        wb = load_workbook(BytesIO(template["content"]))
        for sheet in wb:
            for row in sheet:
                for cell in row:
                    if cell.data_type == "s" and isinstance(cell.value, str):
                        value = TOKEN_PATTERN.sub(
                            lambda match: values[match.group(1) or "olay_saati"], cell.value
                        )
                        if value != cell.value:
                            cell.value = value
                            cell.data_type = "s"
        wb.save(output)
    return output.getvalue()


def preview(content, extension):
    if extension == "docx":
        doc = Document(BytesIO(content))
        lines = [p.text for p in _paragraphs(doc) if p.text.strip()]
    else:
        workbook = load_workbook(BytesIO(content), data_only=False)
        lines = []
        for sheet in workbook:
            lines.append(f"[{sheet.title}]")
            for row in sheet:
                values = [str(cell.value) for cell in row if cell.value is not None and cell.data_type != "f"]
                if values:
                    lines.append(" | ".join(values))
    text = "\n".join(lines)
    if len(text) > 20000:
        return text[:20000] + "\n… (önizleme kısaltıldı)"
    return text or "Bu formda önizlenebilir metin bulunmuyor. Dosyayı kaydedip kontrol edin."


def _iter_blocks(container):
    element = getattr(container, "_tc", None)
    if element is None:
        root = container._element
        element = getattr(root, "body", root)
    for child in element.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, container)
        elif isinstance(child, CT_Tbl):
            yield Table(child, container)


def _docx_paragraph_html(paragraph):
    alignment = {
        0: "left", 1: "center", 2: "right", 3: "justify",
    }.get(paragraph.alignment, "left")
    runs = []
    for run in paragraph.runs:
        styles = []
        if run.bold:
            styles.append("font-weight:700")
        if run.italic:
            styles.append("font-style:italic")
        if run.underline:
            styles.append("text-decoration:underline")
        if run.font.size:
            styles.append(f"font-size:{min(72, max(4, run.font.size.pt)):.1f}pt")
        color = run.font.color.rgb
        if color and re.fullmatch(r"[0-9A-Fa-f]{6}", str(color)):
            styles.append(f"color:#{color}")
        text = html.escape(run.text).replace("\n", "<br>").replace("\t", "&emsp;")
        runs.append(f'<span style="{html.escape(";".join(styles), quote=True)}">{text}</span>')
    if not runs and paragraph.text:
        runs.append(html.escape(paragraph.text))
    return f'<p style="text-align:{alignment}">{"".join(runs) or "&nbsp;"}</p>'


def _docx_blocks_html(container):
    parts = []
    for block in _iter_blocks(container):
        if isinstance(block, Paragraph):
            parts.append(_docx_paragraph_html(block))
        else:
            rows = []
            for row in block.rows:
                cells = []
                for cell in row.cells:
                    cells.append(f"<td>{_docx_blocks_html(cell)}</td>")
                rows.append(f"<tr>{''.join(cells)}</tr>")
            parts.append(f'<table class="document-table"><tbody>{"".join(rows)}</tbody></table>')
    return "".join(parts)


def _xlsx_color(color):
    if color and color.type == "rgb" and color.rgb:
        value = str(color.rgb)[-6:]
        if re.fullmatch(r"[0-9A-Fa-f]{6}", value):
            return f"#{value}"
    return ""


def _xlsx_sheet_html(sheet):
    max_row, max_column = min(sheet.max_row, 60), min(sheet.max_column, 30)
    if max_row < 1 or max_column < 1:
        return "<p>Bu sayfada görüntülenecek hücre yok.</p>", False
    merges = {}
    covered = set()
    for merged in sheet.merged_cells.ranges:
        if merged.min_row <= max_row and merged.min_col <= max_column:
            merges[(merged.min_row, merged.min_col)] = (
                min(max_row, merged.max_row) - merged.min_row + 1,
                min(max_column, merged.max_col) - merged.min_col + 1,
            )
            covered.update(
                (row, column)
                for row in range(merged.min_row, min(max_row, merged.max_row) + 1)
                for column in range(merged.min_col, min(max_column, merged.max_col) + 1)
                if (row, column) != (merged.min_row, merged.min_col)
            )
    col_widths = [
        min(240, max(36, int((sheet.column_dimensions[sheet.cell(1, col).column_letter].width or 8) * 7)))
        for col in range(1, max_column + 1)
    ]
    rows = []
    for row_index in range(1, max_row + 1):
        cells = []
        for column in range(1, max_column + 1):
            if (row_index, column) in covered:
                continue
            cell = sheet.cell(row_index, column)
            value = "" if cell.data_type == "f" or cell.value is None else str(cell.value)
            style = [
                f"background:{_xlsx_color(cell.fill.fgColor)}" if cell.fill.fill_type == "solid" else "",
                f"color:{_xlsx_color(cell.font.color)}" if _xlsx_color(cell.font.color) else "",
                "font-weight:700" if cell.font.bold else "",
                "font-style:italic" if cell.font.italic else "",
                f"text-align:{cell.alignment.horizontal}" if cell.alignment.horizontal in ("left", "center", "right") else "",
                "vertical-align:middle" if cell.alignment.vertical == "center" else "",
                "white-space:pre-wrap" if cell.alignment.wrap_text else "white-space:nowrap",
                "border:1px solid #aeb8c2" if any(
                    getattr(cell.border, side).style for side in ("left", "right", "top", "bottom")
                ) else "",
            ]
            row_span, col_span = merges.get((row_index, column), (1, 1))
            spans = (f' rowspan="{row_span}"' if row_span > 1 else "")
            spans += (f' colspan="{col_span}"' if col_span > 1 else "")
            cells.append(
                f'<td{spans} style="{html.escape(";".join(item for item in style if item), quote=True)}">'
                f"{html.escape(value)}</td>"
            )
        height = sheet.row_dimensions[row_index].height
        row_style = f' style="height:{min(96, max(12, height)):.1f}pt"' if height else ""
        rows.append(f"<tr{row_style}>{''.join(cells)}</tr>")
    col_group = "".join(f'<col style="width:{width}px">' for width in col_widths)
    table = f'<table class="spreadsheet"><colgroup>{col_group}</colgroup><tbody>{"".join(rows)}</tbody></table>'
    truncated = sheet.max_row > max_row or sheet.max_column > max_column
    if truncated:
        table += "<p class=\"truncated\">Önizleme ilk 60 satır ve 30 sütunla sınırlıdır.</p>"
    return table, truncated


def preview_layout(content, extension):
    if extension == "docx":
        document = Document(BytesIO(content))
        section = document.sections[0]
        width = max(120, min(320, section.page_width / 36000))
        height = max(160, min(420, section.page_height / 36000))
        parts = [_docx_blocks_html(document)]
        for header_footer in (section.header, section.footer):
            rendered = _docx_blocks_html(header_footer)
            if rendered.strip():
                parts.append(f'<div class="page-note">{rendered}</div>')
        body = "".join(parts) or "<p>Bu belgede görüntülenecek metin yok.</p>"
        page = f'<main class="paper" style="width:{width:.1f}mm;min-height:{height:.1f}mm">{body}</main>'
    elif extension == "xlsx":
        workbook = load_workbook(BytesIO(content), data_only=False)
        sheets = []
        for sheet in workbook.worksheets[:8]:
            content_html, _ = _xlsx_sheet_html(sheet)
            sheets.append(
                f'<main class="paper spreadsheet-paper"><h1>{html.escape(sheet.title)}</h1>{content_html}</main>'
            )
        page = "".join(sheets) or "<p>Bu çalışma kitabında görüntülenecek sayfa yok.</p>"
        workbook.close()
    else:
        raise ValueError("Görsel önizleme yalnızca Word ve Excel belgelerinde kullanılabilir.")
    return (
        "<!doctype html><html lang=\"tr\"><head><meta charset=\"utf-8\"><style>"
        "html{background:#e8eeea;color:#172a21;font:10pt Arial,sans-serif}"
        "body{margin:0;padding:12px}.paper{box-sizing:border-box;margin:0 auto 12px;padding:15mm;"
        "background:#fff;box-shadow:0 2px 8px #183c2a22;overflow:hidden}"
        "p{margin:0 0 8pt;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.35}"
        ".document-table,.spreadsheet{border-collapse:collapse;max-width:100%;font-size:9pt}"
        ".document-table td,.spreadsheet td{border:1px solid #aeb8c2;padding:4px;vertical-align:top}"
        ".spreadsheet-paper{width:max-content;max-width:100%;padding:8mm;overflow:auto}"
        ".spreadsheet td{min-width:36px;max-width:240px;overflow-wrap:anywhere}"
        ".spreadsheet-paper h1{font-size:12pt}.page-note{margin-top:8mm;font-size:9pt}"
        ".truncated{margin-top:6px;color:#42584a;font-size:8pt}"
        "@media print{html{background:#fff}body{padding:0}.paper{margin:0 auto;box-shadow:none;"
        "page-break-after:always}.paper:last-child{page-break-after:auto}}"
        "</style></head><body>"
        f"{page}</body></html>"
    )
