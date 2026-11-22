"""Veri sağlığı denetimi ve güvenli onarım (başlangıç kontrolü)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JSON_TABLES = ("schedule_months", "attendance_months", "app_settings", "archive_revisions")
CLOUD_SYNC_ENV = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")


def cloud_sync_root(path: Any) -> str:
    """Return the OneDrive root that contains ``path``, or "" when it is not synced."""
    try:
        target = Path(path).resolve()
    except (OSError, TypeError, ValueError):
        return ""
    for name in CLOUD_SYNC_ENV:
        root = os.environ.get(name)
        if not root:
            continue
        try:
            resolved = Path(root).resolve()
            target.relative_to(resolved)
        except (OSError, ValueError):
            continue
        return str(resolved)
    for parent in (target, *target.parents):
        if parent.name.casefold().startswith("onedrive"):
            return str(parent)
    return ""


def _issue(code: str, level: str, message: str, count: int = 1, repairable: bool = False) -> dict[str, Any]:
    return {"code": code, "level": level, "message": message, "count": count, "repairable": repairable}


def _bad_json_rows(connection, table: str) -> list[Any]:
    key = "name" if table == "app_settings" else ("id" if table == "archive_revisions" else "period")
    bad = []
    for row in connection.execute(f"SELECT {key} AS k, payload FROM {table}"):
        try:
            json.loads(row["payload"])
        except (TypeError, ValueError):
            bad.append(row["k"])
    return bad


def _employee_ids(connection) -> set[int]:
    return {int(row["id"]) for row in connection.execute("SELECT id FROM employees")}


def _orphan_profiles(store) -> list[str]:
    profiles = store._setting("employee_profiles", {})
    if not isinstance(profiles, dict):
        return []
    ids = _employee_ids(store.connection)
    return [key for key in profiles if not str(key).isdigit() or int(key) not in ids]


def _orphan_rows(connection, table: str) -> int:
    return int(connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE employee_id NOT IN (SELECT id FROM employees)"
    ).fetchone()[0])


def check(store) -> dict[str, Any]:
    connection = store.connection
    issues: list[dict[str, Any]] = []
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        issues.append(_issue("integrity", "error", f"Veritabanı bütünlük denetimi başarısız: {integrity}. Son sağlam yedeği geri yükleyin."))
    for table in JSON_TABLES:
        bad = _bad_json_rows(connection, table)
        if bad:
            issues.append(_issue(f"json:{table}", "error", f"{table} tablosunda okunamayan {len(bad)} kayıt var ({', '.join(map(str, bad[:5]))}).", len(bad), True))
    for table, label in (("attendance_events", "izin/rapor kaydı"), ("employee_balance_openings", "devir bakiyesi")):
        count = _orphan_rows(connection, table)
        if count:
            issues.append(_issue(f"orphan:{table}", "warning", f"Silinmiş personele ait {count} {label} kaldı.", count, True))
    orphans = _orphan_profiles(store)
    if orphans:
        issues.append(_issue("orphan:profiles", "warning", f"Silinmiş personele ait {len(orphans)} personel kartı kaldı.", len(orphans), True))
    names = [row["name"] for row in connection.execute("SELECT name FROM employees")]
    folded: dict[str, int] = {}
    for name in names:
        key = " ".join(name.split()).casefold()
        folded[key] = folded.get(key, 0) + 1
    duplicates = [name for name, count in folded.items() if count > 1]
    if duplicates:
        issues.append(_issue("duplicate-names", "warning", f"Aynı adı taşıyan personel kayıtları: {', '.join(duplicates[:5])}. Birini yeniden adlandırın veya silin.", len(duplicates)))
    backups = store.backups()
    if not backups:
        issues.append(_issue("no-backup", "warning", "Henüz hiç yedek alınmamış. Arşiv › Yedekler bölümünden yedek alın.", 1, True))
    else:
        latest = datetime.fromisoformat(backups[0]["created_at"])
        age = (datetime.now(timezone.utc) - latest).days
        if age > 7:
            issues.append(_issue("old-backup", "warning", f"Son yedek {age} gün önce alınmış.", 1, True))
    synced = cloud_sync_root(store.path)
    mirror = store.backup_mirror_folder()
    if synced:
        # Inspect names only; a similar filename is not proof of a sync conflict.
        stem = store.path.stem.casefold()
        siblings = (p for p in store.path.parent.iterdir() if p.suffix.casefold() == store.path.suffix.casefold())
        possible_copies = sum(1 for p in siblings if p.is_file() and
                              p.stem.casefold().startswith((stem + "-", stem + " (", stem + " - ")))
        if possible_copies:
            issues.append(_issue("possible-sync-copy", "warning",
                                 "Aynı klasörde benzer adlı veritabanı dosyaları var. Bu tek başına eşitleme "
                                 "çakışması kanıtı değildir. Dosyaları birleştirmeyin veya silmeyin. Önce çalışan "
                                 "şubeyi ve yolu Uygulama bilgisi ekranından kontrol edin; Arşiv › Yedekler'den "
                                 "yedek alıp doğrulayın. Uygulamayı normal şekilde kapattıktan sonra OneDrive "
                                 "eşitleme durumunu ve kopyaları inceleyin.", possible_copies))
    if synced and (not mirror or cloud_sync_root(mirror)):
        issues.append(_issue(
            "cloud-sync", "info",
            f"Veritabanı OneDrive ile eşitlenen klasörde ({synced}). Uygulama açıkken OneDrive dosyayı eşitlerse "
            "çakışma kopyaları oluşabilir. Arşiv › Yedekler › Yedek kopya klasörü bölümünden OneDrive dışında "
            "bir klasör seçerek yedeklerin ikinci kopyasını alın. Veritabanı kilidi tek başına eşitleme "
            "hatası anlamına gelmez; aynı veriyi başka bir açık uygulamanın kullanıp kullanmadığını kontrol edin.",
        ))
    level = "error" if any(item["level"] == "error" for item in issues) else (
        "warning" if any(item["level"] == "warning" for item in issues) else "ok")
    return {
        "status": level,
        "issues": issues,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "people": len(names),
        "size_bytes": store.path.stat().st_size if store.path.exists() else 0,
    }


def repair(store) -> dict[str, Any]:
    """Onarılabilir sorunları giderir; önce yedek alır. Sonuç denetimini döner."""
    before = check(store)
    if any(item["code"] == "integrity" for item in before["issues"]):
        raise ValueError("Veritabanı bütünlüğü bozuk; otomatik onarım yapılmadı. Yedekten geri yükleyin.")
    if not any(item["repairable"] for item in before["issues"]):
        return {"repaired": [], "backup": None, "health": before}
    backup = store.create_backup("before-repair")
    connection = store.connection
    repaired: list[str] = []
    with connection:
        for table in JSON_TABLES:
            bad = _bad_json_rows(connection, table)
            if bad:
                key = "name" if table == "app_settings" else ("id" if table == "archive_revisions" else "period")
                connection.executemany(f"DELETE FROM {table} WHERE {key}=?", [(item,) for item in bad])
                repaired.append(f"{table}: {len(bad)} okunamayan kayıt kaldırıldı")
        for table in ("attendance_events", "employee_balance_openings"):
            cursor = connection.execute(f"DELETE FROM {table} WHERE employee_id NOT IN (SELECT id FROM employees)")
            if cursor.rowcount:
                repaired.append(f"{table}: {cursor.rowcount} sahipsiz kayıt kaldırıldı")
        orphans = _orphan_profiles(store)
        if orphans:
            profiles = store._setting("employee_profiles", {})
            for key in orphans:
                profiles.pop(key, None)
            store._write_setting("employee_profiles", profiles)
            repaired.append(f"personel kartı: {len(orphans)} sahipsiz kart kaldırıldı")
        if repaired:
            store.record_change("data.repair", "Veri onarımı yapıldı", {"items": repaired, "backup": backup["id"]})
    if any(item["code"] in ("no-backup", "old-backup") for item in before["issues"]):
        repaired.append(f"yeni yedek alındı: {backup['id']}")
    connection.execute("PRAGMA optimize")
    return {"repaired": repaired, "backup": backup["id"], "health": check(store)}
