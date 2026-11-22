"""Read-only workspace evidence and account-scoped, revision-checked recovery drafts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import secrets

import controls
import linked_leave
import operations
import scheduling

DRAFT_SETTING = "workspace_recovery_drafts"


def _owner(handler):
    user = getattr(handler, "current_user", None)
    identity = f"user:{user['username'].casefold()}" if user else "anonymous"
    return hashlib.sha256(identity.encode()).hexdigest()


def _revisions(store, kind, month):
    if kind == "schedule":
        return {period: store.schedule_month(period)["revision"] for period in scheduling.adjacent_months(month)}
    return {
        "attendance": store.attendance_month(month)["revision"],
        "schedule": {period: store.schedule_month(period)["revision"] for period in scheduling.adjacent_months(month)},
    }


def normalize_snapshot(store, kind, month, snapshot, payroll=True):
    people = store.people()
    if not isinstance(snapshot, dict):
        raise ValueError("Taslak içeriği geçersiz.")
    if kind == "schedule":
        if set(snapshot) != {"entries", "neighborEntries"} or not isinstance(snapshot["neighborEntries"], dict):
            raise ValueError("Vardiya taslağı geçersiz.")
        adjacent = set(scheduling.adjacent_months(month)) - {month}
        if set(snapshot["neighborEntries"]) != adjacent:
            raise ValueError("Taslak önceki ve sonraki ayın sürümlerini içermelidir.")
        return {
            "entries": scheduling.normalize_schedule_entries(month, snapshot["entries"], people),
            "neighborEntries": {
                period: scheduling.normalize_schedule_entries(period, entries, people)
                for period, entries in snapshot["neighborEntries"].items()
            },
        }
    if set(snapshot) != {"overrides", "deductions", "overtime"}:
        raise ValueError("Puantaj taslağı geçersiz.")
    if not payroll and snapshot["deductions"]:
        raise PermissionError("Kesinti taslağı için bordro yetkisi gereklidir.")
    return {
        "overrides": scheduling.normalize_attendance_overrides(month, snapshot["overrides"], people),
        "deductions": scheduling.normalize_attendance_deductions(snapshot["deductions"], people),
        "overtime": scheduling.normalize_attendance_overtime(snapshot["overtime"], people),
    }


def draft_record(store, owner, kind, month, payroll=True):
    record = store._setting(DRAFT_SETTING, {}).get(owner, {}).get(f"{kind}:{month}")
    if not record:
        return None
    result = json.loads(json.dumps(record))
    result["stale"] = result["revisions"] != _revisions(store, kind, month)
    if kind == "attendance" and not payroll:
        result["snapshot"]["deductions"] = {}
    return result


def save_draft(store, owner, kind, month, payload, payroll=True):
    if set(payload) != {"month", "snapshot", "revisions", "draft_revision"}:
        raise ValueError("Taslak alanları geçersiz.")
    if not isinstance(payload["draft_revision"], str):
        raise ValueError("Taslak sürümü geçersiz.")
    snapshot = normalize_snapshot(store, kind, month, payload["snapshot"], payroll)
    if len(json.dumps(snapshot, ensure_ascii=False)) > 2_000_000:
        raise ValueError("Taslak çok büyük; değişiklikleri kaydedin veya daha küçük gruplarla çalışın.")
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        if payload["revisions"] != _revisions(store, kind, month):
            raise ValueError("Kayıtlı ay değişti. Mevcut taslağı koruyun; güncel kaynakla karşılaştırın.")
        records = store._setting(DRAFT_SETTING, {})
        owned = records.setdefault(owner, {})
        key = f"{kind}:{month}"
        old = owned.get(key)
        if (old["draft_revision"] if old else "") != payload["draft_revision"]:
            raise ValueError("Taslak başka pencerede değişti. Bu pencerenin içeriği üzerine yazılmadı.")
        if key not in owned and len(owned) >= 12:
            raise ValueError("12 kurtarma taslağı var. Eski taslakları inceleyip açıkça kaldırın.")
        record = {
            "snapshot": snapshot, "revisions": payload["revisions"],
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "draft_revision": secrets.token_hex(16),
        }
        owned[key] = record
        store._write_setting(DRAFT_SETTING, records)
        connection.commit()
        return record
    except Exception:
        connection.rollback()
        raise


def discard_draft(store, owner, kind, month, revision):
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        records = store._setting(DRAFT_SETTING, {})
        owned = records.get(owner, {})
        old = owned.get(f"{kind}:{month}")
        if old and old["draft_revision"] != revision:
            raise ValueError("Taslak değişti; silmeden önce yeniden inceleyin.")
        owned.pop(f"{kind}:{month}", None)
        if not owned:
            records.pop(owner, None)
        store._write_setting(DRAFT_SETTING, records)
        store.connection.commit()
    except Exception:
        store.connection.rollback()
        raise


def workspace_status(store, kind, month):
    current = store.schedule_month(month) if kind == "schedule" else store.attendance_month(month)
    published = store._setting("published_plans", {}).get(month)
    result = {
        "month": month, "kind": kind, "revision": current["revision"],
        "saved": bool(current["revision"]), "locked": month in store.locked_periods(),
        "revisions": _revisions(store, kind, month),
    }
    if kind == "schedule":
        result["published"] = "none" if not published else (
            "current" if published.get("source_revision") == current["revision"] else "changed"
        )
        result["has_plan"] = any(row.get("days") for row in current["entries"])
    return result


def bulk_check(store, payload):
    import schedule_protection
    if set(payload) != {"month", "months"} or not isinstance(payload["months"], dict):
        raise ValueError("Toplu giriş önizlemesi geçersiz.")
    month = scheduling.validate_month(payload["month"])
    if set(payload["months"]) != set(scheduling.adjacent_months(month)):
        raise ValueError("Toplu giriş üç komşu dönemi içermelidir.")
    normalized = {}
    before = []
    skipped = []
    for period, data in payload["months"].items():
        if not isinstance(data, dict) or set(data) != {"entries", "revision"}:
            raise ValueError("Toplu giriş dönem alanları geçersiz.")
        original = store.schedule_month(period)
        if original["revision"] != data["revision"]:
            raise ValueError("Önizlemenin kaynağı değişti; ayı yeniden açın.")
        before.extend(original["entries"])
        entries = scheduling.normalize_schedule_entries(period, data["entries"], store.people())
        skipped.extend(schedule_protection.keep_protected(store, period, original["entries"], entries))
        prepared = linked_leave.prepare_schedule_save(store, period, entries)
        if prepared["conflicts"]:
            raise ValueError("Onaylı izinle çakışma var; Bağlantılı izinler aracından inceleyin.")
        normalized[period] = {"entries": prepared["entries"], "revision": original["revision"]}
    store.assert_periods_unlocked([
        period for period, data in normalized.items()
        if data["entries"] != store.schedule_month(period)["entries"]
    ])

    class PreviewStore:
        def schedule_month(self, period):
            return normalized.get(period) or store.schedule_month(period)

        def __getattr__(self, name):
            return getattr(store, name)

    names = {person["id"]: person["name"] for person in store.people()}
    controls.assert_no_new_overlaps(before, [row for data in normalized.values() for row in data["entries"]], names)
    for period, data in normalized.items():
        operations.validate_schedule_in_transaction(PreviewStore(), period, data["entries"])
    findings = operations.quality_report(PreviewStore(), month)["findings"]
    return {"months": normalized, "findings": findings, "skipped": skipped, "persisted": False}


def attendance_check(store, payload, payroll=True):
    if set(payload) != {"month", "snapshot", "revisions"}:
        raise ValueError("Puantaj toplu giriş alanları geçersiz.")
    month = scheduling.validate_month(payload["month"])
    store.assert_periods_unlocked([month])
    if payload["revisions"] != _revisions(store, "attendance", month):
        raise ValueError("Puantaj veya kaynak aylar değişti; önizlemeyi yeniden hazırlayın.")
    return {"snapshot": normalize_snapshot(store, "attendance", month, payload["snapshot"], payroll), "persisted": False}


def handle(handler, method, path, query):
    parts = path.split("/")
    if len(parts) < 4 or parts[1] != "api" or parts[2] not in ("schedule", "attendance"):
        return False
    kind = parts[2]
    action = "/".join(parts[3:])
    if (method, action) not in {
        ("GET", "workspace"), ("GET", "draft"), ("PUT", "draft"), ("POST", "draft/discard"),
        ("POST", "bulk-check"),
    }:
        return False
    payload = handler._read_json(2_500_000) if method != "GET" else None
    month = scheduling.validate_month(payload.get("month", "") if payload is not None else query.get("month", [""])[0])
    with handler._store() as store:
        owner = _owner(handler)
        payroll = handler._has_permission("payroll", store)
        if action == "workspace":
            result = workspace_status(store, kind, month)
            if query.get("evidence") == ["1"] and (
                not store.users_enabled() or (getattr(handler, "current_user", None) or {}).get("role") == "admin"
                or handler._is_internal()
            ):
                quality = operations.quality_report(store, month)
                closing = operations.closing_checklist(store, month)
                approval = controls.approval_status(
                    month, store._setting("approvals", {}).get(month), handler._month_revisions(store, month),
                )
                result["verification"] = {
                    "plan": any(row.get("days") for row in store.schedule_month(month)["entries"]),
                    "quality": not quality["counts"]["error"] and not quality["counts"]["warning"],
                    "published": workspace_status(store, "schedule", month)["published"] == "current",
                    "closing": closing["complete"],
                    "approvals": not approval["next"] and not approval["stale"],
                    "pdks": bool(store._setting("pdks_records", {}).get(month)),
                }
        elif action == "bulk-check":
            result = bulk_check(store, payload) if kind == "schedule" else attendance_check(store, payload, payroll)
        elif method == "GET":
            result = {"draft": draft_record(store, owner, kind, month, payroll)}
        elif method == "PUT":
            result = {"draft": save_draft(store, owner, kind, month, payload, payroll)}
        else:
            if set(payload) != {"month", "draft_revision"} or not isinstance(payload["draft_revision"], str):
                raise ValueError("Taslak silme alanları geçersiz.")
            discard_draft(store, owner, kind, month, payload["draft_revision"])
            result = {"discarded": True}
        handler._json(result)
    return True
