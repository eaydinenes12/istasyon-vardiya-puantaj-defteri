from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from datetime import date, datetime, time
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

import scheduling
import ocr_windows


def _key(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").upper())
    return re.sub(r"[^A-Z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)))


def _date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = re.sub(r"\s*([./-])\s*", r"\1", str(value or "").strip())
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Tarih anlaşılamadı: {text[:50]}. Gün.ay.yıl kullanın.")


def _time(value) -> str:
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?::00)?", text)
    if match and int(match[1]) == 24 and match[2] == "00":
        return "00:00"
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError(f"Saat anlaşılamadı: {text[:50]}. SS:DD kullanın.")
    return f"{int(match[1]):02}:{match[2]}"


_KINDS = {
    "H": "weekly_off", "HAFTATATILI": "weekly_off", "HAFTALIKIZINLI": "weekly_off",
    "S": "annual_leave", "SENELIKIZINLI": "annual_leave", "YILLIKIZIN": "annual_leave",
    "R": "sick_leave", "RAPOR": "sick_leave", "RAPORLU": "sick_leave",
    "U": "unpaid_leave", "UCRETSIZIZIN": "unpaid_leave", "UCRETSIZIZINLI": "unpaid_leave",
    "GC": "overnight_exit", "GECEDENCIKIS": "overnight_exit",
    "E": "marriage_leave", "EVLILIKIZNI": "marriage_leave",
    "K": "quarantine", "KARANTINA": "quarantine",
    "Y": "half_day", "YARIMGUN": "half_day",
    "G": "absent", "ISEGELMEDI": "absent", "IST": "resignation", "ISTIFA": "resignation",
}


def _day(start, end, status) -> dict:
    if isinstance(status, str) and not start and not end:
        interval = re.fullmatch(r"\s*(\d{1,2}[:.]\d{2})(?:\s*[-–—]\s*|\s+)(\d{1,2}[:.]\d{2})\s*", status)
        if interval:
            start, end = interval.groups()
            status = ""
    kind = _key(status)
    if kind in ("", "X", "CALISMA", "MESAİ", "MESAI"):
        if not start or not end:
            raise ValueError("Çalışma kaydında giriş ve çıkış saatleri birlikte olmalıdır.")
        first, last = _time(start), _time(end)
        if first == last:
            raise ValueError("Giriş ve çıkış saatleri aynı olamaz.")
        return {"kind": "work", "start": first, "end": last}
    if kind not in _KINDS:
        raise ValueError(f"Vardiya durumu tanınmadı: {status}.")
    if start or end:
        raise ValueError("İzin/durum kaydında çalışma saati bulunmamalıdır.")
    return {"kind": _KINDS[kind]}


def _tables(
    data: bytes, extension: str, force_ocr: bool = False,
    ocr_pages: list[int] | None = None, ocr_evidence: list[dict] | None = None,
):
    if extension in (".xlsx", ".docx"):
        try:
            with ZipFile(BytesIO(data)) as archive:
                if sum(info.file_size for info in archive.infolist()) > 40_000_000:
                    raise ValueError("Dosyanın açılmış boyutu çok büyük.")
        except BadZipFile as exc:
            raise ValueError("Dosya geçerli bir Excel veya Word belgesi değil.") from exc
    if extension == ".xlsx":
        wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
        try:
            if len(wb.worksheets) > 40:
                raise ValueError("Çalışma sayfası sayısı çok fazla.")
            if any(ws.max_row > 5000 or ws.max_column > 80 for ws in wb.worksheets):
                raise ValueError("Her Excel sayfası en fazla 5000 satır ve 80 sütun içerebilir.")
            return [list(ws.iter_rows(min_row=1, max_row=min(ws.max_row, 5000),
                                      max_col=min(ws.max_column, 80), values_only=True))
                    for ws in wb.worksheets]
        finally:
            wb.close()
    if extension == ".docx":
        document = Document(BytesIO(data))
        if any(len(table.rows) > 5000 for table in document.tables):
            raise ValueError("Her Word tablosu en fazla 5000 satır içerebilir.")
        tables = [[tuple(cell.text for cell in row.cells) for row in table.rows[:5000]]
                  for table in document.tables]
        if not tables:
            tables = [[tuple(re.split(r"\s*\|\s*|\t+|\s{2,}", p.text.strip()))
                       for p in document.paragraphs if p.text.strip()]]
        return tables
    if extension == ".pdf":
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Şifreli PDF içe aktarılamaz.")
        if len(reader.pages) > 50:
            raise ValueError("PDF en fazla 50 sayfa olabilir.")
        tables = []
        scanned = []
        for page in reader.pages:
            text = page.extract_text(extraction_mode="layout") or ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if force_ocr or not lines:
                scanned.append(len(tables))
                tables.append([])
            else:
                tables.append([tuple(re.split(r"\s*\|\s*|\t+|\s{2,}", line)) for line in lines])
        if scanned:
            if ocr_pages is not None:
                ocr_pages.extend(scanned)
            if ocr_evidence is not None:
                recognized_pages = ocr_windows.recognize_pdf_pages_with_evidence(data, scanned)
                ocr_evidence.extend(recognized_pages["evidence"])
                recognized_tables = recognized_pages["tables"]
            else:
                recognized_tables = ocr_windows.recognize_pdf_pages(data, scanned)
            for index, recognized in zip(scanned, recognized_tables):
                tables[index] = recognized
        return tables
    raise ValueError("Yalnızca .xlsx, .docx veya .pdf dosyaları desteklenir.")


def _decode_document(filename: str, encoded: str) -> tuple[bytes, str]:
    if not isinstance(filename, str) or not isinstance(encoded, str):
        raise ValueError("Dosya bilgisi geçersiz.")
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Dosya içeriği okunamadı.") from exc
    if not data or len(data) > 5_000_000:
        raise ValueError("Dosya 5 MB sınırını aşmamalıdır.")
    return data, extension


def extract_document(filename: str, encoded: str, force_ocr: bool = False) -> dict:
    data, extension = _decode_document(filename, encoded)
    ocr_pages = []
    ocr_evidence = []
    tables = _tables(data, extension, force_ocr, ocr_pages, ocr_evidence)
    result = []
    for table in tables:
        rows = []
        for row in table:
            rows.append([
                value.date().isoformat() if isinstance(value, datetime) else
                value.isoformat() if isinstance(value, date) else
                value.strftime("%H:%M") if isinstance(value, time) else
                str("" if value is None else value).replace("\t", " ").replace("\n", " ").replace("|", " ")
                for value in row
            ])
        result.append(rows)
    if sum(len(row) for table in result for row in table) > 100_000:
        raise ValueError("Belge çok büyük; elle eşleştirme için en fazla 100.000 hücre kullanılabilir.")
    if not any(cell.strip() for table in result for row in table for cell in row):
        raise ValueError("Belgeden metin okunamadı. Görüntüyü daha net tarayarak tekrar deneyin.")
    return {
        "tables": result,
        "ocr_pages": [index + 1 for index in ocr_pages],
        "ocr_confidence_available": False,
        "ocr_evidence": ocr_evidence,
    }


def _unique_rows(rows: list[dict], review: list[dict] | None = None) -> list[dict]:
    unique = {}
    rejected = set()

    def source_info(row):
        return row.get("_import_source", {})

    def public_row(row):
        return {key: value for key, value in row.items() if key != "_import_source"}

    for row in rows:
        key = (row["person"].casefold(), row["section"], row["date"])
        if key in rejected:
            continue
        if key in unique and unique[key]["entry"] != row["entry"]:
            if review is None:
                raise ValueError(f"Çelişkili kayıt: {row['person']} / {row['date']}.")
            previous = unique.pop(key)
            previous_source, current_source = source_info(previous), source_info(row)
            rejected.add(key)
            review.append({
                "filename": current_source.get("filename") or previous_source.get("filename", ""),
                "page": current_source.get("page") or previous_source.get("page"),
                "location": " / ".join(
                    item for item in (previous_source.get("location"), current_source.get("location")) if item
                ),
                "source": f"{previous_source.get('source', '')}\n{current_source.get('source', '')}".strip()[:4000],
                "reason": f"Çelişkili kayıt: {row['person']} / {row['date']}.",
            })
            continue
        unique[key] = row
    if not unique and not review:
        raise ValueError("Seçili ayda içe aktarılacak vardiya bulunamadı.")
    if len(unique) > 1000:
        raise ValueError("Bir seferde en fazla 1000 vardiya içe aktarılabilir.")
    return [public_row(row) for row in unique.values()]


def parse_mapped_table(payload: dict, partial: bool = False) -> list[dict] | dict:
    month = scheduling.validate_month(payload.get("month"))
    text = payload.get("text")
    mapping = payload.get("mapping")
    if not isinstance(text, str) or len(text) > 1_000_000 or not isinstance(mapping, dict):
        raise ValueError("Elle eşleştirme verisi geçersiz veya çok büyük.")
    filename = payload.get("filename", "Düzenlenen metin")
    page = payload.get("page")
    if not isinstance(filename, str) or len(filename) > 255:
        raise ValueError("Kaynak dosya adı geçersiz.")
    if page is not None and (
        not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 50
    ):
        raise ValueError("Kaynak sayfa veya tablo numarası geçersiz.")
    mode = mapping.get("mode")
    if mode not in ("rows", "matrix"):
        raise ValueError("Tablo düzeni geçersiz.")
    lines = text.splitlines()
    if len(lines) > 5000:
        raise ValueError("En fazla 5000 tablo satırı eşleştirilebilir.")
    table = [line.split("|") for line in lines]
    if any(len(row) > 80 for row in table):
        raise ValueError("En fazla 80 tablo sütunu eşleştirilebilir.")

    def number(key: str, required: bool = True) -> int | None:
        value = mapping.get(key)
        if not required and (value is None or value == ""):
            return None
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= (5000 if key.endswith("_row") else 80):
            raise ValueError("Eşleştirmedeki satır ve sütun numaralarını kontrol edin.")
        return value - 1

    first_row = number("start_row")
    person_col = number("person_col")
    section_col = number("section_col", False)
    default_section = mapping.get("default_section", "")
    sections = {_key(section): section for section in scheduling.section_names()}
    if section_col is None and _key(default_section) not in sections:
        raise ValueError("Bölüm sütunu yoksa varsayılan bir bölüm seçin.")
    rows = []
    review = [] if partial else None

    def value(row, index):
        return row[index].strip() if index is not None and index < len(row) else ""

    def add(person, section, day, start, end, status, location, source=""):
        try:
            if not person:
                raise ValueError("personel adı eksik.")
            section_name = sections.get(_key(section))
            if not section_name:
                raise ValueError(f"bölüm tanınmadı ({section}).")
            parsed_date = _date(day)
            if parsed_date.strftime("%Y-%m") != month:
                return
            entry = _day(start, end, status)
        except ValueError as exc:
            if review is None:
                raise ValueError(f"{location}: {exc}") from exc
            review.append({
                "filename": filename, "page": page, "location": location,
                "source": source[:2000], "reason": str(exc),
            })
            return
        row = {"person": person, "section": section_name, "date": parsed_date.isoformat(), "entry": entry}
        if partial:
            row["_import_source"] = {"filename": filename, "page": page, "location": location, "source": source[:2000]}
        rows.append(row)

    if mode == "rows":
        date_col = number("date_col")
        status_col = number("status_col", False)
        start_col = number("start_col", False)
        end_col = number("end_col", False)
        if status_col is None and (start_col is None or end_col is None):
            raise ValueError("Durum sütunu veya giriş ve çıkış sütunları seçilmelidir.")
        for index, row in enumerate(table[first_row:], first_row + 1):
            if not any(cell.strip() for cell in row):
                continue
            add(value(row, person_col), value(row, section_col) if section_col is not None else default_section,
                value(row, date_col), value(row, start_col), value(row, end_col), value(row, status_col),
                f"Satır {index}", lines[index - 1])
    else:
        header_row = number("header_row")
        first_day_col = number("first_day_col")
        if header_row >= len(table):
            raise ValueError("Tarih başlığı satırı bulunamadı.")
        dates = [(index, cell) for index, cell in enumerate(table[header_row]) if index >= first_day_col and cell.strip()]
        if not dates:
            raise ValueError("Tarih başlığı satırında tarih bulunamadı.")
        for index, row in enumerate(table[first_row:], first_row + 1):
            if not any(cell.strip() for cell in row):
                continue
            person = value(row, person_col)
            section = value(row, section_col) if section_col is not None else default_section
            for column, day in dates:
                status = value(row, column)
                if status:
                    add(person, section, day, "", "", status, f"Satır {index}, sütun {column + 1}",
                        lines[index - 1])
    if review is None:
        return _unique_rows(rows)
    return {"rows": _unique_rows(rows, review), "review": review}


def import_header_signature(text: str, mapping: dict) -> dict:
    if not isinstance(text, str) or len(text) > 1_000_000 or not isinstance(mapping, dict):
        raise ValueError("Başlık karşılaştırma verisi geçersiz.")
    mode = mapping.get("mode")
    if mode not in ("rows", "matrix"):
        raise ValueError("Profil tablo düzeni geçersiz.")
    lines = text.splitlines()
    if len(lines) > 5000 or any(len(line.split("|")) > 80 for line in lines):
        raise ValueError("Başlık karşılaştırma tablosu çok büyük.")

    def column(key):
        value = mapping.get(key)
        if value is None or value == "":
            return None
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 80:
            raise ValueError("Başlık eşleştirmesindeki sütun numarası geçersiz.")
        return value

    if mode == "rows":
        start_row = mapping.get("start_row")
        if not isinstance(start_row, int) or isinstance(start_row, bool) or not 1 <= start_row <= 5000:
            raise ValueError("Başlık eşleştirmesindeki ilk satır geçersiz.")
        header_index = start_row - 2
        if header_index < 0 or header_index >= len(lines):
            return {"mode": mode, "columns": []}
        cells = lines[header_index].split("|")
        fields = (
            ("person", "person_col"), ("section", "section_col"), ("date", "date_col"),
            ("status", "status_col"), ("start", "start_col"), ("end", "end_col"),
        )
        columns = []
        for field, mapping_key in fields:
            index = column(mapping_key)
            if index is not None:
                columns.append({
                    "field": field, "column": index,
                    "header": _key(cells[index - 1] if index <= len(cells) else ""),
                })
    else:
        header_row = mapping.get("header_row")
        first_day_col = column("first_day_col")
        if not isinstance(header_row, int) or isinstance(header_row, bool) or not 1 <= header_row <= 5000:
            raise ValueError("Başlık satırı geçersiz.")
        if first_day_col is None:
            raise ValueError("İlk tarih sütunu geçersiz.")
        header_index = header_row - 1
        if header_index >= len(lines):
            return {"mode": mode, "columns": []}
        cells = lines[header_index].split("|")
        columns = []
        for field, mapping_key in (("person", "person_col"), ("section", "section_col")):
            index = column(mapping_key)
            if index is not None:
                columns.append({
                    "field": field, "column": index,
                    "header": _key(cells[index - 1] if index <= len(cells) else ""),
                })
        for index in range(first_day_col, len(cells) + 1):
            value = cells[index - 1].strip()
            if not value:
                continue
            try:
                _date(value)
                header = "DATE"
            except ValueError:
                header = _key(value)
            columns.append({"field": "day", "column": index, "header": header})
    return {"mode": mode, "columns": columns}


def validate_import_header_signature(signature) -> dict | None:
    if signature is None:
        return None
    fields = {"person", "section", "date", "status", "start", "end", "day"}
    if (not isinstance(signature, dict) or set(signature) != {"mode", "columns"}
            or signature["mode"] not in ("rows", "matrix")
            or not isinstance(signature["columns"], list) or len(signature["columns"]) > 80):
        raise ValueError("Profil başlık imzası geçersiz.")
    columns = []
    seen = set()
    for item in signature["columns"]:
        if (not isinstance(item, dict) or set(item) != {"field", "column", "header"}
                or not isinstance(item["field"], str) or item["field"] not in fields
                or not isinstance(item["column"], int) or isinstance(item["column"], bool)
                or not 1 <= item["column"] <= 80
                or not isinstance(item["header"], str) or len(item["header"]) > 100):
            raise ValueError("Profil başlık imzası geçersiz.")
        key = (item["field"], item["column"])
        if key in seen:
            raise ValueError("Profil başlık imzası yinelenen sütun içeriyor.")
        seen.add(key)
        columns.append(dict(item))
    return {"mode": signature["mode"], "columns": columns}


def check_import_profile_headers(text: str, mapping: dict, expected) -> dict:
    actual = import_header_signature(text, mapping)
    expected = validate_import_header_signature(expected)
    needs_confirmation = expected is None or not expected["columns"]
    matches = not needs_confirmation and expected == actual
    return {
        "matches": matches,
        "needs_confirmation": needs_confirmation or not matches,
        "expected": expected,
        "actual": actual,
    }


def parse_document(filename: str, encoded: str, month: str, partial: bool = False) -> list[dict] | dict:
    scheduling.validate_month(month)
    data, extension = _decode_document(filename, encoded)
    ocr_pages = []
    tables = _tables(data, extension, ocr_pages=ocr_pages)
    if ocr_pages:
        raise ValueError("Taranmış sayfalardaki metin doğrulanmalıdır. Elle eşleştirme ile metni kontrol edin.")
    sections = {_key(section): section for section in scheduling.section_names()}
    rows = []
    review = [] if partial else None
    aliases = {
        "PERSONEL": "person", "ADSOYAD": "person", "PERSONELADI": "person",
        "PERSONELADISOYADI": "person", "ADISOYADI": "person",
        "BOLUM": "section", "DEPARTMAN": "section", "BIRIM": "section", "TARIH": "date",
        "GIRIS": "start", "GIRISSAATI": "start", "BASLANGIC": "start", "BASLANGICSAATI": "start",
        "CIKIS": "end", "CIKISSAATI": "end", "BITIS": "end", "BITISSAATI": "end",
        "DURUM": "status", "VARDIYA": "status", "VARDIYASAATI": "status",
    }

    def add(person, section, day, start, end, status, location, source="", page=None):
        try:
            person = " ".join(str(person or "").split())
            if not person:
                raise ValueError("personel adı eksik.")
            section_name = sections.get(_key(section))
            if not section_name:
                raise ValueError(f"bölüm tanınmadı ({section}).")
            day = _date(day)
            if day.strftime("%Y-%m") != month:
                return
            entry = _day(start, end, status)
        except ValueError as exc:
            if review is None:
                raise ValueError(f"{location}: {exc}") from exc
            review.append({
                "filename": filename, "page": page, "location": location,
                "source": source[:2000], "reason": str(exc),
            })
            return
        row = {"person": person, "section": section_name, "date": day.isoformat(), "entry": entry}
        if partial:
            row["_import_source"] = {
                "filename": filename, "page": page, "location": location, "source": source[:2000],
            }
        rows.append(row)

    found = False
    for table_index, table in enumerate(tables, 1):
        if len(table) >= 5 and len(table[1]) >= 17 and _key(table[4][3]) == "GIRIS":
            found = True
            markers = ((2, "MARKET"), (13, "TEMİZLİK"), (19, "ÖSSE"))
            if any(len(table) < row or len(table[row - 1]) < 3 or _key(table[row - 1][2]) != _key(name)
                   for row, name in markers):
                raise ValueError(f"Sayfa {table_index}: bölüm konumları şablonla eşleşmiyor; bu sayfa otomatik aktarılamaz.")
            if any(_key(table[4][3 + day * 2]) != "GIRIS" or _key(table[4][4 + day * 2]) != "CIKIS"
                   for day in range(7)):
                raise ValueError(f"Sayfa {table_index}: giriş/çıkış sütunları şablonla eşleşmiyor.")
            if any(len(table) >= row and len(table[row - 1]) >= 3 and table[row - 1][2]
                   for row in (12, 18, 34)):
                raise ValueError(f"Sayfa {table_index}: personel satırları şablonla eşleşmiyor.")
            dates = [table[1][3 + day * 2] for day in range(7)]
            for index, row in enumerate(table, 1):
                section = next((name for name, positions in scheduling.SECTION_ROWS.items() if index in positions), None)
                if index > 71 and len(row) >= 3:
                    section = sections.get(_key(row[0]))
                if not section or len(row) < 17 or not row[2]:
                    continue
                for day, date_value in enumerate(dates):
                    start, end = row[3 + day * 2:5 + day * 2]
                    if start is None and end is None:
                        continue
                    if _key(start) in _KINDS and end is None:
                        add(row[2], section, date_value, None, None, start,
                            f"Sayfa {table_index}, satır {index}", " | ".join(str(value or "") for value in row),
                            table_index)
                    else:
                        add(row[2], section, date_value, start, end, "",
                            f"Sayfa {table_index}, satır {index}", " | ".join(str(value or "") for value in row),
                            table_index)
            continue
        header = None
        for row_index, row in enumerate(table, 1):
            if header is None:
                candidate = {aliases.get(_key(value)): index for index, value in enumerate(row) if aliases.get(_key(value))}
                if {"person", "section", "date"}.issubset(candidate) and ("status" in candidate or {"start", "end"}.issubset(candidate)):
                    header = candidate
                    found = True
                continue
            if not any(str(value or "").strip() for value in row):
                continue
            def value(name):
                index = header.get(name)
                return row[index] if index is not None and index < len(row) else None
            add(value("person"), value("section"), value("date"), value("start"), value("end"),
                value("status"), f"Tablo {table_index}, satır {row_index}",
                " | ".join(str(value or "") for value in row), table_index)
    if not found:
        raise ValueError("Tablo otomatik tanınmadı. Elle eşleştirmeyi kullanarak sütunları seçin ve metni düzeltin.")
    if review is None:
        return _unique_rows(rows)
    return {"rows": _unique_rows(rows, review), "review": review}


def filter_import_rows(rows: list[dict], fields: dict | None, allow_empty: bool = False) -> list[dict]:
    if fields is None:
        return rows
    if not isinstance(fields, dict) or set(fields) != {"mode", "codes"}:
        raise ValueError("İçe aktarma alanı seçimi geçersiz.")
    mode, codes = fields["mode"], fields["codes"]
    if mode not in ("all", "hours", "codes") or not isinstance(codes, list):
        raise ValueError("İçe aktarma alanı seçimi geçersiz.")
    if mode in ("all", "hours"):
        if codes:
            raise ValueError("Bu aktarım kapsamında kod listesi seçilemez.")
        selected = set()
    else:
        allowed = set(scheduling.KIND_CODES.values())
        if (not 1 <= len(codes) <= len(allowed)
                or any(not isinstance(code, str) or code not in allowed for code in codes)
                or len(codes) != len(set(codes))):
            raise ValueError("Aktarılacak durum kodlarını seçin.")
        selected = set(codes)

    if mode == "all":
        filtered = rows
    elif mode == "hours":
        filtered = [row for row in rows if row.get("entry", {}).get("kind") == "work"]
    else:
        filtered = [
            row for row in rows
            if scheduling.KIND_CODES.get(row.get("entry", {}).get("kind")) in selected
        ]
    if not filtered and not allow_empty:
        raise ValueError("Seçilen aktarım alanlarında kayıt bulunamadı.")
    return filtered


def compare_import_rows(rows: list[dict], existing_rows: list[dict]) -> dict:
    if not isinstance(existing_rows, list) or len(existing_rows) > 1000:
        raise ValueError("Karşılaştırılacak çizelge kayıtları geçersiz.")
    valid_kinds = {"work", *scheduling.KIND_CODES.keys()}

    def same_entry(left, right):
        if left.get("kind") != right.get("kind"):
            return False
        return left.get("kind") != "work" or (
            left.get("start"), left.get("end")
        ) == (right.get("start"), right.get("end"))

    def key_for(row):
        person = " ".join(str(row.get("person", "")).split()).casefold()
        section, day = row.get("section"), row.get("date")
        if (not person or len(person) > 80 or section not in scheduling.section_names()
                or not isinstance(day, str)):
            raise ValueError("Karşılaştırılacak çizelge kaydı geçersiz.")
        return person, section, day

    def valid_entry(entry):
        if (not isinstance(entry, dict) or not isinstance(entry.get("kind"), str)
                or entry["kind"] not in valid_kinds):
            raise ValueError("Karşılaştırılacak vardiya kaydı geçersiz.")
        if entry["kind"] == "work":
            return isinstance(entry.get("start"), str) and isinstance(entry.get("end"), str)
        return True

    current = {}
    for row in existing_rows:
        if not isinstance(row, dict) or set(row) != {"person", "section", "date", "entry"}:
            raise ValueError("Karşılaştırılacak çizelge kaydı geçersiz.")
        key = key_for(row)
        if not valid_entry(row["entry"]):
            raise ValueError("Karşılaştırılacak vardiya kaydı geçersiz.")
        if key in current and not same_entry(current[key], row["entry"]):
            raise ValueError("Çizelgede aynı gün için çelişkili kayıtlar var.")
        current[key] = row["entry"]

    statuses = []
    counts = {"same": 0, "new": 0, "changed": 0}
    for row in rows:
        key = key_for(row)
        old_entry = current.get(key)
        if old_entry is None:
            status = "new"
        elif same_entry(old_entry, row["entry"]):
            status = "same"
        else:
            status = "changed"
        statuses.append(status)
        counts[status] += 1
    return {"statuses": statuses, "counts": counts}
