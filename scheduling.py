from __future__ import annotations

import calendar
import math
import re
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.formatting.rule import FormulaRule
from openpyxl.worksheet.cell_range import MultiCellRange
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Border, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_OVERRIDE_DIR: Path | None = None
SECTION_ROWS = {
    "MARKET": tuple(range(6, 12)),
    "TEMİZLİK": (17,),
    "ÖSSE": tuple(range(23, 34)),
}
SECTION_ORDER = tuple(SECTION_ROWS)
EXTRA_SECTION_ROWS = 3
MAX_SECTION_ROWS = 40
MAX_CUSTOM_SECTIONS = 6
MAX_MONTH_PERSONNEL = 60
DEFAULT_DAILY_LIMIT_HOURS = 8.0
FIXED_PATTERN_CODES = ("X", "H", "")
DEFAULT_FIXED_PATTERN = ("X", "X", "X", "X", "X", "X", "H")
# A custom section is exported as a copy of the TEMİZLİK block: one spacer row + four header rows.
CUSTOM_SECTION_HEADER_ROWS = 5
DEFAULT_SCHEDULE_SECTIONS = tuple(
    {"name": name, "rows": len(rows)} for name, rows in SECTION_ROWS.items()
)
_SCHEDULE_SECTIONS: tuple[dict[str, Any], ...] = DEFAULT_SCHEDULE_SECTIONS
_RESTORED_SETTINGS = ContextVar("restored_workbook_settings", default=None)


@contextmanager
def restored_workbook_settings(template_dir, sections, holidays):
    settings = (Path(template_dir), normalize_schedule_sections(sections or list(DEFAULT_SCHEDULE_SECTIONS)),
                _holiday_overrides(holidays))
    token = _RESTORED_SETTINGS.set(settings)
    try:
        yield
    finally:
        _RESTORED_SETTINGS.reset(token)


def _active_schedule_sections():
    settings = _RESTORED_SETTINGS.get()
    return settings[1] if settings is not None else _SCHEDULE_SECTIONS


def normalize_schedule_sections(value: Any) -> list[dict[str, Any]]:
    """Built-in template sections keep their place; custom sections follow ÖSSE in list order."""
    if not isinstance(value, list) or not 3 <= len(value) <= 3 + MAX_CUSTOM_SECTIONS:
        raise ValueError(f"Bölüm listesi 3 yerleşik ve en fazla {MAX_CUSTOM_SECTIONS} yeni bölüm içerebilir.")
    cleaned, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "rows"}:
            raise ValueError("Bölüm bilgisi geçersiz.")
        name = item["name"]
        if not isinstance(name, str):
            raise ValueError("Bölüm adı geçersiz.")
        name = " ".join(name.split()).translate(str.maketrans("iı", "İI")).upper()
        if not 1 <= len(name) <= 24 or not re.fullmatch(r"[0-9A-ZÇĞİÖŞÜ .&/-]+", name):
            raise ValueError("Bölüm adı 1-24 karakter olmalı; harf, rakam, boşluk ve . & / - kullanılabilir.")
        if name.casefold() in seen:
            raise ValueError(f"“{name}” bölümü iki kez yazılmış.")
        seen.add(name.casefold())
        rows = item["rows"]
        minimum = len(SECTION_ROWS.get(name, ())) or 1
        if not isinstance(rows, int) or isinstance(rows, bool) or not minimum <= rows <= MAX_SECTION_ROWS:
            raise ValueError(f"{name} bölümünün satır sayısı {minimum}-{MAX_SECTION_ROWS} arasında olmalıdır.")
        cleaned.append({"name": name, "rows": rows})
    if [item["name"] for item in cleaned[:3]] != list(SECTION_ORDER):
        raise ValueError("MARKET, TEMİZLİK ve ÖSSE bölümleri şablonun parçasıdır; silinemez veya yeniden sıralanamaz.")
    return cleaned


def set_schedule_sections(value: Any) -> None:
    global _SCHEDULE_SECTIONS
    try:
        sections = normalize_schedule_sections(value)
    except (ValueError, TypeError):
        sections = [dict(item) for item in DEFAULT_SCHEDULE_SECTIONS]
    _SCHEDULE_SECTIONS = tuple(sections)


def schedule_sections() -> list[dict[str, Any]]:
    return [{
        **item,
        "template_rows": list(SECTION_ROWS.get(item["name"], ())),
        "capacity": section_capacity(item["name"]),
        "custom": item["name"] not in SECTION_ROWS,
    } for item in _active_schedule_sections()]


def section_names() -> tuple[str, ...]:
    return tuple(item["name"] for item in _active_schedule_sections())


def section_row_count(name: str) -> int:
    return next((item["rows"] for item in _active_schedule_sections() if item["name"] == name), 0)


def section_capacity(name: str) -> int:
    """Visible rows plus the optional rows opened with “Ek personel satırı ekle”."""
    rows = section_row_count(name)
    return min(MAX_SECTION_ROWS, rows + EXTRA_SECTION_ROWS) if rows else 0


def _section_sort_key(name: str) -> int:
    names = section_names()
    return names.index(name) if name in names else len(names)


def _schedule_extra_row_allowance() -> int:
    return sum(
        section_capacity(item["name"]) - len(SECTION_ROWS.get(item["name"], ()))
        + (0 if item["name"] in SECTION_ROWS else CUSTOM_SECTION_HEADER_ROWS)
        for item in _active_schedule_sections()
    )
KIND_CODES = {
    "weekly_off": "H",
    "overnight_exit": "G.Ç",
    "annual_leave": "S",
    "sick_leave": "R",
    "unpaid_leave": "Ü",
    "marriage_leave": "E",
    "quarantine": "K",
    "half_day": "Y",
    "absent": "G",
    "resignation": "İST.",
}
CODE_LABELS = {
    "work": "Çalışma",
    "weekly_off": "Hafta tatili",
    "overnight_exit": "Geceden çıkış",
    "annual_leave": "Senelik izin",
    "sick_leave": "Raporlu",
    "unpaid_leave": "Ücretsiz izin",
    "marriage_leave": "Evlilik izni",
    "quarantine": "Karantina",
    "half_day": "Yarım gün",
    "absent": "İşe gelmedi",
    "resignation": "İstifa",
    "custom": "Özel kod",
}
STANDARD_ATTENDANCE_CODES = {"", "X", "H", "G.Ç", "S", "R", "Ü", "G", "İST.", "E", "K", "Y"}
SCHEDULE_KINDS = {"", "work", *KIND_CODES, "custom"}
MONTH_NAMES = (
    "OCAK", "ŞUBAT", "MART", "NİSAN", "MAYIS", "HAZİRAN",
    "TEMMUZ", "AĞUSTOS", "EYLÜL", "EKİM", "KASIM", "ARALIK",
)
TEMPLATE_FILES = {
    "schedule": "weekly_schedule.xlsx",
    "attendance": "attendance.xlsx",
}
ATTENDANCE_CATEGORY_COLORS = {
    "weekday": "#F1F6F1",
    "weekend": "#E8EEF4",
    "official": "#FBEBDD",
    "religious": "#F2EAF5",
}
_FIXED_PUBLIC_HOLIDAYS = {
    (1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 28), (10, 29),
}
# Diyanet takvimine göre Ramazan (3 gün) ve Kurban (4 gün) bayramları: (arife, ilk gün, gün sayısı).
_RELIGIOUS_FEASTS = (
    (date(2025, 3, 29), date(2025, 3, 30), 3), (date(2025, 6, 5), date(2025, 6, 6), 4),
    (date(2026, 3, 19), date(2026, 3, 20), 3), (date(2026, 5, 26), date(2026, 5, 27), 4),
    (date(2027, 3, 8), date(2027, 3, 9), 3), (date(2027, 5, 15), date(2027, 5, 16), 4),
    (date(2028, 2, 25), date(2028, 2, 26), 3), (date(2028, 5, 4), date(2028, 5, 5), 4),
    (date(2029, 2, 13), date(2029, 2, 14), 3), (date(2029, 4, 23), date(2029, 4, 24), 4),
    (date(2030, 2, 3), date(2030, 2, 4), 3), (date(2030, 4, 12), date(2030, 4, 13), 4),
)
_RELIGIOUS_HOLIDAYS = {
    first + timedelta(days=offset) for _, first, length in _RELIGIOUS_FEASTS for offset in range(length)
}
# 23 Nisan 2029 hem resmî tatil hem bayram arifesidir; tam gün tatil sayılır.
_RELIGIOUS_EVES = {eve for eve, _, _ in _RELIGIOUS_FEASTS if (eve.month, eve.day) != (4, 23)}
RELIGIOUS_CALENDAR_YEARS = (min(item[1].year for item in _RELIGIOUS_FEASTS), max(item[1].year for item in _RELIGIOUS_FEASTS))
_CUSTOM_HOLIDAYS: dict[date, str] = {}
HOLIDAY_KIND_LABELS = {
    "public": "Resmî tatil", "religious": "Dinî bayram",
    "eve": "Arife (yarım gün)", "workday": "Çalışma günü (tatil değil)",
}
NIGHT_START = time(20, 0)
NIGHT_END = time(6, 0)


def set_custom_holidays(items: list[dict[str, Any]] | None) -> None:
    """Install user-edited holiday overrides used by every calendar/export path."""
    global _CUSTOM_HOLIDAYS
    _CUSTOM_HOLIDAYS = _holiday_overrides(items)


def _holiday_overrides(items):
    overrides = {}
    for item in items or []:
        try:
            overrides[date.fromisoformat(item["date"])] = item.get("kind", "public")
        except (KeyError, TypeError, ValueError):
            continue
    return overrides


def _active_holiday(day):
    settings = _RESTORED_SETTINGS.get()
    return (settings[2] if settings is not None else _CUSTOM_HOLIDAYS).get(day)


def builtin_holidays(year: int) -> list[dict[str, str]]:
    names = {
        (1, 1): "Yılbaşı", (4, 23): "Ulusal Egemenlik ve Çocuk Bayramı",
        (5, 1): "Emek ve Dayanışma Günü", (5, 19): "Gençlik ve Spor Bayramı",
        (7, 15): "Demokrasi ve Millî Birlik Günü", (8, 30): "Zafer Bayramı",
        (10, 28): "Cumhuriyet Bayramı arifesi (13:00 sonrası)", (10, 29): "Cumhuriyet Bayramı",
    }
    items = [
        {"date": date(year, month, day).isoformat(), "kind": "public", "name": name}
        for (month, day), name in names.items()
    ]
    for eve, first, length in _RELIGIOUS_FEASTS:
        feast = "Ramazan Bayramı" if length == 3 else "Kurban Bayramı"
        items += [
            {"date": (first + timedelta(days=offset)).isoformat(), "kind": "religious",
             "name": f"{feast} {offset + 1}. gün"}
            for offset in range(length) if (first + timedelta(days=offset)).year == year
        ]
        if eve.year == year and eve in _RELIGIOUS_EVES:
            items.append({"date": eve.isoformat(), "kind": "eve", "name": f"{feast} arifesi (13:00 sonrası)"})
    return sorted(items, key=lambda item: item["date"])


def _theme_colors(workbook) -> list[str]:
    if not workbook.loaded_theme:
        return []
    root = ElementTree.fromstring(workbook.loaded_theme)
    scheme = root.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}clrScheme")
    if scheme is None:
        return []
    namespace = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    theme_order = (
        "lt1", "dk1", "lt2", "dk2", "accent1", "accent2",
        "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink",
    )
    colors = []
    for name in theme_order:
        item = scheme.find(f"{namespace}{name}")
        color = next(iter(item), None)
        if color is None:
            colors.append("000000")
        else:
            colors.append(color.get("lastClr") or color.get("val") or "000000")
    return colors


def _css_color(color, theme: list[str]) -> str | None:
    if color is None:
        return None
    value = None
    if color.type == "rgb" and color.rgb:
        value = color.rgb[-6:]
    elif color.type == "theme" and color.theme is not None and color.theme < len(theme):
        value = theme[color.theme][-6:]
    elif color.type == "indexed" and color.indexed is not None:
        indexed = color.indexed
        if indexed == 64:
            value = "000000"
        elif indexed == 65:
            value = "FFFFFF"
        elif indexed < 64 and indexed < len(_INDEXED_COLORS):
            value = _INDEXED_COLORS[indexed]
    if not value or not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        return None
    channels = [int(value[index:index + 2], 16) for index in (0, 2, 4)]
    tint = color.tint or 0
    if tint:
        channels = [
            round(channel * (1 + tint)) if tint < 0 else round(channel * (1 - tint) + 255 * tint)
            for channel in channels
        ]
    return "#" + "".join(f"{max(0, min(255, channel)):02X}" for channel in channels)


_INDEXED_COLORS = (
    "000000", "FFFFFF", "FF0000", "00FF00", "0000FF", "FFFF00", "FF00FF", "00FFFF",
    "000000", "FFFFFF", "FF0000", "00FF00", "0000FF", "FFFF00", "FF00FF", "00FFFF",
    "800000", "008000", "000080", "808000", "800080", "008080", "C0C0C0", "808080",
    "9999FF", "993366", "FFFFCC", "CCFFFF", "660066", "FF8080", "0066CC", "CCCCFF",
    "000080", "FF00FF", "FFFF00", "00FFFF", "800080", "800000", "008080", "0000FF",
    "00CCFF", "CCFFFF", "CCFFCC", "FFFF99", "99CCFF", "FF99CC", "CC99FF", "FFCC99",
    "3366FF", "33CCCC", "99CC00", "FFCC00", "FF9900", "FF6600", "666699", "969696",
    "003366", "339966", "003300", "333300", "993300", "993366", "333399", "333333",
)


def _style_css(cell, theme: list[str]) -> str:
    declarations = []
    font = cell.font
    if font.name:
        family = re.sub(r"[^a-zA-Z0-9 ,_-]", "", font.name).strip()
        if family:
            declarations.append(f"font-family:{family}")
    if font.sz:
        declarations.append(f"font-size:{font.sz}pt")
    if font.bold:
        declarations.append("font-weight:700")
    if font.italic:
        declarations.append("font-style:italic")
    if font.underline:
        declarations.append("text-decoration:underline")
    font_color = _css_color(font.color, theme)
    fill_color = _css_color(cell.fill.fgColor, theme)
    if cell.fill.fill_type == "solid" and fill_color == "#000000":
        fill_color = "#FFFFFF"
        if font_color == "#FFFFFF":
            font_color = "#000000"
    if font_color:
        declarations.append(f"color:{font_color}")

    if cell.fill.fill_type == "solid" and fill_color:
        declarations.append(f"background-color:{fill_color}")

    alignment = cell.alignment
    if alignment.horizontal:
        horizontal = {"centerContinuous": "center", "distributed": "justify"}.get(
            alignment.horizontal, alignment.horizontal
        )
        declarations.append(f"text-align:{horizontal}")
    if alignment.vertical:
        vertical = {"center": "middle"}.get(alignment.vertical, alignment.vertical)
        declarations.append(f"vertical-align:{vertical}")
    if alignment.wrap_text:
        declarations.append("white-space:pre-wrap")
    elif alignment.shrink_to_fit:
        declarations.append("white-space:nowrap")
    if alignment.indent:
        declarations.append(f"padding-left:{alignment.indent * 3}px")

    for side_name in ("left", "right", "top", "bottom"):
        side = getattr(cell.border, side_name)
        if not side.style:
            continue
        width, border_style = {
            "hair": ("1px", "solid"),
            "dotted": ("1px", "dotted"),
            "dashed": ("1px", "dashed"),
            "dashDot": ("1px", "dashed"),
            "dashDotDot": ("1px", "dashed"),
            "thin": ("1px", "solid"),
            "medium": ("2px", "solid"),
            "mediumDashed": ("2px", "dashed"),
            "mediumDashDot": ("2px", "dashed"),
            "mediumDashDotDot": ("2px", "dashed"),
            "thick": ("3px", "solid"),
            "double": ("3px", "double"),
        }.get(side.style, ("1px", "solid"))
        color = _css_color(side.color, theme) or "#000000"
        declarations.append(f"border-{side_name}:{width} {border_style} {color}")
    return ";".join(declarations)


def set_template_override_dir(path: str | Path | None) -> None:
    global TEMPLATE_OVERRIDE_DIR
    TEMPLATE_OVERRIDE_DIR = Path(path) if path is not None else None


def _template_structure(workbook) -> list[dict[str, Any]]:
    return [{
        "title": ws.title,
        "rows": ws.max_row,
        "columns": ws.max_column,
        "merges": sorted(str(item) for item in ws.merged_cells.ranges),
    } for ws in workbook.worksheets]


def table_template_status(kind: str) -> dict[str, Any]:
    if kind not in TEMPLATE_FILES:
        raise ValueError("Excel şablon türü geçersiz.")
    path = _template_path(TEMPLATE_FILES[kind])
    custom = path.parent != TEMPLATE_DIR
    workbook = load_workbook(path, read_only=True)
    try:
        sheets = [ws.title for ws in workbook.worksheets]
    finally:
        workbook.close()
    return {
        "kind": kind,
        "custom": custom,
        "updated_at": (datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
                       if custom else None),
        "sheets": sheets,
    }


def table_template_bytes(kind: str) -> bytes:
    if kind not in TEMPLATE_FILES:
        raise ValueError("Excel şablon türü geçersiz.")
    path = _template_path(TEMPLATE_FILES[kind])
    if kind == "attendance":
        workbook = load_workbook(path)
        if _drop_normal_hours_column(workbook.active):
            buffer = BytesIO()
            workbook.save(buffer)
            return buffer.getvalue()
    return path.read_bytes()


def _drop_normal_hours_column(ws: Any) -> bool:
    """Removes the legacy NORMAL MESAİ column; FM already holds the daily excess hours."""
    column = next((col for col in range(34, min(ws.max_column, 40) + 1)
                   if isinstance(ws.cell(3, col).value, str)
                   and "NORMAL" in ws.cell(3, col).value.upper()), None)
    if column is None:
        return False
    last = ws.max_column
    widths = {col: ws.column_dimensions[get_column_letter(col)].width for col in range(column, last + 1)}
    merged_styles = {
        (cell.row, cell.column): copy(cell._style)
        for row in ws.iter_rows(min_col=column, max_col=column)
        for cell in row if isinstance(cell, MergedCell)
    }
    ws.delete_cols(column)
    for (row, col), style in merged_styles.items():
        merged = MergedCell(ws, row=row, column=col)
        merged._style = style
        ws._cells[(row, col)] = merged
    for col in range(column, last):
        ws.column_dimensions[get_column_letter(col)].width = widths[col + 1]
    ws.column_dimensions.pop(get_column_letter(last), None)
    return True


def validate_table_template(kind: str, content: bytes) -> bytes:
    """Validate and normalize a template without changing installed files."""
    if kind not in TEMPLATE_FILES:
        raise ValueError("Excel şablon türü geçersiz.")
    if not content.startswith(b"PK") or len(content) > 5_000_000:
        raise ValueError("Şablon .xlsx biçiminde ve en fazla 5 MB olmalıdır.")
    try:
        uploaded = load_workbook(BytesIO(content))
    except Exception as exc:
        raise ValueError("Excel şablonu okunamadı.") from exc
    original = load_workbook(TEMPLATE_DIR / TEMPLATE_FILES[kind])
    normalized = False
    if kind == "attendance":
        _drop_normal_hours_column(original.active)
        normalized = _drop_normal_hours_column(uploaded.active)
    expected, actual = _template_structure(original), _template_structure(uploaded)
    if len(expected) != len(actual):
        raise ValueError(f"Şablonda {len(expected)} çalışma sayfası olmalıdır; dosyada {len(actual)} var.")
    for want, got, sheet in zip(expected, actual, uploaded.worksheets):
        too_small = got["rows"] < want["rows"] or got["columns"] < want["columns"]
        overflow = any(
            cell.value not in (None, "")
            for row in sheet.iter_rows(min_row=1, max_row=got["rows"], max_col=got["columns"])
            for cell in row
            if cell.row > want["rows"] or cell.column > want["columns"]
        ) if not too_small else False
        if too_small or overflow:
            raise ValueError(
                f"“{got['title']}” sayfası {want['rows']} satır × {want['columns']} sütun olmalıdır; "
                f"dosyada {got['rows']} × {got['columns']}. Satır/sütun eklemeyin veya silmeyin."
            )
        if want["merges"] != got["merges"]:
            missing = sorted(set(want["merges"]) - set(got["merges"]))[:3]
            extra = sorted(set(got["merges"]) - set(want["merges"]))[:3]
            raise ValueError(
                f"“{got['title']}” sayfasında birleştirilmiş hücreler değişmiş "
                f"(eksik: {', '.join(missing) or '-'}; fazla: {', '.join(extra) or '-'})."
            )
    trimmed = normalized
    for want, sheet in zip(expected, uploaded.worksheets):
        if sheet.max_row > want["rows"]:
            sheet.delete_rows(want["rows"] + 1, sheet.max_row - want["rows"])
            trimmed = True
        if sheet.max_column > want["columns"]:
            sheet.delete_cols(want["columns"] + 1, sheet.max_column - want["columns"])
            trimmed = True
    if trimmed:
        buffer = BytesIO()
        uploaded.save(buffer)
        content = buffer.getvalue()
    uploaded.close()
    original.close()
    return content


def save_table_template(kind: str, content: bytes) -> dict[str, Any]:
    """Validates an Excel-edited template against the built-in layout and installs it."""
    if TEMPLATE_OVERRIDE_DIR is None:
        raise ValueError("Şablon klasörü kullanılamıyor.")
    content = validate_table_template(kind, content)
    TEMPLATE_OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    target = TEMPLATE_OVERRIDE_DIR / TEMPLATE_FILES[kind]
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    return table_template_status(kind)


def reset_table_template(kind: str) -> dict[str, Any]:
    if kind not in TEMPLATE_FILES:
        raise ValueError("Excel şablon türü geçersiz.")
    if TEMPLATE_OVERRIDE_DIR is not None:
        (TEMPLATE_OVERRIDE_DIR / TEMPLATE_FILES[kind]).unlink(missing_ok=True)
    return table_template_status(kind)


def sheet_settings_for(settings: dict[str, Any] | None, kind: str, month: str, index: int) -> dict[str, Any]:
    """Month/week settings win; otherwise the table-template layer applies."""
    sheets = (settings or {}).get("sheets", {})
    page = sheets.get(f"{kind}:{month}:{index}")
    template = sheets.get(f"{kind}:template:{index}") or {}
    if page and "page_setup" not in page and template.get("page_setup"):
        # Remembered print settings saved on the template layer also apply to formatted months.
        return {**page, "page_setup": template["page_setup"]}
    return page or template


def workbook_template(kind: str, sheet_index: int = 0) -> dict[str, Any]:
    if kind not in TEMPLATE_FILES:
        raise ValueError("Excel şablon türü geçersiz.")
    path = _template_path(TEMPLATE_FILES[kind])
    return _workbook_template(kind, sheet_index, str(path), path.stat().st_mtime_ns)


# Leftover marks in the source Excel files that carry no meaning (e.g. a lone "." in A4 of every weekly sheet).
TEMPLATE_ARTIFACT_CELLS = {"schedule": ("A4",)}


def _clear_template_artifacts(worksheet: Any, kind: str) -> None:
    for coordinate in TEMPLATE_ARTIFACT_CELLS.get(kind, ()):
        cell = worksheet[coordinate]
        if isinstance(cell.value, str) and cell.value.strip() in {".", ","}:
            cell.value = None


@lru_cache(maxsize=24)
def _workbook_template(kind: str, sheet_index: int, path: str, _mtime: int) -> dict[str, Any]:
    if not isinstance(sheet_index, int) or isinstance(sheet_index, bool) or sheet_index < 0:
        raise ValueError("Excel çalışma sayfası numarası geçersiz.")
    workbook = load_workbook(path, data_only=False)
    if sheet_index >= len(workbook.worksheets):
        raise ValueError("Excel çalışma sayfası bulunamadı.")
    worksheet = workbook.worksheets[sheet_index]
    if kind == "attendance":
        _drop_normal_hours_column(worksheet)
    _clear_template_artifacts(worksheet, kind)
    theme = _theme_colors(workbook)
    styles = {}
    cells = []
    for row in worksheet.iter_rows(
        min_row=1, max_row=worksheet.max_row, min_col=1, max_col=worksheet.max_column
    ):
        cell_row = []
        for cell in row:
            if str(cell.style_id) not in styles:
                styles[str(cell.style_id)] = _style_css(cell, theme)
            value = cell.value
            if isinstance(value, (datetime, date)):
                value = value.isoformat()
                cell_type = "date"
            elif isinstance(value, time):
                value = value.strftime("%H:%M")
                cell_type = "time"
            elif isinstance(value, str) and value.startswith("="):
                cell_type = "formula"
            else:
                cell_type = "text"
            cell_row.append({
                "value": value,
                "style": cell.style_id,
                "type": cell_type,
                "format": cell.number_format,
                "rotation": cell.alignment.textRotation or 0,
            })
        cells.append(cell_row)

    rows = []
    default_row_height = worksheet.sheet_format.defaultRowHeight or 15
    for row_number in range(1, worksheet.max_row + 1):
        dimension = worksheet.row_dimensions[row_number]
        rows.append({
            "height": dimension.height or default_row_height,
            "hidden": bool(dimension.hidden),
        })

    columns = []
    for column_number in range(1, worksheet.max_column + 1):
        dimension = worksheet.column_dimensions.get(get_column_letter(column_number))
        columns.append({
            "width": dimension.width if dimension and dimension.width is not None else 8.43,
            "hidden": bool(dimension.hidden) if dimension else False,
        })

    merges = []
    for merged in worksheet.merged_cells.ranges:
        min_col, min_row, max_col, max_row = merged.bounds
        merges.append({
            "row": min_row,
            "column": min_col,
            "rowspan": max_row - min_row + 1,
            "colspan": max_col - min_col + 1,
        })
    return {
        "kind": kind,
        "sheet_index": sheet_index,
        "sheet_name": worksheet.title,
        "rows": rows,
        "columns": columns,
        "cells": cells,
        "styles": styles,
        "merges": merges,
        "zoom": worksheet.sheet_view.zoomScale or 100,
        "print_scale": worksheet.page_setup.scale,
    }


def validate_month(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("Ay YYYY-AA biçiminde olmalıdır.")
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise ValueError("Ay geçerli değil.") from exc
    return value


def month_dates(month: str) -> list[date]:
    year, month_number = map(int, validate_month(month).split("-"))
    return [date(year, month_number, day) for day in range(1, calendar.monthrange(year, month_number)[1] + 1)]


def attendance_day_category(day: date) -> str:
    custom = _active_holiday(day)
    if custom == "religious":
        return "religious"
    if custom in ("public", "eve"):
        return "official"
    if custom == "workday":
        return "weekend" if day.weekday() >= 5 else "weekday"
    if day in _RELIGIOUS_HOLIDAYS:
        return "religious"
    if day in _RELIGIOUS_EVES or (day.month, day.day) in _FIXED_PUBLIC_HOLIDAYS:
        return "official"
    return "weekend" if day.weekday() >= 5 else "weekday"


def holiday_window(day: date) -> tuple[datetime, datetime] | None:
    """UBGT window of a day: full day for holidays, from 13:00 for half-day eves."""
    custom = _active_holiday(day)
    if custom == "workday":
        return None
    after = datetime.combine(day + timedelta(days=1), time.min)
    if custom == "eve" or (custom is None and (day in _RELIGIOUS_EVES or (day.month, day.day) == (10, 28))):
        return datetime.combine(day, time(13, 0)), after
    if custom in ("public", "religious") or day in _RELIGIOUS_HOLIDAYS or (day.month, day.day) in _FIXED_PUBLIC_HOLIDAYS:
        return datetime.combine(day, time.min), after
    return None


def attendance_calendar(month: str) -> dict[str, str]:
    return {
        day.isoformat(): attendance_day_category(day)
        for day in month_dates(month)
    }


def week_starts(month: str) -> list[date]:
    first, last = month_dates(month)[0], month_dates(month)[-1]
    monday = first - timedelta(days=first.weekday())
    final_sunday = last + timedelta(days=6 - last.weekday())
    result = []
    while monday <= final_sunday:
        result.append(monday)
        monday += timedelta(days=7)
    return result


def adjacent_months(month: str) -> tuple[str, str, str]:
    year, month_number = map(int, validate_month(month).split("-"))
    previous = date(year, month_number, 1) - timedelta(days=1)
    next_month = date(year + (month_number == 12), 1 if month_number == 12 else month_number + 1, 1)
    return previous.strftime("%Y-%m"), month, next_month.strftime("%Y-%m")


def normalize_attendance_code(value: Any, allow_custom: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError("Puantaj kodu metin olmalıdır.")
    code = " ".join(value.strip().upper().split())
    if code in STANDARD_ATTENDANCE_CODES:
        return code
    if allow_custom and re.fullmatch(r"[A-ZÇĞİÖŞÜ0-9. -]{1,8}", code):
        return code
    raise ValueError(f"Geçersiz puantaj kodu: {value}")


def normalize_quick_entries(entries: Any) -> list[dict[str, str]]:
    if not isinstance(entries, list) or len(entries) > 40:
        raise ValueError("En fazla 40 özel hızlı giriş tanımlanabilir.")
    normalized = []
    seen_ids: set[str] = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not {"id", "label", "code", "start", "end"} <= set(entry)
            or set(entry) - {"id", "label", "code", "start", "end", "shift_type", "hidden", "preset"}
        ):
            raise ValueError("Hızlı giriş bilgisi geçersiz.")
        entry_id = entry["id"]
        label = entry["label"]
        if not isinstance(entry_id, str) or not entry_id.strip() or len(entry_id) > 80 or entry_id in seen_ids:
            raise ValueError("Hızlı giriş kimliği eksik veya yineleniyor.")
        if not isinstance(label, str):
            raise ValueError("Hızlı giriş adı metin olmalıdır.")
        label = " ".join(label.split())
        if not label or len(label) > 40:
            raise ValueError("Hızlı giriş adı 1-40 karakter olmalıdır.")
        code = normalize_attendance_code(entry["code"])
        if not code:
            raise ValueError("Puantaj kısa kodu boş olamaz.")
        start, end = entry["start"], entry["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("Giriş ve çıkış saatleri metin olmalıdır.")
        if bool(start) != bool(end):
            raise ValueError("Vardiya saatleri birlikte girilmelidir.")
        if start:
            start, end = _validate_time(start), _validate_time(end)
            if start == end:
                raise ValueError("Başlangıç ve bitiş saati aynı olamaz.")
        seen_ids.add(entry_id)
        item = {
            "id": entry_id.strip(),
            "label": label,
            "code": code,
            "start": start,
            "end": end,
        }
        shift_type = _shift_type(entry.get("shift_type"))
        if shift_type:
            item["shift_type"] = shift_type
        if "hidden" in entry:
            if not isinstance(entry["hidden"], bool):
                raise ValueError("Hızlı giriş gizleme ayarı geçersiz.")
            if entry["hidden"]:
                item["hidden"] = True
        preset = entry.get("preset")
        if preset not in (None, ""):
            if not isinstance(preset, str) or not re.fullmatch(r"[a-z0-9_-]{1,30}", preset):
                raise ValueError("Hazır giriş kimliği geçersiz.")
            if any(other.get("preset") == preset for other in normalized):
                raise ValueError("Aynı hazır giriş iki kez düzenlenemez.")
            item["preset"] = preset
        normalized.append(item)
    return normalized


def normalize_color(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("Hücre rengi #RRGGBB biçiminde olmalıdır.")
    return value.lower()


def _inserted_layout(
    template: dict[str, Any],
    extra_rows: int,
    extra_cols: int,
    insert_rows: Any,
    insert_cols: Any,
    delete_rows: Any = None,
    delete_cols: Any = None,
) -> tuple[list[int | None], list[int | None], list[dict[str, int]]]:
    rows = list(range(1, len(template["rows"]) + extra_rows + 1))
    cols = list(range(1, len(template["columns"]) + extra_cols + 1))
    merges = [dict(merge) for merge in template["merges"]]
    for axis, positions in (("row", insert_rows), ("column", insert_cols)):
        if not isinstance(positions, list) or len(positions) > 20:
            raise ValueError("Eklenen satır veya sütun konumları geçersiz.")
        mapping = rows if axis == "row" else cols
        span_name = "rowspan" if axis == "row" else "colspan"
        for position in positions:
            if not isinstance(position, int) or isinstance(position, bool) or not 1 <= position <= len(mapping) + 1:
                raise ValueError("Eklenen satır veya sütun konumu geçersiz.")
            if axis == "column" and template["kind"] == "schedule" and 1 < position <= len(mapping):
                left, right = mapping[position - 2], mapping[position - 1]
                if left in range(4, 17, 2) and right == left + 1:
                    raise ValueError("Giriş ve çıkış sütunlarının arasına ekleme yapılamaz.")
            mapping.insert(position - 1, None)
            for merge in merges:
                if merge[axis] >= position:
                    merge[axis] += 1
                elif position < merge[axis] + merge[span_name]:
                    merge[span_name] += 1

    for axis, positions in (("row", delete_rows or []), ("column", delete_cols or [])):
        if not isinstance(positions, list) or len(positions) > 20:
            raise ValueError("Silinecek satır veya sütun konumları geçersiz.")
        mapping = rows if axis == "row" else cols
        span_name = "rowspan" if axis == "row" else "colspan"
        seen = set()
        for source_position in positions:
            if (not isinstance(source_position, int) or isinstance(source_position, bool)
                    or source_position in seen or source_position not in mapping):
                raise ValueError("Silinecek satır veya sütun konumu geçersiz.")
            seen.add(source_position)
            current_position = mapping.index(source_position) + 1
            if source_position <= (len(template["rows"]) if axis == "row" else len(template["columns"])):
                source_cells = (
                    (row for row in template["cells"][source_position - 1])
                    if axis == "row"
                    else (row[source_position - 1] for row in template["cells"])
                )
                if any(cell["value"] not in (None, "") for cell in source_cells):
                    raise ValueError("İçeriği olan satır veya sütun silinemez; önce içeriği taşıyın.")
                if axis == "row" and template["kind"] == "schedule" and (
                    source_position <= 33 or source_position == 35
                ):
                    raise ValueError("Vardiya verisi veya istasyon müdürü satırı silinemez.")
                if axis == "row" and template["kind"] == "attendance" and source_position <= 24:
                    raise ValueError("Puantaj başlığı veya personel satırları silinemez.")
                for row in template["cells"]:
                    for cell in row:
                        if cell["type"] != "formula":
                            continue
                        references_deleted_position = _CELL_REFERENCE.search(cell["value"])
                        while references_deleted_position:
                            match = references_deleted_position
                            referenced = (
                                int(match.group(4))
                                if axis == "row"
                                else column_index_from_string(match.group(2))
                            )
                            if referenced == source_position:
                                raise ValueError("Formül başvurusu olan satır veya sütun silinemez.")
                            references_deleted_position = _CELL_REFERENCE.search(
                                cell["value"], match.end()
                            )
            if any(
                merge[axis] <= current_position
                < merge[axis] + merge[span_name]
                for merge in merges
            ):
                raise ValueError("Birleşik hücre içeren satır veya sütun silinemez.")
            mapping.pop(current_position - 1)
            for merge in merges:
                if merge[axis] > current_position:
                    merge[axis] -= 1
    return rows, cols, merges


def normalize_schedule_validation_settings(settings: Any) -> dict[str, float]:
    if not isinstance(settings, dict) or set(settings) != {"minimum_rest_hours"}:
        raise ValueError("Vardiya denetim ayarları geçersiz.")
    hours = settings["minimum_rest_hours"]
    if (not isinstance(hours, (int, float)) or isinstance(hours, bool)
            or not math.isfinite(hours) or not 0 <= hours <= 24):
        raise ValueError("Asgari dinlenme süresi 0-24 saat arasında olmalıdır.")
    return {"minimum_rest_hours": round(float(hours), 2)}


BREAK_RULE_PRESET = [[0.0, 15], [4.0, 30], [7.5, 60]]


def normalize_break_rules(value: Any) -> list[list[float | int]]:
    """Mola kademeleri: [[aşılan saat, mola dakikası], ...]; boş liste molayı kapatır."""
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > 6:
        raise ValueError("Mola kuralları en fazla 6 kademe olabilir.")
    rules: list[list[float | int]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("Mola kuralı [saat, dakika] biçiminde olmalıdır.")
        over, minutes = item
        if (not isinstance(over, (int, float)) or isinstance(over, bool) or not math.isfinite(over)
                or not 0 <= over <= 24):
            raise ValueError("Mola kuralındaki saat 0-24 arasında olmalıdır.")
        if (not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or not math.isfinite(minutes)
                or not 0 < minutes <= 240 or int(minutes) != minutes):
            raise ValueError("Mola süresi 1-240 dakika arasında tam sayı olmalıdır.")
        rules.append([round(float(over), 2), int(minutes)])
    rules.sort()
    if len({rule[0] for rule in rules}) != len(rules):
        raise ValueError("Aynı saat için birden fazla mola kuralı tanımlanamaz.")
    return rules


def break_minutes(worked_minutes: int, rules: Any) -> int:
    """Günlük çalışma süresine göre düşülecek mola (en yüksek eşleşen kademe)."""
    applied = 0
    for over, minutes in rules or ():
        if worked_minutes > over * 60:
            applied = int(minutes)
    return min(applied, max(0, worked_minutes))


def normalize_overtime_settings(settings: Any) -> dict[str, Any]:
    if (not isinstance(settings, dict)
            or not {"weekly_limit_hours", "week_start"} <= set(settings)
            or not set(settings) <= {"weekly_limit_hours", "week_start", "daily_limit_hours", "break_rules"}):
        raise ValueError("Fazla mesai ayarları geçersiz.")
    hours = settings["weekly_limit_hours"]
    week_start = settings["week_start"]
    daily = settings.get("daily_limit_hours", DEFAULT_DAILY_LIMIT_HOURS)
    if (not isinstance(hours, (int, float)) or isinstance(hours, bool)
            or not math.isfinite(hours) or not 0 <= hours <= 168):
        raise ValueError("Haftalık normal çalışma saati 0-168 arasında olmalıdır.")
    if (not isinstance(daily, (int, float)) or isinstance(daily, bool)
            or not math.isfinite(daily) or not 0 <= daily <= 24):
        raise ValueError("Günlük normal çalışma saati 0-24 arasında olmalıdır.")
    if (not isinstance(week_start, int) or isinstance(week_start, bool)
            or not 0 <= week_start <= 6):
        raise ValueError("Çalışma haftasının başlangıç günü geçersiz.")
    return {
        "weekly_limit_hours": round(float(hours), 2), "week_start": week_start,
        "daily_limit_hours": round(float(daily), 2),
        "break_rules": normalize_break_rules(settings.get("break_rules")),
    }


def normalize_workbook_settings(settings: Any) -> dict[str, Any]:
    if not isinstance(settings, dict) or set(settings) != {"sheets", "quick_colors"}:
        raise ValueError("Tablo ayarları geçersiz.")
    if not isinstance(settings["sheets"], dict) or len(settings["sheets"]) > 300:
        raise ValueError("En fazla 300 çalışma sayfası ayarlanabilir.")
    if not isinstance(settings["quick_colors"], dict) or len(settings["quick_colors"]) > 100:
        raise ValueError("Hızlı giriş renk listesi geçersiz.")
    colors = {}
    for key, value in settings["quick_colors"].items():
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ValueError("Hızlı giriş renk kimliği geçersiz.")
        colors[key] = normalize_color(value)
    sheets = {}
    for key, value in settings["sheets"].items():
        match = re.fullmatch(r"(schedule|attendance):(\d{4}-\d{2}|template):([0-5])", key)
        if not match:
            raise ValueError("Çalışma sayfası kimliği geçersiz.")
        kind, month, index = match.groups()
        if month == "template":
            if kind == "attendance" and index != "0":
                raise ValueError("Çalışma sayfası haftası geçersiz.")
        else:
            validate_month(month)
            if int(index) >= (len(week_starts(month)) if kind == "schedule" else 1):
                raise ValueError("Çalışma sayfası haftası geçersiz.")
        if (not isinstance(value, dict) or not {"cells", "hide_notes", "zoom"}.issubset(value)
                or set(value) - {"cells", "hide_notes", "zoom", "extra_rows", "extra_cols",
                                 "insert_rows", "insert_cols", "delete_rows", "delete_cols",
                                 "row_heights", "column_widths", "merges_add", "merges_remove",
                                 "page_setup"}):
            raise ValueError("Çalışma sayfası ayarları geçersiz.")
        extra_rows = value.get("extra_rows", 0)
        extra_cols = value.get("extra_cols", 0)
        if (not isinstance(extra_rows, int) or isinstance(extra_rows, bool) or not 0 <= extra_rows <= 50
                or not isinstance(extra_cols, int) or isinstance(extra_cols, bool) or not 0 <= extra_cols <= 20):
            raise ValueError("Ek satır veya sütun sayısı geçersiz.")
        if not isinstance(value["hide_notes"], bool):
            raise ValueError("Not görünürlüğü geçersiz.")
        zoom = value["zoom"]
        if not isinstance(zoom, int) or isinstance(zoom, bool) or not 25 <= zoom <= 150:
            raise ValueError("Tablo ölçeği %25-%150 arasında olmalıdır.")
        if not isinstance(value["cells"], dict) or len(value["cells"]) > 5000:
            raise ValueError("Hücre ayarları geçersiz.")
        template = workbook_template(kind, (1 if int(index) >= 5 else int(index)) if kind == "schedule" else 0)
        row_map, col_map, merges = _inserted_layout(
            template, extra_rows, extra_cols, value.get("insert_rows", []), value.get("insert_cols", []),
            value.get("delete_rows", []), value.get("delete_cols", []),
        )
        # Optional extra personnel rows are inserted above the manager row only when filled.
        max_rows = len(row_map) + (_schedule_extra_row_allowance() if kind == "schedule" else 0)
        cells = {}
        for address, cell in value["cells"].items():
            coord = re.fullmatch(r"([1-9]\d*):([1-9]\d*)", address)
            if not coord or not isinstance(cell, dict) or not cell or set(cell) - CELL_FORMAT_KEYS:
                raise ValueError("Hücre biçimi geçersiz.")
            row, col = map(int, coord.groups())
            if row > max_rows or col > len(col_map):
                raise ValueError("Hücre şablonun dışında.")
            normalized_cell = {}
            if "color" in cell:
                normalized_cell["color"] = normalize_color(cell["color"])
            if "font_color" in cell:
                normalized_cell["font_color"] = normalize_color(cell["font_color"])
            if "font_size" in cell:
                size = cell["font_size"]
                if not isinstance(size, int) or isinstance(size, bool) or not 6 <= size <= 72:
                    raise ValueError("Yazı boyutu 6 ile 72 arasında olmalıdır.")
                normalized_cell["font_size"] = size
            for name in ("font_bold", "font_italic", "font_underline", "font_strike"):
                if name in cell:
                    if not isinstance(cell[name], bool):
                        raise ValueError("Yazı biçimi geçersiz.")
                    normalized_cell[name] = cell[name]
            if "font_name" in cell:
                if cell["font_name"] not in FONT_NAMES:
                    raise ValueError("Yazı tipi geçersiz.")
                normalized_cell["font_name"] = cell["font_name"]
            if "text_rotation" in cell:
                rotation = cell["text_rotation"]
                if (not isinstance(rotation, int) or isinstance(rotation, bool)
                        or not (0 <= rotation <= 180 or rotation == 255)):
                    raise ValueError("Metin yönü 0-180 derece arasında olmalıdır.")
                normalized_cell["text_rotation"] = rotation
            if "border" in cell:
                normalized_cell["border"] = _normalize_border(cell["border"])
            for name, options in (("horizontal", ("left", "center", "right")),
                                  ("vertical", ("top", "center", "bottom"))):
                if name in cell:
                    if cell[name] not in options:
                        raise ValueError("Hücre hizalaması geçersiz.")
                    normalized_cell[name] = cell[name]
            for name in ("shrink_to_fit", "wrap_text"):
                if name in cell:
                    if not isinstance(cell[name], bool):
                        raise ValueError("Metin sığdırma ayarı geçersiz.")
                    normalized_cell[name] = cell[name]
            if normalized_cell.get("shrink_to_fit") and normalized_cell.get("wrap_text"):
                raise ValueError("Metni kaydırma ve sığdırma birlikte kullanılamaz.")
            if "text" in cell:
                source_row = row_map[row - 1] if row <= len(row_map) else None
                source_col = col_map[col - 1]
                if source_row is not None and source_row < 0:
                    raise ValueError("Ek personel satırlarının metni vardiya listesinden düzenlenmelidir.")
                if (source_row is not None and source_col is not None
                        and source_row < (34 if kind == "schedule" else 25)
                        and source_col <= len(template["columns"])):
                    raise ValueError("Yalnızca tablonun alt not metinleri düzenlenebilir.")
                if not isinstance(cell["text"], str) or len(cell["text"]) > 2000:
                    raise ValueError("Not metni en fazla 2000 karakter olabilir.")
                if cell["text"].startswith(("=", "+", "-", "@")):
                    raise ValueError("Not metni formül işaretiyle başlayamaz.")
                if any(
                    merge["row"] <= row < merge["row"] + merge["rowspan"]
                    and merge["column"] <= col < merge["column"] + merge["colspan"]
                    and (row, col) != (merge["row"], merge["column"])
                    for merge in merges
                ):
                    raise ValueError("Not metnini birleşik hücrenin başlangıcına girin.")
                normalized_cell["text"] = cell["text"]
            cells[address] = normalized_cell
        sheets[key] = {"cells": cells, "hide_notes": value["hide_notes"], "zoom": zoom}
        for field, limit, maximum in (("row_heights", max_rows, 409), ("column_widths", len(col_map), 255)):
            if field in value:
                dimensions = value[field]
                if not isinstance(dimensions, dict) or len(dimensions) > limit:
                    raise ValueError("Satır veya sütun ölçüleri geçersiz.")
                normalized = {}
                for position, size in dimensions.items():
                    if not isinstance(position, str) or not re.fullmatch(r"[1-9]\d*", position) or int(position) > limit:
                        raise ValueError("Satır veya sütun konumu geçersiz.")
                    if not isinstance(size, (int, float)) or isinstance(size, bool) or not 1 <= size <= maximum:
                        raise ValueError("Satır veya sütun ölçüsü geçersiz.")
                    normalized[position] = size
                sheets[key][field] = normalized
        if "extra_rows" in value:
            sheets[key]["extra_rows"] = extra_rows
        if "extra_cols" in value:
            sheets[key]["extra_cols"] = extra_cols
        for field in ("insert_rows", "insert_cols", "delete_rows", "delete_cols"):
            if field in value:
                sheets[key][field] = value[field][:]
        data_limit = 34 if kind == "schedule" else 25

        def is_data_cell(row: int, col: int) -> bool:
            source_row = row_map[row - 1] if row <= len(row_map) else None
            source_col = col_map[col - 1] if col <= len(col_map) else None
            return (source_row is not None and source_col is not None
                    and 0 < source_row < data_limit and source_col <= len(template["columns"]))

        if "merges_remove" in value:
            removals = value["merges_remove"]
            if not isinstance(removals, list) or len(removals) > 100:
                raise ValueError("Ayrılacak birleşik hücre listesi geçersiz.")
            anchors = {f"{merge['row']}:{merge['column']}": merge for merge in merges}
            normalized_removals = []
            for anchor in removals:
                if not isinstance(anchor, str) or anchor not in anchors or anchor in normalized_removals:
                    raise ValueError("Ayrılacak birleşik hücre bulunamadı.")
                merge = anchors[anchor]
                if any(is_data_cell(r, c)
                       for r in range(merge["row"], merge["row"] + merge["rowspan"])
                       for c in range(merge["column"], merge["column"] + merge["colspan"])):
                    raise ValueError("Vardiya veya puantaj veri alanındaki birleşik hücreler ayrılamaz.")
                normalized_removals.append(anchor)
            sheets[key]["merges_remove"] = normalized_removals
            merges = [merge for merge in merges
                      if f"{merge['row']}:{merge['column']}" not in normalized_removals]
        if "merges_add" in value:
            additions = value["merges_add"]
            if not isinstance(additions, list) or len(additions) > 100:
                raise ValueError("Birleştirilecek hücre listesi geçersiz.")
            occupied = {(r, c) for merge in merges
                        for r in range(merge["row"], merge["row"] + merge["rowspan"])
                        for c in range(merge["column"], merge["column"] + merge["colspan"])}
            normalized_additions = []
            for merge in additions:
                if (not isinstance(merge, dict) or set(merge) != {"row", "column", "rowspan", "colspan"}
                        or any(not isinstance(merge[name], int) or isinstance(merge[name], bool)
                               or merge[name] < 1 for name in merge)):
                    raise ValueError("Birleştirilecek hücre aralığı geçersiz.")
                if merge["rowspan"] * merge["colspan"] < 2:
                    raise ValueError("Birleştirmek için en az iki hücre seçin.")
                if (merge["row"] + merge["rowspan"] - 1 > max_rows
                        or merge["column"] + merge["colspan"] - 1 > len(col_map)):
                    raise ValueError("Birleştirilecek aralık sayfanın dışında.")
                area = {(r, c) for r in range(merge["row"], merge["row"] + merge["rowspan"])
                        for c in range(merge["column"], merge["column"] + merge["colspan"])}
                if area & occupied:
                    raise ValueError("Birleştirilecek aralık başka bir birleşik hücreyle çakışıyor.")
                if any(is_data_cell(r, c) for r, c in area):
                    raise ValueError("Vardiya veya puantaj veri alanındaki hücreler birleştirilemez.")
                occupied |= area
                normalized_additions.append(dict(merge))
            sheets[key]["merges_add"] = normalized_additions
        if "page_setup" in value:
            sheets[key]["page_setup"] = _normalize_page_setup(value["page_setup"])
    return {"sheets": sheets, "quick_colors": colors}


def _prune_sheet_settings(key: str, sheet: dict[str, Any]) -> dict[str, Any]:
    """Drop stored entries that fall outside the current sheet layout."""
    kind, _, index = key.split(":")
    template = workbook_template(kind, (1 if int(index) >= 5 else int(index)) if kind == "schedule" else 0)
    extra_rows = sheet.get("extra_rows", 0) if isinstance(sheet.get("extra_rows"), int) else 0
    extra_cols = sheet.get("extra_cols", 0) if isinstance(sheet.get("extra_cols"), int) else 0
    row_map, col_map, _ = _inserted_layout(
        template, max(0, min(extra_rows, 50)), max(0, min(extra_cols, 20)),
        sheet.get("insert_rows", []), sheet.get("insert_cols", []),
        sheet.get("delete_rows", []), sheet.get("delete_cols", []),
    )
    max_rows = len(row_map) + (_schedule_extra_row_allowance() if kind == "schedule" else 0)
    max_cols = len(col_map)

    def inside(row: Any, col: Any) -> bool:
        return isinstance(row, int) and isinstance(col, int) and 1 <= row <= max_rows and 1 <= col <= max_cols

    pruned = dict(sheet)
    pruned["cells"] = {
        address: cell for address, cell in (sheet.get("cells") or {}).items()
        if re.fullmatch(r"[1-9]\d*:[1-9]\d*", str(address)) and inside(*map(int, str(address).split(":")))
    }
    for field, limit in (("row_heights", max_rows), ("column_widths", max_cols)):
        if isinstance(sheet.get(field), dict):
            pruned[field] = {position: size for position, size in sheet[field].items()
                             if str(position).isdigit() and 1 <= int(position) <= limit}
    if isinstance(sheet.get("merges_add"), list):
        pruned["merges_add"] = [
            merge for merge in sheet["merges_add"]
            if isinstance(merge, dict) and inside(merge.get("row"), merge.get("column"))
            and isinstance(merge.get("rowspan"), int) and isinstance(merge.get("colspan"), int)
            and inside(merge["row"] + merge["rowspan"] - 1, merge["column"] + merge["colspan"] - 1)
        ]
    return pruned


def repair_workbook_settings(settings: Any) -> dict[str, Any]:
    """Return stored workbook settings with invalid sheets/cells removed.

    Settings restored from older backups may contain cells outside the current layout; one
    such sheet would otherwise make every later formatting save fail validation.
    """
    raw = settings if isinstance(settings, dict) else {}
    try:
        colors = normalize_workbook_settings(
            {"sheets": {}, "quick_colors": raw.get("quick_colors") or {}})["quick_colors"]
    except (ValueError, TypeError, AttributeError):
        colors = {}
    sheets: dict[str, Any] = {}
    stored = raw.get("sheets") if isinstance(raw.get("sheets"), dict) else {}
    for key, sheet in stored.items():
        try:
            sheets.update(normalize_workbook_settings({"sheets": {key: sheet}, "quick_colors": {}})["sheets"])
            continue
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        try:
            candidate = _prune_sheet_settings(key, sheet)
            sheets.update(normalize_workbook_settings({"sheets": {key: candidate}, "quick_colors": {}})["sheets"])
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return {"sheets": sheets, "quick_colors": colors}


FONT_NAMES = (
    "Arial", "Calibri", "Segoe UI", "Times New Roman", "Verdana", "Tahoma", "Courier New",
    "Georgia", "Cambria", "Trebuchet MS", "Arial Narrow", "Consolas",
)
BORDER_STYLES = ("none", "thin", "medium", "thick", "double", "dashed", "dotted", "hair")
CELL_FORMAT_KEYS = {
    "color", "text", "font_color", "font_size", "font_bold", "font_italic", "font_underline",
    "font_strike", "font_name", "horizontal", "vertical", "shrink_to_fit", "wrap_text",
    "text_rotation", "border",
}
CELL_STYLE_KEYS = CELL_FORMAT_KEYS - {"text"}


def _normalize_border(border: Any) -> dict[str, dict[str, str]]:
    if not isinstance(border, dict) or not border or set(border) - {"top", "bottom", "left", "right"}:
        raise ValueError("Kenarlık ayarı geçersiz.")
    normalized = {}
    for side, value in border.items():
        if not isinstance(value, dict) or set(value) - {"style", "color"} or value.get("style") not in BORDER_STYLES:
            raise ValueError("Kenarlık çizgi stili geçersiz.")
        normalized[side] = {"style": value["style"], "color": normalize_color(value.get("color", "#000000"))}
    return normalized


def _normalize_page_setup(setup: Any) -> dict[str, Any]:
    allowed = {"print_area", "orientation", "paper", "fit_width", "fit_height", "margins",
               "print_title_rows", "center_horizontally", "header_text", "footer_text",
               "page_numbers", "code_dropdown", "highlight_codes"}
    if not isinstance(setup, dict) or set(setup) - allowed:
        raise ValueError("Sayfa yapısı ayarları geçersiz.")
    normalized: dict[str, Any] = {}
    area = setup.get("print_area", "")
    if area:
        if not isinstance(area, str) or not re.fullmatch(r"[A-Z]{1,3}[1-9]\d{0,4}:[A-Z]{1,3}[1-9]\d{0,4}", area.upper()):
            raise ValueError("Yazdırma alanı A1:Z99 biçiminde olmalıdır.")
        first, last = area.upper().split(":")
        first_col, first_row = coordinate_from_string(first)
        last_col, last_row = coordinate_from_string(last)
        if column_index_from_string(first_col) > column_index_from_string(last_col) or first_row > last_row:
            raise ValueError("Yazdırma alanının başlangıcı bitişinden sonra olamaz.")
        normalized["print_area"] = area.upper()
    if setup.get("orientation"):
        if setup["orientation"] not in ("portrait", "landscape"):
            raise ValueError("Sayfa yönü geçersiz.")
        normalized["orientation"] = setup["orientation"]
    if setup.get("paper"):
        if setup["paper"] not in ("A4", "A3", "A5", "Letter"):
            raise ValueError("Kâğıt boyutu geçersiz.")
        normalized["paper"] = setup["paper"]
    for name in ("fit_width", "fit_height"):
        if name in setup:
            number = setup[name]
            if not isinstance(number, int) or isinstance(number, bool) or not 0 <= number <= 20:
                raise ValueError("Sayfaya sığdırma değeri 0-20 arasında olmalıdır.")
            normalized[name] = number
    if "margins" in setup:
        margins = setup["margins"]
        if not isinstance(margins, dict) or set(margins) - {"top", "bottom", "left", "right"}:
            raise ValueError("Kenar boşlukları geçersiz.")
        normalized["margins"] = {}
        for side, number in margins.items():
            if (not isinstance(number, (int, float)) or isinstance(number, bool)
                    or not math.isfinite(number) or not 0 <= number <= 10):
                raise ValueError("Kenar boşluğu 0-10 cm arasında olmalıdır.")
            normalized["margins"][side] = round(float(number), 2)
    rows = setup.get("print_title_rows", "")
    if rows:
        match = isinstance(rows, str) and re.fullmatch(r"([1-9]\d{0,3}):([1-9]\d{0,3})", rows)
        if not match or int(match.group(1)) > int(match.group(2)):
            raise ValueError("Her sayfada yinelenecek satırlar 1:3 biçiminde olmalıdır.")
        normalized["print_title_rows"] = rows
    if "center_horizontally" in setup:
        if not isinstance(setup["center_horizontally"], bool):
            raise ValueError("Yatay ortalama ayarı geçersiz.")
        normalized["center_horizontally"] = setup["center_horizontally"]
    for name in ("header_text", "footer_text"):
        text = setup.get(name, "")
        if not isinstance(text, str) or len(text) > 120 or any(ord(ch) < 32 for ch in text):
            raise ValueError("Üst/alt bilgi metni en fazla 120 karakter olmalıdır.")
        if text.strip():
            normalized[name] = text.strip()
    for name in ("page_numbers", "code_dropdown", "highlight_codes"):
        if name in setup:
            if not isinstance(setup[name], bool):
                raise ValueError("Sayfa yapısı açık/kapalı ayarı geçersiz.")
            if setup[name]:
                normalized[name] = True
    return normalized


def _remove_layout_merges(ws, sheet: dict[str, Any]) -> None:
    for anchor in sheet.get("merges_remove", []):
        row, col = map(int, anchor.split(":"))
        for merged in list(ws.merged_cells.ranges):
            if merged.min_row == row and merged.min_col == col:
                ws.unmerge_cells(str(merged))
                break


def _apply_custom_merges(ws, sheet: dict[str, Any]) -> None:
    for merge in sheet.get("merges_add", []):
        ws.merge_cells(
            start_row=merge["row"], start_column=merge["column"],
            end_row=merge["row"] + merge["rowspan"] - 1,
            end_column=merge["column"] + merge["colspan"] - 1,
        )


def _apply_page_setup(ws, sheet: dict[str, Any]) -> None:
    setup = sheet.get("page_setup") or {}
    if setup.get("print_area"):
        ws.print_area = setup["print_area"]
    if setup.get("orientation"):
        ws.page_setup.orientation = setup["orientation"]
    if setup.get("paper"):
        ws.page_setup.paperSize = {"A4": 9, "A3": 8, "A5": 11, "Letter": 1}[setup["paper"]]
    if "fit_width" in setup or "fit_height" in setup:
        width, height = setup.get("fit_width", 1), setup.get("fit_height", 0)
        ws.sheet_properties.pageSetUpPr.fitToPage = bool(width or height)
        ws.page_setup.fitToWidth = width
        ws.page_setup.fitToHeight = height
    for side, value in setup.get("margins", {}).items():
        setattr(ws.page_margins, side, round(value / 2.54, 3))
    if setup.get("print_title_rows"):
        ws.print_title_rows = setup["print_title_rows"]
    if "center_horizontally" in setup:
        ws.print_options.horizontalCentered = setup["center_horizontally"]
    if setup.get("header_text"):
        ws.oddHeader.center.text = setup["header_text"].replace("&", "&&")
    if setup.get("footer_text"):
        ws.oddFooter.left.text = setup["footer_text"].replace("&", "&&")
    if setup.get("page_numbers"):
        ws.oddFooter.right.text = "Sayfa &P / &N"


ATTENDANCE_HIGHLIGHTS = {"R": "F8CBAD", "S": "C6EFCE", "Ü": "D9D9D9", "İST.": "FFEB9C", "E": "BDD7EE"}


def _apply_attendance_helpers(ws, setup: dict[str, Any], cell_ranges: list[str], codes: set[str]) -> None:
    """Optional Excel helpers on the day cells: a non-blocking code dropdown and code highlighting."""
    if not cell_ranges:
        return
    if setup.get("code_dropdown"):
        ordered = [code for code in ("X", "H", "G.Ç", "S", "R", "Ü", "İST.", "E", "K", "Y") if code]
        ordered += sorted(code for code in codes if code and code not in ordered and "," not in code and '"' not in code)
        formula = ",".join(ordered)
        while len(formula) > 250:
            ordered.pop()
            formula = ",".join(ordered)
        validation = DataValidation(type="list", formula1=f'"{formula}"', allow_blank=True,
                                    showErrorMessage=False, showDropDown=False)
        validation.sqref = MultiCellRange(" ".join(cell_ranges))
        ws.add_data_validation(validation)
    if setup.get("highlight_codes"):
        for cell_range in cell_ranges:
            top_left = cell_range.split(":")[0]
            for code, color in ATTENDANCE_HIGHLIGHTS.items():
                ws.conditional_formatting.add(cell_range, FormulaRule(
                    formula=[f'EXACT({top_left},"{code}")'],
                    fill=PatternFill("solid", start_color=color, end_color=color),
                ))


def _apply_workbook_settings(ws, settings: dict[str, Any], kind: str, month: str, index: int) -> None:
    sheet = sheet_settings_for(settings, kind, month, index)
    base_row, base_col = ws.max_row, ws.max_column
    extra_rows, extra_cols = sheet.get("extra_rows", 0), sheet.get("extra_cols", 0)
    if extra_rows or extra_cols:
        for column in range(base_col + 1, base_col + extra_cols + 1):
            ws.column_dimensions[get_column_letter(column)].width = 12
        for row in range(base_row + 1, base_row + extra_rows + 1):
            ws.row_dimensions[row].height = ws.sheet_format.defaultRowHeight or 15
        ws.cell(base_row + extra_rows, base_col + extra_cols)
    for row in ws:
        for cell in row:
            cell.fill = PatternFill(fill_type="solid", fgColor="FFFFFF")
            font = copy(cell.font)
            font.color = "202020"
            cell.font = font
    if sheet.get("hide_notes"):
        for row in range(34 if kind == "schedule" else 25, ws.max_row + 1):
            ws.row_dimensions[row].hidden = True
    if "zoom" in sheet:
        ws.sheet_view.zoomScale = sheet["zoom"]


_CELL_REFERENCE = re.compile(r"(?<![A-Za-z0-9_])(\$?)([A-Z]{1,3})(\$?)([1-9]\d*)(?![A-Za-z0-9_])")


def _insert_worksheet_axis(ws, axis: str, position: int) -> None:
    previous_rows, previous_cols = ws.max_row, ws.max_column
    ranges = [tuple(merged.bounds) for merged in ws.merged_cells.ranges]
    merged_styles = {
        (row, col): copy(ws.cell(row, col)._style)
        for min_col, min_row, max_col, max_row in ranges
        for row in range(min_row, max_row + 1)
        for col in range(min_col, max_col + 1)
    }
    for merged in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merged))
    if axis == "row":
        dimensions = [(key, copy(value)) for key, value in ws.row_dimensions.items()]
        ws.row_dimensions.clear()
        for key, dimension in dimensions:
            target = key + (key >= position)
            dimension.index = target
            ws.row_dimensions[target] = dimension
        ws.insert_rows(position)
        ws.row_dimensions[position].height = ws.sheet_format.defaultRowHeight or 15
        ws.cell(previous_rows + 1, previous_cols)
    else:
        dimensions = [(key, copy(value)) for key, value in ws.column_dimensions.items()]
        ws.column_dimensions.clear()
        for key, dimension in dimensions:
            col = column_index_from_string(key)
            target = col + (col >= position)
            dimension.index = get_column_letter(target)
            if dimension.min is not None:
                dimension.min += dimension.min >= position
            if dimension.max is not None:
                dimension.max += dimension.max >= position
            ws.column_dimensions[dimension.index] = dimension
        ws.insert_cols(position)
        ws.column_dimensions[get_column_letter(position)].width = 12
        ws.cell(previous_rows, previous_cols + 1)

    def shifted(row: int, col: int) -> tuple[int, int]:
        return (row + (axis == "row" and row >= position),
                col + (axis == "column" and col >= position))

    for min_col, min_row, max_col, max_row in ranges:
        new_min_row, new_min_col = shifted(min_row, min_col)
        new_max_row, new_max_col = shifted(max_row, max_col)
        ws.merge_cells(start_row=new_min_row, start_column=new_min_col,
                       end_row=new_max_row, end_column=new_max_col)
    for (row, col), style in merged_styles.items():
        new_row, new_col = shifted(row, col)
        ws.cell(new_row, new_col)._style = style
    for row in ws:
        for cell in row:
            if cell.data_type != "f":
                continue
            def replace(match):
                col = column_index_from_string(match.group(2))
                row_num = int(match.group(4))
                row_num, col = shifted(row_num, col)
                return f"{match.group(1)}{get_column_letter(col)}{match.group(3)}{row_num}"
            cell.value = _CELL_REFERENCE.sub(replace, cell.value)


def _delete_worksheet_axis(ws, axis: str, position: int) -> None:
    ranges = [tuple(merged.bounds) for merged in ws.merged_cells.ranges]
    if axis == "row":
        intersects_merge = any(min_row <= position <= max_row
                               for _, min_row, _, max_row in ranges)
    else:
        intersects_merge = any(min_col <= position <= max_col
                               for min_col, _, max_col, _ in ranges)
    if intersects_merge:
        raise ValueError("Birleşik hücre içeren satır veya sütun silinemez.")
    merged_styles = {
        (row, col): copy(ws.cell(row, col)._style)
        for min_col, min_row, max_col, max_row in ranges
        for row in range(min_row, max_row + 1)
        for col in range(min_col, max_col + 1)
    }
    for merged in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merged))
    if axis == "row":
        dimensions = [(key, copy(value)) for key, value in ws.row_dimensions.items()]
        ws.row_dimensions.clear()
        ws.delete_rows(position, 1)
        for key, dimension in dimensions:
            if key == position:
                continue
            target = key - (key > position)
            dimension.index = target
            ws.row_dimensions[target] = dimension
    else:
        dimensions = [(key, copy(value)) for key, value in ws.column_dimensions.items()]
        ws.column_dimensions.clear()
        ws.delete_cols(position, 1)
        for key, dimension in dimensions:
            col = column_index_from_string(key)
            if col == position:
                continue
            target = col - (col > position)
            dimension.index = get_column_letter(target)
            if dimension.min is not None:
                dimension.min -= dimension.min > position
            if dimension.max is not None:
                dimension.max -= dimension.max > position
            ws.column_dimensions[dimension.index] = dimension

    def shifted(row: int, col: int) -> tuple[int, int]:
        if axis == "row" and row > position:
            row -= 1
        if axis == "column" and col > position:
            col -= 1
        return row, col

    for min_col, min_row, max_col, max_row in ranges:
        new_min_row, new_min_col = shifted(min_row, min_col)
        new_max_row, new_max_col = shifted(max_row, max_col)
        ws.merge_cells(start_row=new_min_row, start_column=new_min_col,
                       end_row=new_max_row, end_column=new_max_col)
    for (row, col), style in merged_styles.items():
        new_row, new_col = shifted(row, col)
        ws.cell(new_row, new_col)._style = style
    for row in ws:
        for cell in row:
            if cell.data_type != "f":
                continue

            def replace(match):
                col = column_index_from_string(match.group(2))
                row_num = int(match.group(4))
                row_num, col = shifted(row_num, col)
                return f"{match.group(1)}{get_column_letter(col)}{match.group(3)}{row_num}"

            cell.value = _CELL_REFERENCE.sub(replace, cell.value)


def _insert_worksheet_cells(
    ws, settings: dict[str, Any], kind: str, month: str, index: int, extra_row_count: int = 0,
    before_custom=None,
) -> list[int]:
    sheet = sheet_settings_for(settings, kind, month, index)
    template = workbook_template(kind, (1 if kind == "schedule" and index >= 5 else index))
    extra_rows, extra_cols = sheet.get("extra_rows", 0), sheet.get("extra_cols", 0)
    for axis, positions in (("row", sheet.get("insert_rows", [])), ("column", sheet.get("insert_cols", []))):
        for position in positions:
            _insert_worksheet_axis(ws, axis, position)

    row_map, col_map, _ = _inserted_layout(
        template, extra_rows, extra_cols,
        sheet.get("insert_rows", []), sheet.get("insert_cols", []),
    )
    row_positions = sorted(
        (row_map.index(source_row) + 1 for source_row in sheet.get("delete_rows", [])),
        reverse=True,
    )
    col_positions = sorted(
        (col_map.index(source_col) + 1 for source_col in sheet.get("delete_cols", [])),
        reverse=True,
    )
    for position in row_positions:
        _delete_worksheet_axis(ws, "row", position)
    for position in col_positions:
        _delete_worksheet_axis(ws, "column", position)

    _remove_layout_merges(ws, sheet)
    extra_schedule_rows = []
    if kind == "schedule":
        row_map, _, _ = _inserted_layout(
            template, extra_rows, extra_cols,
            sheet.get("insert_rows", []), sheet.get("insert_cols", []),
            sheet.get("delete_rows", []), sheet.get("delete_cols", []),
        )
        manager_position = row_map.index(35) + 1
        for offset in range(extra_row_count):
            position = manager_position + offset
            _insert_worksheet_axis(ws, "row", position)
            extra_schedule_rows.append(position)
    if before_custom:
        # Data-driven rows are placed before custom cells/merges, whose coordinates include them.
        before_custom()

    _apply_custom_merges(ws, sheet)
    for address, value in sheet.get("cells", {}).items():
        if "text" in value:
            row, col = map(int, address.split(":"))
            ws.cell(row, col).value = value["text"]
    return extra_schedule_rows


def _apply_cell_colors(ws, settings: dict[str, Any], kind: str, month: str, index: int) -> None:
    sheet = sheet_settings_for(settings, kind, month, index)
    for row, height in sheet.get("row_heights", {}).items():
        ws.row_dimensions[int(row)].height = height
    for col, width in sheet.get("column_widths", {}).items():
        ws.column_dimensions[get_column_letter(int(col))].width = width
    for address, value in sheet.get("cells", {}).items():
        if not set(value).intersection(CELL_STYLE_KEYS):
            continue
        row, col = map(int, address.split(":"))
        targets = [(row, col)]
        bounds = (row, col, row, col)
        for merged in ws.merged_cells.ranges:
            if ws.cell(row, col).coordinate in merged:
                targets = [(r, c) for r in range(merged.min_row, merged.max_row + 1)
                           for c in range(merged.min_col, merged.max_col + 1)]
                bounds = (merged.min_row, merged.min_col, merged.max_row, merged.max_col)
                break
        for r, c in targets:
            cell = ws.cell(r, c)
            if "color" in value:
                _set_fill(cell, value["color"])
            font = copy(cell.font)
            if "font_color" in value: font.color = value["font_color"][1:].upper()
            if "font_size" in value: font.sz = value["font_size"]
            if "font_bold" in value: font.bold = value["font_bold"]
            if "font_italic" in value: font.italic = value["font_italic"]
            if "font_underline" in value: font.underline = "single" if value["font_underline"] else None
            if "font_strike" in value: font.strike = value["font_strike"]
            if "font_name" in value: font.name = value["font_name"]
            cell.font = font
            alignment = copy(cell.alignment)
            if "horizontal" in value: alignment.horizontal = value["horizontal"]
            if "vertical" in value: alignment.vertical = value["vertical"]
            if "shrink_to_fit" in value: alignment.shrinkToFit = value["shrink_to_fit"]
            if "wrap_text" in value: alignment.wrapText = value["wrap_text"]
            if "text_rotation" in value: alignment.textRotation = value["text_rotation"]
            cell.alignment = alignment
            if "border" in value:
                sides = {}
                edge = {"top": r == bounds[0], "left": c == bounds[1],
                        "bottom": r == bounds[2], "right": c == bounds[3]}
                for side, setting in value["border"].items():
                    if edge[side]:
                        sides[side] = Side(
                            style=None if setting["style"] == "none" else setting["style"],
                            color=setting["color"][1:].upper(),
                        )
                if sides:
                    border = copy(cell.border)
                    for side, item in sides.items():
                        setattr(border, side, item)
                    cell.border = border


def _set_fill(cell, color: str) -> None:
    cell.fill = PatternFill(fill_type="solid", fgColor=color[1:].upper())
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
              for channel in channels]
    luminance = sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    font = copy(cell.font)
    font.color = "000000" if luminance > 0.179 else "FFFFFF"
    cell.font = font


def _validate_time(value: Any) -> str:
    if value == "24:00":
        return "00:00"
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Vardiya saatleri SS:DD biçiminde olmalıdır (gece yarısı için 24:00).")
    return value


def _shift_type(value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"[^\s<>]{1,30}", value.strip()):
        raise ValueError("Vardiya türü en fazla 30 karakter olmalıdır.")
    return value.strip()


def normalize_schedule_entries(
    month: str,
    entries: Any,
    staff: list[dict[str, Any]],
    *,
    validated_days: dict[tuple[str, str], dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    validate_month(month)
    if not isinstance(entries, list) or len(entries) > 300:
        raise ValueError("Vardiya satır sayısı 0-300 arasında olmalıdır.")
    people = {int(person["id"]) for person in staff}
    seen_rows: set[str] = set()
    seen_assignments: set[tuple[int, str]] = set()
    seen_slots: dict[str, set[int]] = defaultdict(set)
    allowed_dates = {item.isoformat() for item in month_dates(month)}
    cleaned = []
    section_counts = defaultdict(int)
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError("Vardiya satırı geçersiz.")
        row_id = row.get("row_id")
        employee_id = row.get("employee_id")
        section = row.get("section")
        days = row.get("days", {})
        if not isinstance(row_id, str) or not row_id.strip() or len(row_id) > 80 or row_id in seen_rows:
            raise ValueError("Vardiya satırı kimliği eksik veya yineleniyor.")
        if not isinstance(employee_id, int) or isinstance(employee_id, bool) or employee_id not in people:
            raise ValueError("Vardiya satırındaki personel bulunamadı.")
        if section not in section_names():
            raise ValueError("Vardiya bölümü tanımlı bölümlerden biri olmalıdır.")
        slot = row.get("slot")
        if slot is not None:
            if (
                not isinstance(slot, int)
                or isinstance(slot, bool)
                or slot < 1
                or slot > section_capacity(section)
            ):
                raise ValueError("Vardiya satırının şablon konumu geçersiz.")
            if slot in seen_slots[section]:
                raise ValueError("Aynı bölümde iki personel aynı şablon satırına atanamaz.")
            seen_slots[section].add(slot)
        section_counts[section] += 1
        if section_counts[section] > section_capacity(section):
            raise ValueError(
                f"{section} bölümünde en fazla {section_capacity(section)} personel satırı olabilir. "
                "Satır sayısını Araçlar › Bölümler ve satırlar penceresinden artırabilirsiniz."
            )
        assignment = (employee_id, section)
        if assignment in seen_assignments:
            raise ValueError("Aynı personel aynı bölümde birden fazla satıra eklenemez.")
        if not isinstance(days, dict):
            raise ValueError("Günlük vardiya girişleri geçersiz.")
        normalized_days: dict[str, dict[str, str]] = {}
        for day_text, entry in days.items():
            if day_text not in allowed_dates:
                raise ValueError("Vardiya tarihi seçili ayın dışında.")
            if not isinstance(entry, dict):
                raise ValueError("Günlük vardiya kaydı geçersiz.")
            previous = validated_days.get((row_id, day_text)) if validated_days is not None else None
            if previous is not None and entry == previous:
                normalized_days[day_text] = previous.copy()
                continue
            kind = entry.get("kind", "")
            if kind not in SCHEDULE_KINDS:
                raise ValueError("Vardiya türü geçersiz.")
            if kind == "":
                continue
            if kind == "work":
                start = _validate_time(entry.get("start"))
                end = _validate_time(entry.get("end"))
                if start == end:
                    raise ValueError("Başlangıç ve bitiş saati aynı olamaz.")
                attendance_code = normalize_attendance_code(entry.get("attendance_code", "X"))
                if not attendance_code:
                    raise ValueError("Çalışma puantaj kodu boş olamaz.")
                shift_label = entry.get("shift_label", "")
                if not isinstance(shift_label, str) or len(shift_label.strip()) > 40:
                    raise ValueError("Vardiya adı en fazla 40 karakter olabilir.")
                normalized_days[day_text] = {"kind": kind, "start": start, "end": end}
                shift_type = _shift_type(entry.get("shift_type"))
                if shift_type:
                    normalized_days[day_text]["shift_type"] = shift_type
                if attendance_code != "X":
                    normalized_days[day_text]["attendance_code"] = attendance_code
                if shift_label.strip():
                    normalized_days[day_text]["shift_label"] = " ".join(shift_label.split())
            elif kind == "custom":
                code = normalize_attendance_code(entry.get("custom_code", ""))
                if not code:
                    raise ValueError("Özel vardiya kodu boş olamaz.")
                normalized_days[day_text] = {"kind": kind, "custom_code": code}
                custom_label = entry.get("custom_label", "")
                if not isinstance(custom_label, str) or len(custom_label.strip()) > 40:
                    raise ValueError("Hızlı giriş adı en fazla 40 karakter olabilir.")
                if custom_label.strip():
                    normalized_days[day_text]["custom_label"] = " ".join(custom_label.split())
            else:
                normalized_days[day_text] = {"kind": kind}
            if "fill_color" in entry:
                normalized_days[day_text]["fill_color"] = normalize_color(entry["fill_color"])
            note = entry.get("note", "")
            if not isinstance(note, str) or len(note.strip()) > 200:
                raise ValueError("Hücre notu en fazla 200 karakter olabilir.")
            if note.strip():
                normalized_days[day_text]["note"] = note.strip()
        seen_rows.add(row_id)
        seen_assignments.add(assignment)
        normalized_row = {
            "row_id": row_id.strip(),
            "employee_id": employee_id,
            "section": section,
            "days": normalized_days,
        }
        if slot is not None:
            normalized_row["slot"] = slot
        cleaned.append(normalized_row)

    if len({row["employee_id"] for row in cleaned}) > MAX_MONTH_PERSONNEL:
        raise ValueError(f"Bir ayın çizelgesinde en fazla {MAX_MONTH_PERSONNEL} farklı personel olabilir.")
    for section in section_names():
        section_entries = [row for row in cleaned if row["section"] == section]
        used_slots = {row["slot"] for row in section_entries if "slot" in row}
        free_slots = iter(
            slot for slot in range(1, section_capacity(section) + 1)
            if slot not in used_slots
        )
        unassigned = sorted(
            (row for row in section_entries if "slot" not in row),
            key=lambda row: (row["employee_id"], row["row_id"]),
        )
        for row in unassigned:
            row["slot"] = next(free_slots)
    return cleaned


def normalize_schedule_templates(
    templates: Any,
    staff: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(templates, list) or len(templates) > 30:
        raise ValueError("En fazla 30 vardiya şablonu saklanabilir.")

    seen_ids: set[str] = set()
    cleaned_templates = []
    for template in templates:
        if not isinstance(template, dict) or set(template) != {"id", "name", "entries"}:
            raise ValueError("Vardiya şablonu geçersiz.")
        template_id = template["id"]
        name = template["name"]
        entries = template["entries"]
        if (
            not isinstance(template_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", template_id)
            or template_id in seen_ids
        ):
            raise ValueError("Vardiya şablonu kimliği eksik veya yineleniyor.")
        if not isinstance(name, str) or not 1 <= len(" ".join(name.split())) <= 60:
            raise ValueError("Şablon adı 1-60 karakter olmalıdır.")
        if not isinstance(entries, list) or len(entries) > 27:
            raise ValueError("Bir vardiya şablonunda en fazla 27 personel satırı olabilir.")

        validation_entries = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {
                "employee_id", "section", "slot", "days"
            }:
                raise ValueError("Vardiya şablonundaki personel satırı geçersiz.")
            days = entry["days"]
            if not isinstance(days, dict) or any(
                not isinstance(offset, str) or not re.fullmatch(r"[0-6]", offset)
                for offset in days
            ):
                raise ValueError("Şablon günleri 0-6 arasında olmalıdır.")
            validation_days = {
                f"2026-01-{int(offset) + 1:02d}": item
                for offset, item in days.items()
            }
            validation_entries.append({
                "row_id": f"template-{index}",
                "employee_id": entry["employee_id"],
                "section": entry["section"],
                "slot": entry["slot"],
                "days": validation_days,
            })

        normalized = normalize_schedule_entries("2026-01", validation_entries, staff)
        cleaned_entries = []
        for entry in normalized:
            relative_days = {
                str(int(day[-2:]) - 1): item
                for day, item in entry["days"].items()
            }
            cleaned_entries.append({
                "employee_id": entry["employee_id"],
                "section": entry["section"],
                "slot": entry["slot"],
                "days": relative_days,
            })
        seen_ids.add(template_id)
        cleaned_templates.append({
            "id": template_id,
            "name": " ".join(name.split()),
            "entries": cleaned_entries,
        })
    return cleaned_templates


def normalize_attendance_overrides(month: str, overrides: Any, staff: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    allowed_dates = {item.isoformat() for item in month_dates(month)}
    people = {str(int(person["id"])) for person in staff}
    if not isinstance(overrides, dict):
        raise ValueError("Puantaj kodları geçersiz.")
    cleaned: dict[str, dict[str, str]] = {}
    for day_text, values in overrides.items():
        if day_text not in allowed_dates or not isinstance(values, dict):
            raise ValueError("Puantaj tarihi veya kod listesi geçersiz.")
        day_values = {}
        for employee_id, code in values.items():
            key = str(employee_id)
            if key not in people:
                raise ValueError("Puantaj kodundaki personel bulunamadı.")
            day_values[key] = normalize_attendance_code(code)
        if day_values:
            cleaned[day_text] = day_values
    return cleaned


def normalize_attendance_deductions(values: Any, staff: list[dict[str, Any]]) -> dict[str, float]:
    if not isinstance(values, dict):
        raise ValueError("Kesinti tutarları geçersiz.")
    people = {str(int(person["id"])) for person in staff}
    cleaned: dict[str, float] = {}
    for employee_id, amount in values.items():
        key = str(employee_id)
        if key not in people:
            raise ValueError("Kesinti tutarındaki personel bulunamadı.")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ValueError("Kesinti tutarı sayı olmalıdır.")
        try:
            normalized = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Kesinti tutarı geçersiz.") from exc
        if not normalized.is_finite() or normalized < 0 or normalized > Decimal("999999999.99"):
            raise ValueError("Kesinti tutarı 0 ile 999999999,99 arasında olmalıdır.")
        if normalized:
            cleaned[key] = float(normalized)
    return cleaned


def normalize_attendance_roster(values: Any, staff: list[dict[str, Any]]) -> dict[str, list[int]]:
    if not isinstance(values, dict) or not set(values) <= {"added", "removed", "order"}:
        raise ValueError("Puantaj personel listesi geçersiz.")
    people = {int(person["id"]) for person in staff}
    cleaned: dict[str, list[int]] = {}
    for name in ("added", "removed", "order"):
        items = values.get(name, [])
        if not isinstance(items, list) or len(items) > MAX_MONTH_PERSONNEL * 4:
            raise ValueError("Puantaj personel listesi geçersiz.")
        seen: list[int] = []
        for item in items:
            if isinstance(item, bool) or not isinstance(item, int) or item not in people:
                raise ValueError("Puantaj listesindeki personel bulunamadı.")
            if item not in seen:
                seen.append(item)
        cleaned[name] = seen
    cleaned["added"] = [item for item in cleaned["added"] if item not in cleaned["removed"]]
    return cleaned


def normalize_attendance_overtime(values: Any, staff: list[dict[str, Any]]) -> dict[str, float]:
    if not isinstance(values, dict):
        raise ValueError("Fazla mesai düzeltmeleri geçersiz.")
    people = {str(int(person["id"])) for person in staff}
    cleaned: dict[str, float] = {}
    for employee_id, hours in values.items():
        if str(employee_id) not in people:
            raise ValueError("Fazla mesai düzeltmesindeki personel bulunamadı.")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or not 0 <= hours <= 744:
            raise ValueError("Fazla mesai saati 0 ile 744 arasında olmalıdır.")
        cleaned[str(employee_id)] = round(float(hours), 2)
    return cleaned


def _time_interval(day_text: str, start_text: str, end_text: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(day_text)
    start = datetime.combine(day, time.fromisoformat(start_text))
    end = datetime.combine(day, time.fromisoformat(end_text))
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _union_minutes(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> int:
    clipped = sorted((max(first, start), min(last, end)) for first, last in intervals if first < end and last > start)
    if not clipped:
        return 0
    total = 0
    current_start, current_end = clipped[0]
    for first, last in clipped[1:]:
        if first <= current_end:
            current_end = max(current_end, last)
        else:
            total += int((current_end - current_start).total_seconds() // 60)
            current_start, current_end = first, last
    return total + int((current_end - current_start).total_seconds() // 60)


def daily_overtime_details(
    intervals: list[tuple[datetime, datetime]], limit_minutes: int, break_rules: Any = (),
) -> dict[str, dict[str, int]]:
    """Per shift start day: worked, break, holiday and overtime minutes.

    FM = çalışma − mola − resmî tatil − günlük sınır (en az 0). Overnight shifts belong to the
    day they start; holiday (UBGT) minutes are paid in their own column.
    """
    by_day: dict[date, list[tuple[datetime, datetime]]] = defaultdict(list)
    for start, end in intervals:
        by_day[start.date()].append((start, end))
    result: dict[str, dict[str, int]] = {}
    for day, items in by_day.items():
        begin, finish = min(item[0] for item in items), max(item[1] for item in items)
        worked = _union_minutes(items, begin, finish)
        holiday = 0
        cursor = begin.date()
        while cursor <= finish.date():
            window = holiday_window(cursor)
            if window:
                holiday += _union_minutes(items, *window)
            cursor += timedelta(days=1)
        rest = break_minutes(worked, break_rules)
        result[day.isoformat()] = {
            "worked": worked, "break": rest, "holiday": holiday, "limit": limit_minutes,
            "overtime": max(0, worked - rest - holiday - limit_minutes) if limit_minutes else 0,
        }
    return result


def daily_overtime_minutes(
    intervals: list[tuple[datetime, datetime]], limit_minutes: int, break_rules: Any = (),
) -> dict[str, int]:
    """Fazla mesai per shift start day: worked time beyond the daily limit, excluding holiday hours."""
    return {
        day: item["overtime"]
        for day, item in daily_overtime_details(intervals, limit_minutes, break_rules).items()
    }


def person_overtime_rule(person: dict[str, Any] | None, limit_minutes: int, break_rules: Any) -> tuple[int, Any]:
    """Kişiye özel günlük sınır ve mola muafiyeti (personel kartı)."""
    person = person or {}
    custom = person.get("daily_limit_hours")
    if isinstance(custom, (int, float)) and not isinstance(custom, bool) and custom > 0:
        limit_minutes = int(round(float(custom) * 60))
    return limit_minutes, ([] if person.get("break_exempt") else break_rules)


def weekly_schedule_overtime(
    entries: list[dict[str, Any]], limit_minutes: int, break_rules: Any = (),
    person_rules: dict[int, dict[str, Any]] | None = None,
) -> dict[int, dict[str, int]]:
    """Daily overtime minutes per employee from schedule entries (any month)."""
    intervals: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    for row in entries:
        for day_text, item in row.get("days", {}).items():
            if item.get("kind") == "work":
                intervals[int(row["employee_id"])].append(_time_interval(day_text, item["start"], item["end"]))
    result = {}
    for employee_id, items in intervals.items():
        limit, rules = person_overtime_rule((person_rules or {}).get(employee_id), limit_minutes, break_rules)
        if limit:
            result[employee_id] = daily_overtime_minutes(sorted(items), limit, rules)
    return result


def schedule_week_totals(
    entries: list[dict[str, Any]], starts: list[date], overtime_settings: dict[str, Any] | None = None,
    person_rules: dict[int, dict[str, Any]] | None = None,
) -> dict[int, dict[str, tuple[float, float]]]:
    """Weekly FM hours (R column) and the cumulative FM of earlier weeks (T column) per employee."""
    rules = normalize_overtime_settings(
        {"weekly_limit_hours": 0.0, "week_start": 0}
        if overtime_settings is None else overtime_settings
    )
    daily = weekly_schedule_overtime(
        entries, round(rules["daily_limit_hours"] * 60), rules["break_rules"], person_rules,
    )
    result: dict[int, dict[str, tuple[float, float]]] = {}
    for employee_id, days in daily.items():
        running = 0.0
        weeks: dict[str, tuple[float, float]] = {}
        for monday in starts:
            minutes = sum(
                days.get((monday + timedelta(days=offset)).isoformat(), 0) for offset in range(7)
            )
            hours = round(minutes / 60, 2)
            weeks[monday.isoformat()] = (hours, round(running, 2))
            running += hours
        result[employee_id] = weeks
    return result


def cross_month_shifts(intervals, period_start, period_end):
    """Describe individual shifts with positive working time in both months."""
    result = []
    for start, end in sorted(set(intervals)):
        if start >= period_end or end <= period_start or start.month == end.month:
            continue
        boundary = datetime.combine(end.date(), time.min)
        if end == boundary:
            continue
        before = (boundary - start) // timedelta(minutes=1)
        after = (end - boundary) // timedelta(minutes=1)
        result.append({
            "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
            "before_minutes": before, "after_minutes": after, "total_minutes": before + after,
        })
    return result


def calculate_attendance(
    month: str,
    entries: list[dict[str, Any]],
    overrides: dict[str, dict[str, str]],
    staff: list[dict[str, Any]],
    deductions: dict[str, float] | None = None,
    overtime_settings: dict[str, Any] | None = None,
    overtime_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    dates = month_dates(month)
    first, after_last = datetime.combine(dates[0], time.min), datetime.combine(dates[-1] + timedelta(days=1), time.min)
    rules = normalize_overtime_settings(
        {"weekly_limit_hours": 0.0, "week_start": 0}
        if overtime_settings is None else overtime_settings
    )
    employee_names = {int(person["id"]): person["name"] for person in staff}
    candidates: dict[tuple[int, str], list[str]] = defaultdict(list)
    intervals: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    for row in entries:
        employee_id = int(row["employee_id"])
        if employee_id not in employee_names:
            continue
        for day_text, item in row.get("days", {}).items():
            kind = item.get("kind")
            if kind == "work":
                start, end = _time_interval(day_text, item["start"], item["end"])
                attendance_code = normalize_attendance_code(item.get("attendance_code", "X"))
                candidates[(employee_id, day_text)].append(attendance_code)
                if end.date() != start.date():
                    candidates[(employee_id, end.date().isoformat())].append("G.Ç")
                intervals[employee_id].append((start, end))
            elif kind == "custom":
                candidates[(employee_id, day_text)].append(normalize_attendance_code(item.get("custom_code", "")))
            elif kind in KIND_CODES:
                candidates[(employee_id, day_text)].append(KIND_CODES[kind])

    warnings = []
    rows = []
    global_daily_limit = int(round(rules.get("daily_limit_hours", 0) * 60))
    for person in sorted(staff, key=lambda item: (item.get("sort", 10**6), item["name"].casefold(), int(item["id"]))):
        employee_id = int(person["id"])
        employee_codes: dict[str, str] = {}
        automatic_codes: dict[str, str] = {}
        sources: dict[str, str] = {}
        person_intervals = list(intervals.get(employee_id, []))
        pattern = person.get("fixed_pattern")
        pattern_days: set[str] = set()
        if isinstance(pattern, (list, tuple)) and len(pattern) == 7:
            fixed_start = person.get("fixed_start") or "08:00"
            fixed_end = person.get("fixed_end") or "16:00"
            for day in dates:
                day_text = day.isoformat()
                active_pattern = fixed_pattern_for_date(person, day)
                code = active_pattern[day.weekday()] if isinstance(active_pattern, (list, tuple)) \
                    and len(active_pattern) == 7 else pattern[day.weekday()]
                if not code or candidates.get((employee_id, day_text)):
                    continue
                candidates[(employee_id, day_text)] = [code]
                pattern_days.add(day_text)
                manual = overrides.get(day_text, {}).get(str(employee_id))
                if code == "X" and (manual is None or manual == "X") and fixed_start != fixed_end:
                    person_intervals.append(_time_interval(day_text, fixed_start, fixed_end))
        for day in dates:
            day_text = day.isoformat()
            options = candidates.get((employee_id, day_text), [])
            unique = set(options)
            if len(unique) > 1:
                warnings.append({
                    "employee_id": employee_id,
                    "date": day_text,
                    "codes": sorted(unique),
                })
            if options:
                priority = {
                    "X": 1, "G.Ç": 2, "H": 3, "S": 4, "R": 4, "Ü": 4, "G": 4,
                    "E": 4, "K": 4, "Y": 4, "İST.": 5,
                }
                automatic = max(dict.fromkeys(options), key=lambda code: priority.get(code, 4))
            else:
                automatic = ""
            if automatic:
                automatic_codes[day_text] = automatic
            manual = overrides.get(day_text, {}).get(str(employee_id))
            if manual is not None:
                employee_codes[day_text] = normalize_attendance_code(manual)
                sources[day_text] = "manual"
            elif automatic:
                employee_codes[day_text] = automatic
                sources[day_text] = "schedule"
        merged_intervals = sorted(person_intervals)
        total_minutes = _union_minutes(merged_intervals, first, after_last)
        holiday_28_minutes = 0
        holiday_29_minutes = 0
        if dates[0].year and dates[0].month == 10:
            holiday_28_start = datetime.combine(date(dates[0].year, 10, 28), time(13, 0))
            holiday_29_start = datetime.combine(date(dates[0].year, 10, 29), time.min)
            holiday_30_start = datetime.combine(date(dates[0].year, 10, 30), time.min)
            holiday_28_minutes = _union_minutes(merged_intervals, max(first, holiday_28_start), min(after_last, holiday_29_start))
            holiday_29_minutes = _union_minutes(merged_intervals, max(first, holiday_29_start), min(after_last, holiday_30_start))
        ubgt_days: dict[str, float] = {}
        holiday_minutes = 0
        for day in dates:
            window = holiday_window(day)
            if window:
                minutes = _union_minutes(merged_intervals, *window)
                holiday_minutes += minutes
                if minutes:
                    ubgt_days[day.isoformat()] = round(minutes / 60, 2)
        daily_limit, person_breaks = person_overtime_rule(person, global_daily_limit, rules["break_rules"])
        overtime_days: dict[str, float] = {}
        overtime_details: dict[str, dict[str, float]] = {}
        overtime_detail_minutes: dict[str, dict[str, int]] = {}
        break_total = 0
        for day_text, detail in daily_overtime_details(merged_intervals, daily_limit, person_breaks).items():
            if day_text[:7] != month:
                continue
            break_total += detail["break"]
            overtime_detail_minutes[day_text] = dict(detail)
            overtime_details[day_text] = {
                key: round(value / 60, 2) for key, value in detail.items()
            }
            if daily_limit and detail["overtime"]:
                overtime_days[day_text] = round(detail["overtime"] / 60, 2)
        night_minutes = 0
        cursor_day = dates[0] - timedelta(days=1)
        while cursor_day <= dates[-1]:
            night_begin = datetime.combine(cursor_day, NIGHT_START)
            night_end = datetime.combine(cursor_day + timedelta(days=1), NIGHT_END)
            night_minutes += _union_minutes(
                merged_intervals, max(first, night_begin), min(after_last, night_end)
            ) if max(first, night_begin) < min(after_last, night_end) else 0
            cursor_day += timedelta(days=1)
        overtime_minutes = 0
        if daily_limit:
            overtime_minutes = int(round(sum(overtime_days.values()) * 60))
        elif rules["weekly_limit_hours"]:
            cursor = dates[0] - timedelta(days=(dates[0].weekday() - rules["week_start"]) % 7)
            weekly_limit = int(round(rules["weekly_limit_hours"] * 60))
            while cursor <= dates[-1]:
                week_begin = datetime.combine(cursor, time.min)
                week_end = week_begin + timedelta(days=7)
                if cursor.strftime("%Y-%m") == month:
                    worked_minutes = _union_minutes(merged_intervals, week_begin, week_end)
                    overtime_minutes += max(0, worked_minutes - weekly_limit)
                cursor += timedelta(days=7)
        computed_overtime = round(overtime_minutes / 60, 2)
        manual_overtime = (overtime_overrides or {}).get(str(employee_id))
        if manual_overtime is not None:
            overtime_minutes = int(round(float(manual_overtime) * 60))
        worked_hours_by_day = {}
        for day in dates:
            day_start = datetime.combine(day, time.min)
            day_end = datetime.combine(day + timedelta(days=1), time.min)
            minutes = _union_minutes(merged_intervals, day_start, day_end)
            if minutes:
                worked_hours_by_day[day.isoformat()] = round(minutes / 60, 2)
        rows.append({
            "employee_id": employee_id,
            "name": person["name"],
            "staff_type": "nonshift" if pattern_days or person.get("fixed_pattern") else "shift",
            "deduction_amount": (deductions or {}).get(str(employee_id), 0),
            "codes": employee_codes,
            "automatic_codes": automatic_codes,
            "sources": sources,
            "pattern_days": sorted(pattern_days),
            "colors": {
                day: item["fill_color"]
                for entry in entries if int(entry["employee_id"]) == employee_id
                for day, item in entry.get("days", {}).items()
                if item.get("fill_color") and sources.get(day) == "schedule"
                and employee_codes.get(day) == (
                    item.get("attendance_code", "X") if item["kind"] == "work"
                    else item.get("custom_code", "") if item["kind"] == "custom"
                    else KIND_CODES.get(item["kind"], "")
                )
            },
            "worked_hours": round(total_minutes / 60, 2),
            "worked_hours_by_day": worked_hours_by_day,
            "cross_month_shifts": cross_month_shifts(person_intervals, first, after_last),
            "break_hours": round(break_total / 60, 2),
            "daily_limit_hours": round(daily_limit / 60, 2),
            "overtime_details": dict(sorted(overtime_details.items())),
            "overtime_detail_minutes": dict(sorted(overtime_detail_minutes.items())),
            "normal_hours": round(max(0, total_minutes - break_total - holiday_minutes - overtime_minutes) / 60, 2),
            "holiday_hours": round(holiday_minutes / 60, 2),
            "holiday_28_hours": round(holiday_28_minutes / 60, 2),
            "holiday_29_hours": round(holiday_29_minutes / 60, 2),
            "ubgt_hours": round(sum(ubgt_days.values()), 2),
            "ubgt_days": ubgt_days,
            "overtime_hours": round(overtime_minutes / 60, 2),
            "overtime_computed_hours": computed_overtime,
            "overtime_manual": manual_overtime is not None,
            "overtime_days": overtime_days,
            "night_hours": round(night_minutes / 60, 2),
            "notes": {
                day: item["note"]
                for entry in entries if int(entry["employee_id"]) == employee_id
                for day, item in entry.get("days", {}).items() if item.get("note")
            },
        })
    movements = attendance_movements(dates, entries, rows, staff)
    return {
        "month": month,
        "dates": [item.isoformat() for item in dates],
        "rows": rows,
        "warnings": warnings,
        "movements": movements[:ATTENDANCE_MOVEMENT_ROWS],
        "movement_overflow": max(0, len(movements) - ATTENDANCE_MOVEMENT_ROWS),
        "legend": attendance_legend(rows),
    }


def fixed_pattern_for_date(person: dict[str, Any], day: date) -> list[str] | tuple[str, ...]:
    """Return the latest dated permanent change or one-week exception effective on day."""
    base = person.get("fixed_pattern")
    if not isinstance(base, (list, tuple)) or len(base) != 7:
        return []
    selected_date = date.min
    selected_priority = -1
    selected = base
    week_start = day - timedelta(days=day.weekday())
    for item in person.get("fixed_pattern_exceptions", []):
        if not isinstance(item, dict) or item.get("start_date") != week_start.isoformat():
            continue
        pattern = item.get("pattern")
        if isinstance(pattern, (list, tuple)) and len(pattern) == 7:
            selected_date, selected_priority, selected = week_start, 0, pattern
    for item in person.get("fixed_pattern_changes", []):
        if not isinstance(item, dict):
            continue
        try:
            effective = date.fromisoformat(item.get("effective_date", ""))
        except (TypeError, ValueError):
            continue
        pattern = item.get("pattern")
        if (effective <= day and (effective, 1) >= (selected_date, selected_priority)
                and isinstance(pattern, (list, tuple)) and len(pattern) == 7):
            selected_date, selected_priority, selected = effective, 1, pattern
    return selected


ATTENDANCE_MOVEMENT_ROWS = 10
# The source template labels these columns "28 EKİM ..." / "29 EKİM ..."; every month uses generic labels.
ATTENDANCE_HOUR_HEADERS = {34: "RESMİ TATİL MESAİ", 35: "FAZLA MESAİ"}
ATTENDANCE_LEGEND_SLOTS = {
    "X": (37, 5), "S": (38, 5), "H": (39, 5), "Ü": (40, 5), "R": (41, 5), "G.Ç": (42, 5),
    "G": (37, 12), "İST.": (38, 12), "E": (39, 12), "K": (40, 12), "Y": (41, 12),
}
ATTENDANCE_CUSTOM_LEGEND_SLOT = (42, 12)


def attendance_movements(
    dates: list[date],
    entries: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    staff: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive hire/exit/resignation dates for the lower attendance table.

    Hires need previous-month schedule data and exits of active staff need next-month
    data; without that context the dates cannot be told apart from a partial plan.
    """
    first_text, last_text = dates[0].isoformat(), dates[-1].isoformat()
    previous_ids: set[int] = set()
    next_ids: set[int] = set()
    for entry in entries:
        for day_text in entry.get("days", {}):
            if day_text < first_text:
                previous_ids.add(int(entry["employee_id"]))
            elif day_text > last_text:
                next_ids.add(int(entry["employee_id"]))
    active = {int(person["id"]): bool(person.get("active", True)) for person in staff}
    movements = []
    for row in rows:
        employee_id = int(row["employee_id"])
        codes = {day: code for day, code in row.get("codes", {}).items() if code}
        if not codes:
            continue
        resign_day = next((day for day in sorted(codes) if codes[day] == "İST."), None)
        worked_days = sorted(day for day, code in codes.items() if code != "İST." and (not resign_day or day < resign_day))
        hire_day = None
        if previous_ids and employee_id not in previous_ids and worked_days and worked_days[0] > first_text:
            hire_day = worked_days[0]
        exit_day = None
        if resign_day:
            exit_day = worked_days[-1] if worked_days else None
        elif worked_days and worked_days[-1] < last_text and (
            not active.get(employee_id, True) or (next_ids and employee_id not in next_ids)
        ):
            exit_day = worked_days[-1]
        if hire_day or exit_day or resign_day:
            movements.append({
                "employee_id": employee_id,
                "name": row["name"],
                "hire_date": hire_day,
                "exit_date": exit_day,
                "resignation_date": resign_day,
            })
    return movements


def attendance_legend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    colors: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        for day, code in row.get("codes", {}).items():
            if not code:
                continue
            counts[code] += 1
            color = row.get("colors", {}).get(day)
            if color:
                colors[code][color] += 1
    order = list(ATTENDANCE_LEGEND_SLOTS)
    legend = []
    for code in sorted(counts, key=lambda item: (order.index(item) if item in order else len(order), item)):
        palette = colors.get(code) or {}
        legend.append({
            "code": code,
            "count": counts[code],
            "color": max(palette, key=lambda color: (palette[color], color)) if palette else None,
            "standard": code in ATTENDANCE_LEGEND_SLOTS,
        })
    return legend


def _excel_date(value: str | None) -> datetime | None:
    return datetime.combine(date.fromisoformat(value), time.min) if value else None


def _write_attendance_lower_tables(ws, attendance: dict[str, Any]) -> None:
    for row_number in range(26, 26 + ATTENDANCE_MOVEMENT_ROWS):
        for col in (2, 3, 9, 13):
            ws.cell(row_number, col).value = None
    for index, item in enumerate(attendance.get("movements", [])[:ATTENDANCE_MOVEMENT_ROWS]):
        row_number = 26 + index
        ws.cell(row_number, 2).value = item["name"]
        ws.cell(row_number, 3).value = _excel_date(item.get("hire_date"))
        ws.cell(row_number, 9).value = _excel_date(item.get("exit_date"))
        ws.cell(row_number, 13).value = _excel_date(item.get("resignation_date"))
    legend = {item["code"]: item for item in attendance.get("legend", [])}
    custom = [item for item in attendance.get("legend", []) if not item.get("standard")]
    for code, (row_number, col) in ATTENDANCE_LEGEND_SLOTS.items():
        item = legend.get(code)
        count_cell = ws.cell(row_number, col + 1)
        count_cell.value = item["count"] if item else None
        count_cell.number_format = '0" gün"'
        if item and item.get("color"):
            _set_fill(ws.cell(row_number, col), item["color"])
    row_number, col = ATTENDANCE_CUSTOM_LEGEND_SLOT
    ws.cell(row_number, col - 5).value = "ÖZEL KODLAR" if custom else None
    ws.cell(row_number, col).value = ", ".join(item["code"] for item in custom) or None
    ws.cell(row_number, col + 1).value = sum(item["count"] for item in custom) or None
    ws.cell(row_number, col + 1).number_format = '0" gün"'
    if len(custom) == 1 and custom[0].get("color"):
        _set_fill(ws.cell(row_number, col), custom[0]["color"])


def _merge_neighbor_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_assignment: dict[tuple[int, str], dict[str, Any]] = {}
    for row in entries:
        key = (int(row["employee_id"]), row["section"])
        target = by_assignment.setdefault(key, {
            "row_id": row.get("row_id", ""),
            "employee_id": int(row["employee_id"]),
            "section": row["section"],
            "days": {},
        })
        target["days"].update(row.get("days", {}))
        if row.get("slot") is not None:
            target["slot"] = row["slot"]
    return sorted(
        by_assignment.values(),
        key=lambda row: (_section_sort_key(row["section"]), row["employee_id"], row["row_id"]),
    )


def _template_path(filename: str) -> Path:
    settings = _RESTORED_SETTINGS.get()
    override = settings[0] if settings is not None else TEMPLATE_OVERRIDE_DIR
    if override is not None:
        custom = override / filename
        if custom.is_file():
            return custom
    path = TEMPLATE_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Excel şablonu bulunamadı: {path}")
    return path


def _sheet_name(start: date, schedule_month: str) -> str:
    target_year, target_month = map(int, schedule_month.split("-"))
    if start.year == target_year and start.month == target_month:
        month_start = date(target_year, target_month, 1)
        first_monday = month_start + timedelta(days=(7 - month_start.weekday()) % 7)
        week_number = (start - first_monday).days // 7 + 1
    else:
        month_start = date(start.year, start.month, 1)
        first_monday = month_start - timedelta(days=month_start.weekday())
        month_end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        last_monday = month_end - timedelta(days=month_end.weekday())
        week_number = (last_monday - first_monday).days // 7 + 1
    month_name = MONTH_NAMES[start.month - 1]
    return f"{month_name} {week_number}"[:31]


def _apply_attendance_calendar_styles(worksheet, dates: list[date]) -> None:
    date_columns = {}
    weekday_columns = {}
    for column in range(3, 34):
        source_value = worksheet.cell(3, column).value
        if not isinstance(source_value, datetime):
            continue
        source_date = source_value.date()
        date_columns[source_date] = column
        if source_date.day not in (28, 29):
            weekday_columns.setdefault(source_date.weekday(), column)

    style_rows = range(2, 25)
    style_snapshots = {
        column: {
            row: copy(worksheet.cell(row, column)._style)
            for row in style_rows
        }
        for column in range(3, 34)
    }
    for column in range(3, 34):
        day_index = column - 3
        day = dates[day_index] if day_index < len(dates) else None
        source_column = (
            date_columns.get(day)
            if day is not None
            else None
        )
        if source_column is None:
            weekday = day.weekday() if day is not None else 0
            source_column = weekday_columns[weekday]
        for row in style_rows:
            cell = worksheet.cell(row, column)
            cell._style = copy(style_snapshots[source_column][row])
            if day is None and row == 3:
                cell.fill = PatternFill(fill_type=None)
        if day is None:
            for row in (2, 3):
                worksheet.cell(row, column).fill = PatternFill(fill_type=None)
            continue
        fill_color = ATTENDANCE_CATEGORY_COLORS[attendance_day_category(day)]
        for row in (2, 3):
            _set_fill(worksheet.cell(row, column), fill_color)


def _replace_black_fills(workbook) -> None:
    theme = _theme_colors(workbook)
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                if cell.fill.fill_type != "solid" or _css_color(cell.fill.fgColor, theme) != "#000000":
                    continue
                fill = copy(cell.fill)
                fill.fgColor = "FFFFFFFF"
                cell.fill = fill
                if _css_color(cell.font.color, theme) == "#FFFFFF":
                    font = copy(cell.font)
                    font.color = "FF000000"
                    cell.font = font


def _write_schedule_day(ws, row: int, columns: tuple[int, int], item: dict[str, Any]) -> None:
    first_col, second_col = columns
    if item["kind"] == "work":
        ws.cell(row, first_col).value = time.fromisoformat(item["start"])
        ws.cell(row, first_col).number_format = "hh:mm"
        if item["end"] == "00:00":
            # Gece yarısı bitişi Excel'de 24:00 olarak görünsün (1 gün = 24 saat).
            ws.cell(row, second_col).value = 1
            ws.cell(row, second_col).number_format = "[hh]:mm"
        else:
            ws.cell(row, second_col).value = time.fromisoformat(item["end"])
            ws.cell(row, second_col).number_format = "hh:mm"
    else:
        label = {
            "weekly_off": "HAFTALIK İZİNLİ", "overnight_exit": "GECEDEN ÇIKIŞ",
            "annual_leave": "SENELİK İZİNLİ", "sick_leave": "RAPORLU",
            "unpaid_leave": "ÜCRETSİZ İZİNLİ", "marriage_leave": "EVLİLİK İZNİ",
            "quarantine": "KARANTİNA", "half_day": "YARIM GÜN",
            "resignation": "İSTİFA", "absent": "İŞE GELMEDİ",
            "custom": item.get("custom_label") or item.get("custom_code"),
        }[item["kind"]]
        ws.merge_cells(start_row=row, start_column=first_col, end_row=row, end_column=second_col)
        ws.cell(row, first_col).value = label
    if item.get("fill_color"):
        for col in columns:
            _set_fill(ws.cell(row, col), item["fill_color"])
    if item.get("note"):
        from openpyxl.comments import Comment
        comment = Comment(item["note"], "Vardiya Puantaj")
        comment.width, comment.height = 220, 70
        ws.cell(row, first_col).comment = comment


def _layout_position(layout_rows: list[int | None], source_row: int) -> int:
    """Sheet row of a template row, or of the nearest surviving row above it when it was deleted."""
    for row in range(source_row, 0, -1):
        if row in layout_rows:
            return layout_rows.index(row) + 1
    return 1


def _section_extra_ordinals(item: dict[str, Any], allocated: dict[int, Any]) -> list[int]:
    """Rows needed below the template rows: the configured rows plus filled optional rows."""
    template_count = len(SECTION_ROWS.get(item["name"], ()))
    return sorted(set(range(template_count + 1, item["rows"] + 1))
                  | {ordinal for ordinal in allocated if ordinal > template_count})


def _insert_section_rows(
    ws,
    monday: date,
    names: dict[int, str],
    section_entries: dict[str, dict[int, dict[str, Any]]],
    layout_rows: list[int | None],
    columns: dict[int, int],
    write_totals=None,
) -> None:
    """Grow sections inside their own block and append custom sections as TEMİZLİK-style blocks.

    Insertions run bottom-up so every position computed from the layout stays valid.
    """
    def position(source_row: int) -> int:
        return _layout_position(layout_rows, source_row)

    def prepare_row(target: int, style_row: int, height: float | None, white: bool = True) -> None:
        ws.row_dimensions[target].height = height
        for col, mapped in columns.items():
            cell = ws.cell(target, mapped)
            cell._style = copy(ws.cell(style_row, mapped)._style)
            cell.value = None
            if white:
                _set_fill(cell, "#ffffff")
        if {18, 20, 21} <= set(columns):
            ws.cell(target, columns[21]).value = (
                f"=SUM({get_column_letter(columns[18])}{target}:{get_column_letter(columns[20])}{target})"
            )

    def write_entry(target: int, ordinal: int, entry: dict[str, Any] | None) -> None:
        if not entry:
            return
        if 2 in columns:
            ws.cell(target, columns[2]).value = ordinal
        if 3 in columns:
            ws.cell(target, columns[3]).value = names.get(int(entry["employee_id"]), "")
        if write_totals:
            write_totals(target, int(entry["employee_id"]), columns)
        for day_offset in range(7):
            item = entry.get("days", {}).get((monday + timedelta(days=day_offset)).isoformat())
            first_col = 4 + day_offset * 2
            if item and first_col in columns and first_col + 1 in columns:
                _write_schedule_day(ws, target, (columns[first_col], columns[first_col + 1]), item)

    plan = [(item, _section_extra_ordinals(item, section_entries.get(item["name"], {})))
            for item in _active_schedule_sections()]
    anchor = position(SECTION_ROWS["ÖSSE"][-1]) + 1
    header_rows = [position(row) for row in range(13, 17)]
    spacer_row, data_style_row = position(18), position(17)
    for item, ordinals in reversed(plan):
        if item["name"] in SECTION_ROWS:
            continue
        for offset in range(CUSTOM_SECTION_HEADER_ROWS + len(ordinals)):
            _insert_worksheet_axis(ws, "row", anchor + offset)
        prepare_row(anchor, spacer_row, ws.row_dimensions[spacer_row].height, white=False)
        ws.cell(anchor, columns.get(21, 21)).value = None
        for index, source in enumerate(header_rows):
            target = anchor + 1 + index
            ws.row_dimensions[target].height = ws.row_dimensions[source].height
            for mapped in columns.values():
                cell, origin = ws.cell(target, mapped), ws.cell(source, mapped)
                cell._style = copy(origin._style)
                cell.value = origin.value
        shift = anchor + 1 - header_rows[0]
        for merged in list(ws.merged_cells.ranges):
            min_col, min_row, max_col, max_row = merged.bounds
            if header_rows[0] <= min_row and max_row <= header_rows[-1]:
                ws.merge_cells(start_row=min_row + shift, start_column=min_col,
                               end_row=max_row + shift, end_column=max_col)
        if 3 in columns:
            ws.cell(anchor + 1, columns[3]).value = item["name"]
        allocated = section_entries.get(item["name"], {})
        for index, ordinal in enumerate(ordinals):
            target = anchor + CUSTOM_SECTION_HEADER_ROWS + index
            prepare_row(target, data_style_row, ws.row_dimensions[data_style_row].height)
            write_entry(target, ordinal, allocated.get(ordinal))
    for item, ordinals in reversed(plan):
        template_rows = SECTION_ROWS.get(item["name"])
        if not template_rows or not ordinals:
            continue
        last = position(template_rows[-1])
        allocated = section_entries.get(item["name"], {})
        for index, ordinal in enumerate(ordinals):
            target = last + 1 + index
            _insert_worksheet_axis(ws, "row", target)
            prepare_row(target, last, ws.row_dimensions[last].height)
            write_entry(target, ordinal, allocated.get(ordinal))


def export_schedule_xlsx(
    path: str | Path,
    month: str,
    entries: list[dict[str, Any]],
    staff: list[dict[str, Any]],
    active_week_index: int = 0,
    settings: dict[str, Any] | None = None,
    selected_entries: list[dict[str, Any]] | None = None,
    overtime_settings: dict[str, Any] | None = None,
    leave_remaining: dict[int, float] | None = None,
    person_rules: dict[int, dict[str, Any]] | None = None,
) -> None:
    starts = week_starts(month)
    if (
        not isinstance(active_week_index, int)
        or isinstance(active_week_index, bool)
        or not 0 <= active_week_index < len(starts)
    ):
        raise ValueError("Seçilen hafta bulunamadı.")
    workbook = load_workbook(_template_path("weekly_schedule.xlsx"))
    sheets = workbook.worksheets
    while len(sheets) < len(starts):
        sheets.append(workbook.copy_worksheet(sheets[1 if len(sheets) > 1 else 0]))
    for sheet in list(workbook.worksheets[len(starts):]):
        workbook.remove(sheet)
    names = {int(person["id"]): person["name"] for person in staff}
    rows_by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    merged_entries = _merge_neighbor_entries(entries)
    if selected_entries is not None:
        merged_by_person = {(row["employee_id"], row["section"]): row for row in merged_entries}
        merged_entries = [
            {**merged_by_person[(row["employee_id"], row["section"])], "slot": row.get("slot")}
            for row in selected_entries
        ]
    for row in merged_entries:
        rows_by_section[row["section"]].append(row)
    weekly_totals = schedule_week_totals(merged_entries, starts, overtime_settings, person_rules)
    leave_remaining = leave_remaining or {}
    for sheet_index, (ws, monday) in enumerate(zip(workbook.worksheets, starts)):
        totals_written: set[int] = set()

        def write_totals(target: int, employee_id: int, cols: dict[int, int], ws=ws, monday=monday, done=totals_written) -> None:
            if employee_id in done:
                return
            done.add(employee_id)
            week, previous = weekly_totals.get(employee_id, {}).get(monday.isoformat(), (0.0, 0.0))
            if 18 in cols:
                ws.cell(target, cols[18]).value = week
            if 20 in cols:
                ws.cell(target, cols[20]).value = previous
            if 22 in cols and employee_id in leave_remaining:
                ws.cell(target, cols[22]).value = leave_remaining[employee_id]

        _clear_template_artifacts(ws, "schedule")
        _apply_workbook_settings(ws, settings or {}, "schedule", month, sheet_index)
        ws.title = _sheet_name(monday, month)
        ws.sheet_view.showGridLines = False
        data_rows = [*range(6, 12), 17, *range(23, 34)]
        for merged in list(ws.merged_cells.ranges):
            min_col, min_row, max_col, max_row = merged.bounds
            if min_row == max_row and min_row in data_rows and 4 <= min_col <= 16 and max_col == min_col + 1:
                ws.unmerge_cells(str(merged))
        for row_number in data_rows:
            for col in range(2, 23):
                cell = ws.cell(row_number, col)
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    continue
                cell.value = None
        for day_offset in range(7):
            day = monday + timedelta(days=day_offset)
            ws.cell(2, 4 + day_offset * 2).value = datetime.combine(day, time.min)
        section_entries: dict[str, dict[int, dict[str, Any]]] = {}
        for section in section_names():
            row_numbers = SECTION_ROWS.get(section, ())
            section_rows = rows_by_section[section]
            limit = section_capacity(section)
            if len(section_rows) > limit:
                raise ValueError(
                    f"{section} bölümünde {limit} kişilik Excel alanı var; "
                    "Araçlar › Bölümler ve satırlar penceresinden satır sayısını artırın."
                )
            allocated = {}
            for entry in section_rows:
                slot = entry.get("slot")
                if isinstance(slot, int) and 1 <= slot <= limit and slot not in allocated:
                    allocated[slot] = entry
            for entry in section_rows:
                if any(item is entry for item in allocated.values()):
                    continue
                slot = next(slot for slot in range(1, limit + 1) if slot not in allocated)
                allocated[slot] = entry
            section_entries[section] = allocated
            for ordinal, entry in sorted(allocated.items()):
                if ordinal > len(row_numbers):
                    continue
                target_row = row_numbers[ordinal - 1]
                ws.cell(target_row, 2).value = ordinal
                ws.cell(target_row, 3).value = names.get(int(entry["employee_id"]), "")
                write_totals(target_row, int(entry["employee_id"]), {18: 18, 20: 20, 22: 22})
                for day_offset in range(7):
                    day = monday + timedelta(days=day_offset)
                    item = entry.get("days", {}).get(day.isoformat())
                    if not item:
                        continue
                    first_col = 4 + day_offset * 2
                    _write_schedule_day(ws, target_row, (first_col, first_col + 1), item)
        sheet_settings = sheet_settings_for(settings, "schedule", month, sheet_index)
        template = workbook_template("schedule", 1 if sheet_index >= 5 else sheet_index)
        layout_rows, layout_cols, _ = _inserted_layout(
            template, sheet_settings.get("extra_rows", 0), sheet_settings.get("extra_cols", 0),
            sheet_settings.get("insert_rows", []), sheet_settings.get("insert_cols", []),
            sheet_settings.get("delete_rows", []), sheet_settings.get("delete_cols", []),
        )
        columns = {col: layout_cols.index(col) + 1 for col in range(1, 23) if col in layout_cols}
        _insert_worksheet_cells(
            ws, settings or {}, "schedule", month, sheet_index,
            before_custom=lambda: _insert_section_rows(
                ws, monday, names, section_entries, layout_rows, columns, write_totals,
            ),
        )
        _apply_cell_colors(ws, settings or {}, "schedule", month, sheet_index)
        start_col = "A" if 1 in sheet_settings.get("insert_cols", []) else "B"
        ws.print_area = f"{start_col}1:{get_column_letter(ws.max_column)}{ws.max_row}"
        _apply_page_setup(ws, sheet_settings)
    workbook.active = active_week_index
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.save(path)


def export_attendance_xlsx(path: str | Path, month: str, attendance: dict[str, Any], settings: dict[str, Any] | None = None) -> None:
    dates = month_dates(month)
    workbook = load_workbook(_template_path("attendance.xlsx"))
    ws = workbook.active
    _drop_normal_hours_column(ws)
    _apply_workbook_settings(ws, settings or {}, "attendance", month, 0)
    _apply_attendance_calendar_styles(ws, dates)
    ws.title = f"PUANTAJ {MONTH_NAMES[dates[0].month - 1]} {dates[0].year}"[:31]
    ws.sheet_view.showGridLines = False
    ws["A1"] = (f"İSTASYON {MONTH_NAMES[dates[0].month - 1]} {dates[0].year} PUANTAJ"
                " · R: Resmî tatil · D: Dinî bayram")
    for day_index in range(31):
        day_col = 3 + day_index
        if day_index < len(dates):
            ws.cell(2, day_col).value = day_index + 1
            ws.cell(3, day_col).value = datetime.combine(dates[day_index], time.min)
            category = attendance_day_category(dates[day_index])
            if category in ("official", "religious"):
                label = "R" if category == "official" else "D"
                ws.cell(2, day_col).number_format = f'0"{label}"'
        else:
            ws.cell(2, day_col).value = None
            ws.cell(3, day_col).value = None
    table_rows = range(4, 25)
    for row_number in table_rows:
        for col in range(1, 37):
            ws.cell(row_number, col).value = None
    rows = attendance.get("rows", [])
    if len(rows) > MAX_MONTH_PERSONNEL:
        raise ValueError(f"Puantajda en fazla {MAX_MONTH_PERSONNEL} personel satırı olabilir.")
    # The template borders personnel rows 4-22 only; rows 23-24 are spacers, so later people copy row 22.
    base_styles = {col: copy(ws.cell(22, col)._style) for col in range(1, 37)}
    base_height = ws.row_dimensions[22].height

    def write_person(row_number: int, index: int, person: dict[str, Any], cols: dict[int, int]) -> None:
        if index >= 19:
            ws.row_dimensions[row_number].height = base_height
            for col, mapped in cols.items():
                ws.cell(row_number, mapped)._style = copy(base_styles[col])
        ws.cell(row_number, cols[1]).value = index + 1
        ws.cell(row_number, cols[2]).value = person["name"]
        for day_index, day in enumerate(dates):
            if 3 + day_index not in cols:
                continue
            target = ws.cell(row_number, cols[3 + day_index])
            code = person.get("codes", {}).get(day.isoformat(), "")
            if code:
                target.value = code
            color = person.get("colors", {}).get(day.isoformat())
            if color:
                _set_fill(target, color)
        for col, value in ((34, person.get("holiday_hours", 0) or None),
                           (35, person.get("overtime_hours", 0) or None),
                           (36, person.get("deduction_amount") or None)):
            if col in cols:
                ws.cell(row_number, cols[col]).value = value

    for col, label in ATTENDANCE_HOUR_HEADERS.items():
        header = ws.cell(3, col)
        if isinstance(header.value, str) and "EKİM" in header.value.upper():
            header.value = label
    identity = {col: col for col in range(1, 37)}
    for index, person in enumerate(rows[:21]):
        write_person(4 + index, index, person, identity)
    _write_attendance_lower_tables(ws, attendance)
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    sheet = sheet_settings_for(settings, "attendance", month, 0)
    layout_rows, layout_cols, _ = _inserted_layout(
        workbook_template("attendance", 0), sheet.get("extra_rows", 0), sheet.get("extra_cols", 0),
        sheet.get("insert_rows", []), sheet.get("insert_cols", []),
        sheet.get("delete_rows", []), sheet.get("delete_cols", []),
    )

    def insert_extra_people() -> None:
        # Extra personnel rows continue the table directly below row 24, pushing the lower tables down.
        cols = {col: layout_cols.index(col) + 1 for col in range(1, 37) if col in layout_cols}
        last = _layout_position(layout_rows, 24)
        for offset, person in enumerate(rows[21:], start=1):
            target = last + offset
            _insert_worksheet_axis(ws, "row", target)
            write_person(target, 20 + offset, person, cols)

    _insert_worksheet_cells(ws, settings or {}, "attendance", month, 0,
                            before_custom=insert_extra_people if len(rows) > 21 else None)
    _apply_cell_colors(ws, settings or {}, "attendance", month, 0)
    sheet_settings = sheet_settings_for(settings, "attendance", month, 0)
    if any(sheet_settings.get(key) for key in ("extra_rows", "extra_cols", "insert_rows", "insert_cols")):
        ws.print_area = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
    _apply_page_setup(ws, sheet_settings)
    setup = sheet_settings.get("page_setup") or {}
    if rows and (setup.get("code_dropdown") or setup.get("highlight_codes")):
        first_row = _layout_position(layout_rows, 4)
        if len(rows) > 21:
            last_row = _layout_position(layout_rows, 24) + len(rows) - 21
        else:
            last_row = _layout_position(layout_rows, 3 + len(rows))
        ranges = []
        for day_index in range(len(dates)):
            if 3 + day_index in layout_cols:
                letter = get_column_letter(layout_cols.index(3 + day_index) + 1)
                ranges.append(f"{letter}{first_row}:{letter}{last_row}")
        codes = {code for row in rows for code in row.get("codes", {}).values() if isinstance(code, str)}
        _apply_attendance_helpers(ws, setup, ranges, codes)
    workbook.save(path)
