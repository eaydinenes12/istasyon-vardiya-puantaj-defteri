"""Renders a generated workbook's active sheet as static HTML for the in-app output preview."""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries

MAX_ROWS = 160
MAX_COLS = 60


def _color(value: Any) -> str:
    rgb = getattr(value, "rgb", None)
    if isinstance(rgb, str) and len(rgb) == 8 and rgb not in ("00000000",):
        return "#" + rgb[2:]
    return ""


def _border(side: Any) -> str:
    style = getattr(side, "style", None)
    if not style:
        return ""
    width = "2px" if style in ("medium", "thick", "double") else "1px"
    return f"{width} solid {_color(side.color) or '#000'}"


def _text(value: Any, number_format: str) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        if "dddd" in number_format or "aaaa" in number_format:
            days = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
            return days[value.weekday()]
        return value.strftime("%d.%m.%Y" if "yy" in number_format.lower() else "%d.%m")
    if isinstance(value, str) and value.startswith("="):
        return "ƒ"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return str(value)


def workbook_html(content: bytes) -> str:
    ws = load_workbook(BytesIO(content)).active
    max_row = min(ws.max_row, MAX_ROWS)
    max_col = min(ws.max_column, MAX_COLS)
    if ws.print_area:
        area = ws.print_area if isinstance(ws.print_area, str) else ws.print_area[0]
        area = area.split("!")[-1].replace("$", "")
        try:
            _, _, last_col, last_row = range_boundaries(area)
            max_row, max_col = min(max_row, last_row), min(max_col, last_col)
        except ValueError:
            pass
    spans: dict[tuple[int, int], tuple[int, int]] = {}
    hidden: set[tuple[int, int]] = set()
    for merged in ws.merged_cells.ranges:
        if merged.min_row > max_row or merged.min_col > max_col:
            continue
        spans[(merged.min_row, merged.min_col)] = (
            min(merged.max_row, max_row) - merged.min_row + 1,
            min(merged.max_col, max_col) - merged.min_col + 1,
        )
        for row in range(merged.min_row, merged.max_row + 1):
            for col in range(merged.min_col, merged.max_col + 1):
                if (row, col) != (merged.min_row, merged.min_col):
                    hidden.add((row, col))
    widths = []
    for col in range(1, max_col + 1):
        dimension = ws.column_dimensions[get_column_letter(col)]
        width = 0 if dimension.hidden else (dimension.width or 8.43)
        widths.append(f'<col style="width:{round(width * 7 + 5)}px">')
    parts = [f'<table class="xl-preview" data-sheet="{escape(ws.title)}"><colgroup>{"".join(widths)}</colgroup>']
    for row in range(1, max_row + 1):
        dimension = ws.row_dimensions[row]
        if dimension.hidden:
            continue
        height = round((dimension.height or 15) * 4 / 3)
        parts.append(f'<tr style="height:{height}px">')
        for col in range(1, max_col + 1):
            if (row, col) in hidden:
                continue
            cell = ws.cell(row, col)
            styles = []
            fill = cell.fill
            if fill is not None and fill.fill_type == "solid":
                color = _color(fill.fgColor)
                if color:
                    styles.append(f"background:{color}")
            font = cell.font
            if font is not None:
                if font.b:
                    styles.append("font-weight:700")
                if font.i:
                    styles.append("font-style:italic")
                if font.sz:
                    styles.append(f"font-size:{round(float(font.sz) * 4 / 3)}px")
                color = _color(font.color) if font.color is not None else ""
                if color:
                    styles.append(f"color:{color}")
            alignment = cell.alignment
            if alignment is not None:
                if alignment.horizontal in ("center", "centerContinuous", "right", "left", "justify"):
                    styles.append(f"text-align:{'center' if alignment.horizontal.startswith('center') else alignment.horizontal}")
                if alignment.vertical in ("top", "center", "bottom"):
                    styles.append(f"vertical-align:{'middle' if alignment.vertical == 'center' else alignment.vertical}")
                if alignment.text_rotation in (90, 180):
                    styles.append("writing-mode:vertical-rl;transform:rotate(180deg)")
                if alignment.wrap_text:
                    styles.append("white-space:normal")
            border = cell.border
            if border is not None:
                for side_name in ("top", "right", "bottom", "left"):
                    value = _border(getattr(border, side_name))
                    if value:
                        styles.append(f"border-{side_name}:{value}")
            attrs = ""
            if (row, col) in spans:
                rowspan, colspan = spans[(row, col)]
                if rowspan > 1:
                    attrs += f' rowspan="{rowspan}"'
                if colspan > 1:
                    attrs += f' colspan="{colspan}"'
            text = _text(cell.value, cell.number_format or "")
            title = f' title="{escape(str(cell.value))}"' if text == "ƒ" else ""
            parts.append(f'<td{attrs}{title} style="{";".join(styles)}">{escape(text)}</td>')
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)
