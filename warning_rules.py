"""Kontrol uyarılarını yok sayma kuralları.

Uyarılar hiçbir zaman kaydı engellemez; kullanıcı bir uyarıyı
- bu seferlik (tür + kişi + gün),
- bu kişi için (tür + kişi),
- tümüyle (tür)
kapatabilir. Kurallar "Uyarı yönetimi" penceresinden geri açılır.
"""
from __future__ import annotations

from typing import Any

SETTING_NAME = "ignored_warnings"
SCOPES = ("once", "person", "type")
LIMIT = 2000


def _text(value: Any, limit: int = 80) -> str:
    return str(value if value is not None else "").strip()[:limit]


def item_key(scope: str, item: dict[str, Any]) -> str:
    kind = _text(item.get("type"))
    person = _text(item.get("employee_id")) or _text(item.get("name"))
    if scope == "type":
        return kind
    if scope == "person":
        return f"{kind}|{person}"
    return f"{kind}|{person}|{_text(item.get('date'), 10)}"


def normalize(value: Any) -> dict[str, list[str]]:
    source = value if isinstance(value, dict) else {}
    result: dict[str, list[str]] = {}
    for scope in SCOPES:
        items = source.get(scope, [])
        clean = []
        for item in items if isinstance(items, list) else []:
            text = _text(item, 160)
            if text and text not in clean:
                clean.append(text)
        result[scope] = clean[-LIMIT:]
    return result


def load(store) -> dict[str, list[str]]:
    return normalize(store._setting(SETTING_NAME, {}))


def save(store, value: Any) -> dict[str, list[str]]:
    clean = normalize(value)
    with store.connection:
        store._write_setting(SETTING_NAME, clean)
    return clean


def ignore(store, scope: Any, item: Any) -> dict[str, list[str]]:
    if scope not in SCOPES:
        raise ValueError("Yok sayma kapsamı geçersiz.")
    if not isinstance(item, dict) or not _text(item.get("type")):
        raise ValueError("Yok sayılacak uyarı geçersiz.")
    rules = load(store)
    key = item_key(scope, item)
    if key not in rules[scope]:
        rules[scope].append(key)
    return save(store, rules)


def matched_scope(item: dict[str, Any], rules: dict[str, list[str]]) -> str | None:
    for scope in ("type", "person", "once"):
        if item_key(scope, item) in rules.get(scope, ()):
            return scope
    return None


def apply(report: dict[str, Any], rules: dict[str, list[str]]) -> dict[str, Any]:
    """Yok sayılan uyarıları rapordan ayırır; sayıları yeniden hesaplar."""
    visible, hidden = [], []
    for item in report.get("items", []):
        (hidden if matched_scope(item, rules) else visible).append(item)
    counts = {severity: sum(item["severity"] == severity for item in visible) for severity in ("error", "warning", "info")}
    return {**report, "items": visible, "counts": counts, "ignored": len(hidden), "ignored_items": hidden}
