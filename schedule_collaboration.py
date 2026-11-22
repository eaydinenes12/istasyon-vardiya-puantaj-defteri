"""Merge independent schedule edits without trusting client-supplied baselines."""
from __future__ import annotations

import copy
import json


def apply_entry_changes(base, changes):
    if not isinstance(changes, dict) or set(changes) != {"upsert_rows", "remove_row_ids", "day_changes"}:
        raise ValueError("Vardiya alan değişiklikleri geçersiz.")
    if not all(isinstance(changes[key], list) for key in ("upsert_rows", "remove_row_ids", "day_changes")):
        raise ValueError("Vardiya alan değişiklikleri geçersiz.")
    rows = {row["row_id"]: copy.deepcopy(row) for row in base}
    removed = changes["remove_row_ids"]
    if any(not isinstance(row_id, str) or not row_id for row_id in removed) or len(set(removed)) != len(removed):
        raise ValueError("Silinecek vardiya satırları geçersiz.")
    for row_id in removed:
        if row_id not in rows:
            raise ValueError("Silinecek vardiya satırı kaynakta bulunamadı.")
        del rows[row_id]

    replaced = set()
    for row in changes["upsert_rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("row_id"), str) or not row["row_id"]:
            raise ValueError("Eklenecek veya değiştirilecek vardiya satırı geçersiz.")
        if row["row_id"] in removed or row["row_id"] in replaced:
            raise ValueError("Vardiya satırı bir istekte birden fazla kez değiştirilemez.")
        replaced.add(row["row_id"])
        rows[row["row_id"]] = copy.deepcopy(row)

    changed_cells = set()
    for change in changes["day_changes"]:
        if not isinstance(change, dict) or set(change) != {"row_id", "day", "value"}:
            raise ValueError("Vardiya gün alanı değişikliği geçersiz.")
        row_id, day = change["row_id"], change["day"]
        if not isinstance(row_id, str) or not row_id or not isinstance(day, str) or not day:
            raise ValueError("Vardiya gün alanı değişikliği geçersiz.")
        key = (row_id, day)
        if key in changed_cells or row_id in replaced:
            raise ValueError("Vardiya gün alanı bir istekte birden fazla kez değiştirilemez.")
        changed_cells.add(key)
        row = rows.get(row_id)
        if row is None:
            raise ValueError("Vardiya gün değişikliğinin satırı bulunamadı.")
        row = {**row, "days": dict(row.get("days") or {})}
        if change["value"] is None:
            row["days"].pop(day, None)
        else:
            row["days"][day] = copy.deepcopy(change["value"])
        rows[row_id] = row
    return list(rows.values())


def archived_entries(connection, month, revision):
    if not revision:
        return []
    row = connection.execute(
        "SELECT payload FROM archive_revisions WHERE kind='schedule' AND period=? AND revision=? "
        "ORDER BY id DESC LIMIT 1", (month, revision),
    ).fetchone()
    return json.loads(row["payload"]) if row else None


def merge_entries(base, current, proposed):
    baseline, live, edited = ({row["row_id"]: row for row in rows}
                              for rows in (base, current, proposed))
    result = []
    conflicts = []
    for row_id in dict.fromkeys((*live, *edited, *baseline)):
        before, saved, local = (rows.get(row_id) for rows in (baseline, live, edited))
        if local == before or local == saved:
            row = saved
        elif saved == before:
            row = local
        elif any(item is None for item in (before, saved, local)) or not (
            _metadata(before) == _metadata(saved) == _metadata(local)
        ):
            conflicts.append({"row_id": row_id, "employee_id": (saved or local or before)["employee_id"],
                              "date": None, "reason": "Satır yerleşimi değişti."})
            continue
        else:
            row = {**saved, "days": {}}
            days = (before["days"], saved["days"], local["days"])
            for day in sorted(set().union(*days)):
                original, on_disk, in_draft = (values.get(day) for values in days)
                if in_draft == original or in_draft == on_disk:
                    value = on_disk
                elif on_disk == original:
                    value = in_draft
                else:
                    conflicts.append({"row_id": row_id, "employee_id": saved["employee_id"], "date": day,
                                      "saved": on_disk, "draft": in_draft})
                    continue
                if value is not None:
                    row["days"][day] = value
        if row is not None:
            result.append(row)
    return result, conflicts


def _metadata(row):
    return {key: value for key, value in row.items() if key != "days"}


def merge_attendance(connection, month, revision, current, proposed):
    row = connection.execute(
        "SELECT payload FROM archive_revisions WHERE kind='attendance' AND period=? AND revision=? "
        "ORDER BY id DESC LIMIT 1", (month, revision),
    ).fetchone()
    if row is None and revision:
        return None, [{"reason": "Başlangıç puantaj sürümü bulunamadı."}]
    baseline = json.loads(row["payload"]) if row else {}
    if "overrides" not in baseline:
        baseline = {"overrides": baseline}
    for field in ("deductions", "roster", "overtime"):
        default = {"added": [], "removed": [], "order": []} if field == "roster" else {}
        if proposed[field] != current[field] and proposed[field] != baseline.get(field, default):
            return None, [{"reason": "Kesinti, mesai veya personel sırası değişikliği için güncel puantajı açın."}]
    original, saved, local = (data["overrides"] for data in (baseline, current, proposed))
    result, conflicts = {}, []
    for day in sorted(original.keys() | saved.keys() | local.keys()):
        before, live, draft = (values.get(day, {}) for values in (original, saved, local))
        for employee in sorted(before.keys() | live.keys() | draft.keys()):
            previous, stored, edited = (values.get(employee) for values in (before, live, draft))
            if edited == previous or edited == stored:
                value = stored
            elif stored == previous:
                value = edited
            else:
                conflicts.append({"date": day, "employee_id": int(employee), "saved": stored, "draft": edited})
                continue
            if value is not None:
                result.setdefault(day, {})[employee] = value
    return {**proposed, "overrides": result, **{key: current[key] for key in ("deductions", "roster", "overtime")}}, conflicts


def restore_cell_entries(connection, month, revision, current, row_id, day):
    history = connection.execute(
        "SELECT revision, payload FROM archive_revisions WHERE kind='schedule' AND period=? AND id >= "
        "(SELECT max(id) FROM archive_revisions WHERE kind='schedule' AND period=? AND revision=?) ORDER BY id",
        (month, month, revision),
    )
    snapshots = (json.loads(row["payload"]) for row in history)
    original = next(snapshots, None)
    if original is None:
        raise ValueError("Geri alınacak eski sürüm bulunamadı.")

    def find(entries):
        return next((row for row in entries if row["row_id"] == row_id), None)

    original_row, saved_row = find(original), find(current)
    if original_row is None or saved_row is None or _metadata(original_row) != _metadata(saved_row):
        raise ValueError("Vardiya satırı değişti. Bu hücre tek başına geri alınamaz.")
    original_value = original_row["days"].get(day)
    saved_value = saved_row["days"].get(day)
    if original_value == saved_value:
        raise ValueError("Bu hücrede geri alınacak fark yok.")
    last = original_value
    transitions = 0
    for entries in snapshots:
        row = find(entries)
        if row is None or _metadata(row) != _metadata(original_row):
            raise ValueError("Vardiya satırı sonradan değişti.")
        value = row["days"].get(day)
        transitions += value != last
        last = value
        if transitions > 1:
            break
    transitions += saved_value != last
    if transitions != 1:
        raise ValueError("Hücre bu sürümden sonra birden fazla kez değişti. Daha yeni sürümü seçin.")
    days = dict(saved_row["days"])
    if original_value is None:
        days.pop(day, None)
    else:
        days[day] = original_value
    return [{**row, "days": days} if row["row_id"] == row_id else row for row in current]


def restore_attendance_code(connection, month, revision, current, employee_id, day):
    history = connection.execute(
        "SELECT payload FROM archive_revisions WHERE kind='attendance' AND period=? AND id >= "
        "(SELECT max(id) FROM archive_revisions WHERE kind='attendance' AND period=? AND revision=?) ORDER BY id",
        (month, month, revision),
    )
    snapshots = (json.loads(row["payload"]) for row in history)
    original = next(snapshots, None)
    if original is None:
        raise ValueError("Geri alınacak eski puantaj sürümü bulunamadı.")

    def value(payload):
        if employee_id in payload.get("roster", {}).get("removed", []):
            raise ValueError("Personel puantaj listesinden çıkarılmış.")
        return payload.get("overrides", payload).get(day, {}).get(str(employee_id))

    before, saved = value(original), value(current)
    if before == saved:
        raise ValueError("Bu hücrede geri alınacak manuel kod farkı yok.")
    last, transitions = before, 0
    for snapshot in snapshots:
        code = value(snapshot)
        transitions += code != last
        last = code
        if transitions > 1:
            break
    if transitions + (saved != last) != 1:
        raise ValueError("Puantaj hücresi bu sürümden sonra birden fazla kez değişti. Daha yeni sürümü seçin.")
    return before
