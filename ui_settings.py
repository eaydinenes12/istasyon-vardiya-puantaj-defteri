"""Tablo kullanımı tercihleri: vardiya türleri, kısayollar, ipuçları, şablon tercihleri, görünüm."""
from __future__ import annotations

import json
import re
from typing import Any

SETTING_NAME = "ui_settings"
TIME = re.compile(r"(?:[01]\d|2[0-4]):[0-5]\d")
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
COMBO = re.compile(r"(?:(?:Ctrl|Alt|Shift)\+){0,3}(?:[A-Za-z0-9]|F(?:[1-9]|1[0-2])|Enter|Delete|Home|End|PageUp|PageDown|[.,/;'\-=\[\]])")

DEFAULT_SHIFT_TYPES: list[dict[str, Any]] = [
    {"id": "sabah", "label": "Sabah", "color": "#e2efda", "from": "05:00", "to": "10:59"},
    {"id": "ara", "label": "Ara vardiya", "color": "#fce4d6", "from": "11:00", "to": "13:59"},
    {"id": "aksam", "label": "Akşam", "color": "#ddebf7", "from": "14:00", "to": "19:59"},
    {"id": "gece", "label": "Gece", "color": "#e4dfec", "from": "20:00", "to": "04:59"},
    {"id": "yarim", "label": "Yarım gün", "color": "#fff2cc", "from": "", "to": ""},
]

MENU_VIEWS = ("view:schedule", "view:attendance", "view:archive", "view:templates")
RING_SIZES = (160, 190, 220, 260, 300, 340)

DEFAULTS: dict[str, Any] = {
    "shift_types": DEFAULT_SHIFT_TYPES,
    "half_day_max_hours": 5.0,
    "auto_type_color": True,
    "shortcuts": {},
    "tips": {"enabled": True, "dismissed": []},
    "templates": {"favorites": [], "default": "", "usage": {}},
    "density": "comfortable",
    "hide_empty_rows": True,
    "personnel_views": [],
    "export": {"filename_template": "", "last_folder": ""},
    "easy_menu": {
        "order": list(MENU_VIEWS), "hidden": [], "ring_enabled": True, "ring_size": RING_SIZES[1],
        "ring_items": None,
    },
    "easy_ribbons": {"schedule": True, "attendance": True},
    "easy_workspaces": [],
    "easy_active_workspace": "",
    "easy_guide": {"step": 0, "completed": [], "dismissed": False},
    "easy_task": {"flow": "new-month", "month": "", "checked": []},
    "document_presets": [],
}

DOCUMENT_PRESET_CODES = ("", "G", "S", "Ü", "R")


def _text(value: Any, limit: int, field: str) -> str:
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ValueError(f"{field} en fazla {limit} karakter olmalıdır.")
    return " ".join(value.split())


def _shift_types(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("En fazla 20 vardiya türü tanımlanabilir.")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Vardiya türü geçersiz.")
        type_id = _text(item.get("id", ""), 30, "Vardiya türü kimliği")
        label = _text(item.get("label", ""), 30, "Vardiya türü adı")
        if not type_id or not label or type_id in seen:
            raise ValueError("Vardiya türü adı boş veya yineleniyor.")
        color = item.get("color", "#ffffff")
        if not isinstance(color, str) or not COLOR.fullmatch(color):
            raise ValueError("Vardiya türü rengi #RRGGBB olmalıdır.")
        start, end = item.get("from", ""), item.get("to", "")
        for part in (start, end):
            if part and (not isinstance(part, str) or not TIME.fullmatch(part)):
                raise ValueError("Vardiya türü saat aralığı SS:DD olmalıdır.")
        if bool(start) != bool(end):
            raise ValueError("Vardiya türü başlangıç aralığının iki ucu birlikte girilmelidir.")
        seen.add(type_id)
        result.append({"id": type_id, "label": label, "color": color.lower(), "from": start, "to": end})
    return result


def _string_list(value: Any, limit: int, size: int, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{field} listesi geçersiz.")
    return [_text(item, size, field) for item in value if isinstance(item, str) and item.strip()]


def _menu_item_id(item: Any) -> bool:
    if not isinstance(item, str) or not item or len(item) > 140 or re.search(r"[\x00-\x1f\x7f]", item):
        return False
    name = item.partition(":")[2]
    return item in MENU_VIEWS or (
        item.startswith(("tool:", "group:")) and bool(name) and name == " ".join(name.split())
    )


def _easy_menu(value: Any) -> dict[str, Any]:
    required = {"order", "hidden"}
    optional = {"ring_enabled", "ring_size", "ring_items"}
    if not isinstance(value, dict) or not required <= set(value) <= required | optional:
        raise ValueError("Menü düzeni, sıralama ve gizlenen sekmeleri içermelidir.")
    order, hidden = value["order"], value["hidden"]
    ring_enabled = value.get("ring_enabled", True)
    ring_size = value.get("ring_size", RING_SIZES[1])
    ring_items = value.get("ring_items")
    if not isinstance(ring_enabled, bool) or type(ring_size) is not int or ring_size not in RING_SIZES:
        raise ValueError("Amblem kısayol halkası ayarları geçersiz.")
    ring_ids = [item for item in ring_items if item is not None] if isinstance(ring_items, list) else []
    if ring_items is not None and (
        not isinstance(ring_items, list) or len(ring_items) > 12
        or any(item is not None and not _menu_item_id(item) for item in ring_items)
        or len(set(ring_ids)) != len(ring_ids)
    ):
        raise ValueError("Kısayol halkasında en fazla 12 farklı geçerli öğe bulunabilir.")
    if not isinstance(order, list) or not len(MENU_VIEWS) <= len(order) <= 100:
        raise ValueError("Menü sıralaması 4-100 öğe içermelidir.")
    for item in order:
        if not isinstance(item, str) or not item or len(item) > 140 or re.search(r"[\x00-\x1f\x7f]", item):
            raise ValueError("Menü öğesi geçersiz.")
        if not _menu_item_id(item):
            raise ValueError("Menü öğesi kimliği geçersiz.")
    if len(set(order)) != len(order) or not set(MENU_VIEWS).issubset(order):
        raise ValueError("Ana menü sekmeleri eksik veya menü öğeleri yineleniyor.")
    if not isinstance(hidden, list) or any(
        not _menu_item_id(item) or item.startswith("tool:") for item in hidden
    ) or len(set(hidden)) != len(hidden):
        raise ValueError("Gizlenen ana sayfa veya başlık sekmesi geçersiz ya da yineleniyor.")
    return {
        "order": list(order), "hidden": list(hidden),
        "ring_enabled": ring_enabled, "ring_size": ring_size,
        "ring_items": None if ring_items is None else list(ring_items),
    }


def _easy_ribbons(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict) or set(value) != {"schedule", "attendance"} or any(
        not isinstance(visible, bool) for visible in value.values()
    ):
        raise ValueError("Vardiya ve Puantaj araç şeritleri için açık/kapalı seçimi gereklidir.")
    return dict(value)


def _easy_workspaces(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("En fazla 20 çalışma düzeni kaydedilebilir.")
    result, names = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "menu", "ribbons", "zoom"}:
            raise ValueError("Çalışma düzeni alanları geçersiz.")
        name = _text(item["name"], 60, "Çalışma düzeni adı")
        if not name or name.casefold() in names:
            raise ValueError("Çalışma düzeni adı boş veya yineleniyor.")
        zoom = item["zoom"]
        if not isinstance(zoom, dict) or set(zoom) != {"schedule", "attendance"} or any(
            isinstance(number, bool) or not isinstance(number, (int, float))
            or not 25 <= number <= 150 for number in zoom.values()
        ):
            raise ValueError("Çalışma düzeni yakınlaştırması %25-%150 arasında olmalıdır.")
        names.add(name.casefold())
        result.append({
            "name": name, "menu": _easy_menu(item["menu"]),
            "ribbons": _easy_ribbons(item["ribbons"]), "zoom": dict(zoom),
        })
    return result


def normalize(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Görünüm ayarları geçersiz.")
    unknown = set(payload) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Bilinmeyen ayar: {', '.join(sorted(unknown))}")
    result = merged({})
    if "easy_task" in payload:
        task = payload["easy_task"]
        if (not isinstance(task, dict) or set(task) != {"flow", "month", "checked"}
                or task["flow"] not in ("new-month", "leave-document", "pdks", "emergency", "closing")
                or not isinstance(task["month"], str)
                or (task["month"] and not re.fullmatch(r"[1-9]\d{3}-(?:0[1-9]|1[0-2])", task["month"]))
                or not isinstance(task["checked"], list) or len(task["checked"]) > 5
                or any(type(index) is not int or not 0 <= index < 5 for index in task["checked"])
                or len(set(task["checked"])) != len(task["checked"])):
            raise ValueError("Görev ilerlemesi geçersiz.")
        result["easy_task"] = dict(task, checked=list(task["checked"]))
    if "easy_menu" in payload:
        result["easy_menu"] = _easy_menu(payload["easy_menu"])
    if "easy_ribbons" in payload:
        result["easy_ribbons"] = _easy_ribbons(payload["easy_ribbons"])
    if "easy_workspaces" in payload:
        result["easy_workspaces"] = _easy_workspaces(payload["easy_workspaces"])
    if "easy_active_workspace" in payload:
        result["easy_active_workspace"] = _text(payload["easy_active_workspace"], 60, "Etkin çalışma düzeni")
        if result["easy_active_workspace"] and not any(
            item["name"] == result["easy_active_workspace"] for item in result["easy_workspaces"]
        ):
            raise ValueError("Etkin çalışma düzeni kayıtlı listede bulunamadı.")
    if "easy_guide" in payload:
        guide = payload["easy_guide"]
        if (not isinstance(guide, dict) or set(guide) != {"step", "completed", "dismissed"}
                or type(guide["step"]) is not int or not 0 <= guide["step"] < 8
                or type(guide["dismissed"]) is not bool
                or not isinstance(guide["completed"], list)
                or len(guide["completed"]) > 8
                or any(type(step) is not int or not 0 <= step < 8 for step in guide["completed"])
                or len(set(guide["completed"])) != len(guide["completed"])):
            raise ValueError("Başlangıç rehberi adımları geçersiz.")
        result["easy_guide"] = {
            "step": guide["step"], "completed": list(guide["completed"]), "dismissed": guide["dismissed"],
        }
    if "shift_types" in payload:
        result["shift_types"] = _shift_types(payload["shift_types"])
    if "half_day_max_hours" in payload:
        hours = payload["half_day_max_hours"]
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 <= hours <= 12:
            raise ValueError("Yarım gün süresi 0-12 saat olmalıdır.")
        result["half_day_max_hours"] = float(hours)
    for flag in ("auto_type_color", "hide_empty_rows"):
        if flag in payload:
            if not isinstance(payload[flag], bool):
                raise ValueError("Açık/kapalı ayarı geçersiz.")
            result[flag] = payload[flag]
    if "shortcuts" in payload:
        shortcuts = payload["shortcuts"]
        if not isinstance(shortcuts, dict) or len(shortcuts) > 60:
            raise ValueError("Kısayol listesi geçersiz.")
        clean: dict[str, str] = {}
        for action, combo in shortcuts.items():
            if not isinstance(action, str) or not re.fullmatch(r"[a-z0-9-]{1,40}", action):
                raise ValueError("Kısayol işlemi geçersiz.")
            if combo == "":
                clean[action] = ""
                continue
            if not isinstance(combo, str) or not COMBO.fullmatch(combo):
                raise ValueError(f"Kısayol tuşu geçersiz: {combo}")
            if combo in clean.values():
                raise ValueError(f"{combo} kısayolu iki işleme atanamaz.")
            clean[action] = combo
        result["shortcuts"] = clean
    if "tips" in payload:
        tips = payload["tips"]
        if not isinstance(tips, dict) or not isinstance(tips.get("enabled", True), bool):
            raise ValueError("İpucu ayarı geçersiz.")
        result["tips"] = {
            "enabled": tips.get("enabled", True),
            "dismissed": _string_list(tips.get("dismissed", []), 200, 60, "İpucu"),
        }
    if "templates" in payload:
        prefs = payload["templates"]
        if not isinstance(prefs, dict):
            raise ValueError("Şablon tercihleri geçersiz.")
        usage = prefs.get("usage", {})
        if not isinstance(usage, dict) or len(usage) > 200:
            raise ValueError("Şablon kullanım sayıları geçersiz.")
        result["templates"] = {
            "favorites": _string_list(prefs.get("favorites", []), 100, 80, "Favori şablon"),
            "default": _text(prefs.get("default", ""), 80, "Varsayılan şablon"),
            "usage": {
                _text(name, 80, "Şablon"): int(count)
                for name, count in usage.items()
                if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 100000
            },
        }
    if "density" in payload:
        if payload["density"] not in ("compact", "comfortable"):
            raise ValueError("Yoğunluk sıkı veya rahat olmalıdır.")
        result["density"] = payload["density"]
    if "personnel_views" in payload:
        views = payload["personnel_views"]
        if not isinstance(views, list) or len(views) > 30:
            raise ValueError("En fazla 30 kayıtlı personel listesi olabilir.")
        clean_views = []
        for view in views:
            if not isinstance(view, dict) or not isinstance(view.get("filters", {}), dict):
                raise ValueError("Kayıtlı personel listesi geçersiz.")
            name = _text(view.get("name", ""), 40, "Liste adı")
            if not name:
                raise ValueError("Kayıtlı liste adı boş olamaz.")
            filters = view.get("filters", {})
            if len(json.dumps(filters, ensure_ascii=False)) > 2000:
                raise ValueError("Liste filtreleri çok uzun.")
            columns = _string_list(view.get("columns", []), 30, 40, "Sütun")
            clean_views.append({"name": name, "filters": filters, "columns": columns})
        result["personnel_views"] = clean_views
    if "export" in payload:
        prefs = payload["export"]
        if not isinstance(prefs, dict) or set(prefs) - {"filename_template", "last_folder"}:
            raise ValueError("Çıktı tercihleri geçersiz.")
        template = _text(prefs.get("filename_template", ""), 120, "Dosya adı şablonu")
        if template and not export_filename(template, "schedule", "2026-01", 0, "").strip("_. "):
            raise ValueError("Dosya adı şablonu boş bir ad üretiyor.")
        result["export"] = {
            "filename_template": template,
            "last_folder": _text(prefs.get("last_folder", ""), 400, "Klasör"),
        }
    if "document_presets" in payload:
        presets = payload["document_presets"]
        if not isinstance(presets, list) or len(presets) > 30:
            raise ValueError("En fazla 30 tutanak şablonu kaydedilebilir.")
        clean_presets, preset_names = [], set()
        for preset in presets:
            if not isinstance(preset, dict) or not set(preset) <= {"name", "title", "code", "section", "description"}:
                raise ValueError("Tutanak şablonu geçersiz.")
            preset_name = _text(preset.get("name", ""), 60, "Tutanak şablonu adı")
            if not preset_name or preset_name.casefold() in preset_names:
                raise ValueError("Tutanak şablonu adı boş veya tekrar eden olamaz.")
            preset_names.add(preset_name.casefold())
            code = preset.get("code", "")
            if code not in DOCUMENT_PRESET_CODES:
                raise ValueError("Tutanak şablonu türü geçersiz.")
            description = preset.get("description", "")
            if not isinstance(description, str) or len(description.strip()) > 500:
                raise ValueError("Tutanak açıklaması en fazla 500 karakter olmalıdır.")
            clean_presets.append({
                "name": preset_name,
                "title": _text(preset.get("title", ""), 120, "Tutanak başlığı"),
                "code": code,
                "section": _text(preset.get("section", ""), 80, "Bölüm"),
                "description": description.strip(),
            })
        result["document_presets"] = clean_presets
    return result


MONTH_NAMES_TR = ["OCAK", "ŞUBAT", "MART", "NİSAN", "MAYIS", "HAZİRAN", "TEMMUZ",
                  "AĞUSTOS", "EYLÜL", "EKİM", "KASIM", "ARALIK"]
DEFAULT_FILENAME_TEMPLATE = "{TUR}_{AY}{PERSONEL}"


def export_filename(template: str, kind: str, month: str, week: int, employee: str, extension: str = "") -> str:
    """Builds a safe file name from placeholders: {TUR} {AY} {YIL} {AYNO} {AYADI} {HAFTA} {PERSONEL}."""
    year, number = month.split("-")
    values = {
        "TUR": "VARDIYA_LISTESI" if kind == "schedule" else "PUANTAJ",
        "AY": month, "YIL": year, "AYNO": number,
        "AYADI": MONTH_NAMES_TR[int(number) - 1],
        "HAFTA": f"{week + 1}.HAFTA" if kind == "schedule" else "",
        "PERSONEL": f"_{employee}" if employee else "",
    }
    name = template or DEFAULT_FILENAME_TEMPLATE
    for key, value in values.items():
        name = name.replace("{" + key + "}", value)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f{}]', "_", name).strip(" .")
    name = re.sub(r"_{2,}", "_", name)[:150] or values["TUR"]
    return f"{name}.{extension}" if extension else name


def merged(stored: Any) -> dict[str, Any]:
    result = json.loads(json.dumps(DEFAULTS))
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in result:
                result[key] = value
    return result


def load(store) -> dict[str, Any]:
    return merged(store._setting(SETTING_NAME, {}))


def save(store, payload: Any) -> dict[str, Any]:
    current = load(store)
    current.update({key: value for key, value in (payload or {}).items()} if isinstance(payload, dict) else {})
    normalized = normalize(current)
    with store.connection:
        store._write_setting(SETTING_NAME, normalized)
    return normalized


def classify_shift(start: str, end: str, settings: dict[str, Any]) -> str:
    """Saatlere göre vardiya türü önerir (yarım gün süresi, sonra başlangıç aralığı)."""
    def minutes(text: str) -> int:
        hour, minute = map(int, text.split(":"))
        return hour * 60 + minute

    if not start or not end:
        return ""
    begin, finish = minutes(start), minutes(end)
    duration = (finish - begin) % 1440 or 1440
    types = settings.get("shift_types", [])
    limit = float(settings.get("half_day_max_hours", 5))
    if limit and duration <= limit * 60 and any(item["id"] == "yarim" for item in types):
        return "yarim"
    for item in types:
        if not item.get("from"):
            continue
        low, high = minutes(item["from"]), minutes(item["to"])
        inside = low <= begin <= high if low <= high else begin >= low or begin <= high
        if inside:
            return item["id"]
    return ""

LIBRARY_SETTING = "template_library"
LIBRARY_LIMIT = 40
LIBRARY_ITEM_BYTES = 400_000


def normalize_library(items: Any) -> list[dict[str, Any]]:
    """Kayıtlı şablon biçimleri: ad, tablo türü ve şablon sayfalarının biçim ayarları."""
    if not isinstance(items, list) or len(items) > LIBRARY_LIMIT:
        raise ValueError(f"Şablon kitaplığında en fazla {LIBRARY_LIMIT} kayıt olabilir.")
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or not set(item) <= {"name", "kind", "sheets", "created"}:
            raise ValueError("Şablon kaydı geçersiz.")
        name = _text(item.get("name", ""), 60, "Şablon adı")
        kind = item.get("kind")
        if not name or kind not in ("schedule", "attendance"):
            raise ValueError("Şablon adı veya tablo türü geçersiz.")
        if (kind, name.casefold()) in seen:
            raise ValueError(f"'{name}' adlı şablon zaten var.")
        sheets = item.get("sheets")
        if not isinstance(sheets, dict) or not sheets or len(sheets) > 20:
            raise ValueError("Şablon sayfaları geçersiz.")
        for index, sheet in sheets.items():
            if not isinstance(index, str) or not index.isdigit() or not isinstance(sheet, dict):
                raise ValueError("Şablon sayfası geçersiz.")
        if len(json.dumps(sheets, ensure_ascii=False)) > LIBRARY_ITEM_BYTES:
            raise ValueError("Şablon kaydı çok büyük.")
        created = item.get("created", "")
        if not isinstance(created, str) or len(created) > 40:
            raise ValueError("Şablon tarihi geçersiz.")
        seen.add((kind, name.casefold()))
        result.append({"name": name, "kind": kind, "sheets": sheets, "created": created})
    return result


def load_library(store) -> list[dict[str, Any]]:
    value = store._setting(LIBRARY_SETTING, [])
    return value if isinstance(value, list) else []


def _ready_cells(rows: tuple[int, ...], columns: range, style: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{row}:{column}": dict(style) for row in rows for column in columns}


def _ready_library() -> list[dict[str, Any]]:
    """Ready-made format presets; they only touch formatting, never shift or schedule data."""
    print_setup = {"orientation": "landscape", "paper": "A4", "fit_width": 1, "fit_height": 1,
                   "center_horizontally": True, "page_numbers": True,
                   "margins": {"top": 1.0, "bottom": 1.0, "left": 0.8, "right": 0.8}}
    day_rows = (4, 5, 15, 16, 21, 22)
    section_colors = {"2:3": "#ddebf7", "13:3": "#e2efda", "19:3": "#fff2cc"}
    colored = _ready_cells(day_rows, range(4, 18), {"font_bold": True})
    colored |= _ready_cells((4, 15, 21), range(4, 18), {"font_bold": True, "color": "#f2f2f2"})
    colored |= {address: {"color": color, "font_bold": True} for address, color in section_colors.items()}

    def schedule(cells: dict[str, Any], zoom: int, setup: dict[str, Any] | None = None) -> dict[str, Any]:
        sheet: dict[str, Any] = {"cells": cells, "hide_notes": False, "zoom": zoom}
        if setup:
            sheet["page_setup"] = setup
        return {str(index): json.loads(json.dumps(sheet)) for index in range(6)}

    return [
        {"name": "Hazır · A4 yatay tek sayfa", "kind": "schedule",
         "description": "Her hafta tek A4 yatay sayfaya sığar; sayfa numarası ve ortalama açık.",
         "sheets": schedule({}, 55, print_setup)},
        {"name": "Hazır · Renkli bölüm başlıkları", "kind": "schedule",
         "description": "MARKET, TEMİZLİK ve ÖSSE başlıkları açık tonlarla ayrılır; gün başlıkları kalın.",
         "sheets": schedule(colored, 55, print_setup)},
        {"name": "Hazır · Okunaklı ekran", "kind": "schedule",
         "description": "Ekranda daha büyük ölçek ve kalın gün başlıkları; yazdırma tek sayfa.",
         "sheets": schedule(_ready_cells(day_rows, range(4, 18), {"font_bold": True}), 80, print_setup)},
        {"name": "Hazır · A4 yatay tek sayfa", "kind": "attendance",
         "description": "Puantaj tek A4 yatay sayfaya sığar; başlık satırları her sayfada yinelenir.",
         "sheets": {"0": {"cells": {}, "hide_notes": False, "zoom": 100,
                          "page_setup": print_setup | {"print_title_rows": "1:3"}}}},
        {"name": "Hazır · Okunaklı ekran", "kind": "attendance",
         "description": "Ekranda %120 ölçek; yazdırma ayarı tek sayfa.",
         "sheets": {"0": {"cells": {}, "hide_notes": False, "zoom": 120,
                          "page_setup": print_setup | {"print_title_rows": "1:3"}}}},
        {"name": "Hazır · Sade görünüm", "kind": "attendance",
         "description": "Alt not ve açıklama alanları gizlenir; yalnızca puantaj tablosu görünür.",
         "sheets": {"0": {"cells": {}, "hide_notes": True, "zoom": 100}}},
    ]


READY_LIBRARY = _ready_library()


def save_library(store, items: Any) -> list[dict[str, Any]]:
    clean = normalize_library(items)
    with store.connection:
        store._write_setting(LIBRARY_SETTING, clean)
    return clean
