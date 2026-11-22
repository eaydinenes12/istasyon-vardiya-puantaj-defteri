"""Person-linked issue and return records for keys, cards, clothing, and equipment."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


ASSET_CATEGORIES = {"key", "card", "clothing", "equipment", "other"}


def initialize(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS employee_assets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          employee_id INTEGER NOT NULL REFERENCES employees(id),
          category TEXT NOT NULL,
          item TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          issued_at TEXT NOT NULL,
          issued_by TEXT NOT NULL,
          returned_at TEXT,
          returned_by TEXT
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS employee_assets_employee ON employee_assets(employee_id, id DESC)"
    )


def _employee(store, employee_id: Any, active_only: bool = False):
    if not isinstance(employee_id, int) or isinstance(employee_id, bool):
        raise ValueError("Personel seçimi geçersiz.")
    row = store.connection.execute(
        "SELECT id, name, active FROM employees WHERE id=?", (employee_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Personel bulunamadı.")
    if active_only and not row["active"]:
        raise ValueError("Pasif personele yeni zimmet verilemez.")
    return row


def list_assets(store, employee_id: Any) -> list[dict[str, Any]]:
    _employee(store, employee_id)
    return [
        {
            "id": int(row["id"]),
            "employee_id": int(row["employee_id"]),
            "category": row["category"],
            "item": row["item"],
            "note": row["note"],
            "issued_at": row["issued_at"],
            "issued_by": row["issued_by"],
            "returned_at": row["returned_at"] or "",
            "returned_by": row["returned_by"] or "",
        }
        for row in store.connection.execute(
            "SELECT id, employee_id, category, item, note, issued_at, issued_by, returned_at, returned_by "
            "FROM employee_assets WHERE employee_id=? ORDER BY id DESC",
            (employee_id,),
        )
    ]


def issue_asset(store, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"action", "employee_id", "category", "item", "note"}:
        raise ValueError("Zimmet teslim isteği geçersiz.")
    person = _employee(store, payload["employee_id"], active_only=True)
    category, item, note = payload["category"], payload["item"], payload["note"]
    if not isinstance(category, str) or category not in ASSET_CATEGORIES:
        raise ValueError("Zimmet türü geçersiz.")
    if not isinstance(item, str):
        raise ValueError("Zimmet adı metin olmalıdır.")
    item = " ".join(item.split())
    if not item or len(item) > 80:
        raise ValueError("Zimmet adı 1-80 karakter olmalıdır.")
    if not isinstance(note, str) or len(note.strip()) > 250:
        raise ValueError("Zimmet açıklaması en fazla 250 karakter olabilir.")
    note = note.strip()
    issued_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    issued_by = store.actor or store.user_name()
    store._backup_before_change()
    with store.connection:
        cursor = store.connection.execute(
            "INSERT INTO employee_assets(employee_id, category, item, note, issued_at, issued_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (person["id"], category, item, note, issued_at, issued_by),
        )
        asset_id = int(cursor.lastrowid)
        store.record_change(
            "employee_asset.issue", f"{person['name']} için {item} teslim edildi",
            {"employee_id": int(person["id"]), "asset_id": asset_id, "category": category, "item": item},
        )
    return next(asset for asset in list_assets(store, int(person["id"])) if asset["id"] == asset_id)


def return_asset(store, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"action", "asset_id"}:
        raise ValueError("Zimmet iade isteği geçersiz.")
    asset_id = payload["asset_id"]
    if not isinstance(asset_id, int) or isinstance(asset_id, bool):
        raise ValueError("Zimmet seçimi geçersiz.")
    row = store.connection.execute(
        "SELECT a.employee_id, a.item, a.returned_at, e.name "
        "FROM employee_assets a JOIN employees e ON e.id=a.employee_id WHERE a.id=?",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Zimmet kaydı bulunamadı.")
    if row["returned_at"]:
        raise ValueError("Bu zimmet daha önce iade edilmiş.")
    returned_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    returned_by = store.actor or store.user_name()
    store._backup_before_change()
    with store.connection:
        cursor = store.connection.execute(
            "UPDATE employee_assets SET returned_at=?, returned_by=? WHERE id=? AND returned_at IS NULL",
            (returned_at, returned_by, asset_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Zimmet başka bir işlemde güncellendi; listeyi yenileyin.")
        store.record_change(
            "employee_asset.return", f"{row['name']} için {row['item']} iade alındı",
            {"employee_id": int(row["employee_id"]), "asset_id": asset_id, "item": row["item"]},
        )
    return next(asset for asset in list_assets(store, int(row["employee_id"])) if asset["id"] == asset_id)
