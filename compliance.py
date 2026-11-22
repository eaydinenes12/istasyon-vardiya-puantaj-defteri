"""İş Kanunu uygunluk kontrolleri (B grubu).

Kurallar (4857 sayılı İş Kanunu ve yönetmelikler):
- md. 46: 7 günlük süre içinde en az 24 saat kesintisiz hafta tatili.
- İş Sağlığı ve Güvenliği bakımından postalar halinde çalışma yönetmeliği: iki posta arası en az 11 saat dinlenme.
- md. 69 / postalar yönetmeliği: gece postası en fazla bir hafta (bazı sektörlerde iki hafta) üst üste.
- md. 63: denkleştirme döneminde haftalık ortalama 45 saat.
- md. 71-73, 88 / Gebe veya emziren kadınlar yönetmeliği, Fazla çalışma yönetmeliği md. 7:
  18 yaş altı gece çalışamaz; gebe, emziren ve 18 yaş altı fazla çalışma yapamaz.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

import scheduling

DEFAULT_SETTINGS = {
    "legal_rest_hours": 11.0, "max_night_weeks": 2, "balancing_weeks": 8,
    "min_staffing": {}, "min_staffing_windows": [],
}
SPECIAL_STATUSES = {
    "pregnant": "Hamile",
    "nursing": "Emziren",
    "under18": "18 yaş altı",
    "disabled": "Engelli",
}
NIGHT_SHIFT_MINUTES = 180
WEEKLY_REST_HOURS = 24
WEEKLY_AVERAGE_LIMIT = 45


def normalize_settings(value: Any) -> dict[str, Any]:
    if value is None:
        return dict(DEFAULT_SETTINGS)
    if not isinstance(value, dict):
        raise ValueError("Uyum ayarları geçersiz.")
    result = dict(DEFAULT_SETTINGS)
    result["min_staffing"] = normalize_min_staffing(value.get("min_staffing", {}))
    result["min_staffing_windows"] = normalize_min_staffing_windows(value.get("min_staffing_windows", []))
    try:
        if "legal_rest_hours" in value:
            result["legal_rest_hours"] = round(float(value["legal_rest_hours"]), 2)
        if "max_night_weeks" in value:
            result["max_night_weeks"] = int(value["max_night_weeks"])
        if "balancing_weeks" in value:
            result["balancing_weeks"] = int(value["balancing_weeks"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Uyum ayarları sayı olmalıdır.") from exc
    if not 0 <= result["legal_rest_hours"] <= 24:
        raise ValueError("Vardiya arası dinlenme 0-24 saat olmalıdır.")
    if not 0 <= result["max_night_weeks"] <= 8:
        raise ValueError("Üst üste gece haftası sınırı 0-8 olmalıdır.")
    if result["balancing_weeks"] not in (0, 2, 4, 8, 12, 16):
        raise ValueError("Denkleştirme dönemi 0 (kapalı), 2, 4, 8, 12 veya 16 hafta olmalıdır.")
    return result


def normalize_min_staffing(value: Any) -> dict[str, int]:
    """Bölüm adı -> günlük en az çalışan sayısı (0 = denetim yok)."""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("Asgari kadro ayarı geçersiz.")
    result: dict[str, int] = {}
    for section, count in value.items():
        name = str(section or "").strip()[:60]
        try:
            number = int(count)
        except (TypeError, ValueError) as exc:
            raise ValueError("Asgari kadro sayısı tam sayı olmalıdır.") from exc
        if not 0 <= number <= 99:
            raise ValueError("Asgari kadro 0-99 kişi olmalıdır.")
        if name and number:
            result[name] = number
    return result


def _minutes(value: Any, *, allow_end_of_day: bool = False) -> int:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("Saat aralığı HH:MM biçiminde olmalıdır.")
    try:
        hour, minute = int(value[:2]), int(value[3:])
    except ValueError as exc:
        raise ValueError("Saat aralığı HH:MM biçiminde olmalıdır.") from exc
    if minute > 59 or hour > 23 or hour < 0:
        if allow_end_of_day and value == "24:00":
            return 24 * 60
        raise ValueError("Saat aralığı 00:00-24:00 arasında olmalıdır.")
    return hour * 60 + minute


def normalize_min_staffing_windows(value: Any) -> list[dict[str, Any]]:
    """Normalize section/time windows with a minimum concurrent headcount."""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("Saat aralıklı asgari kadro ayarı geçersiz.")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Asgari kadro aralığı geçersiz.")
        section = str(item.get("section") or "").strip()[:60]
        start, end = _minutes(item.get("start")), _minutes(item.get("end"), allow_end_of_day=True)
        try:
            minimum = int(item.get("minimum"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Saat aralığındaki asgari kadro tam sayı olmalıdır.") from exc
        if not section or not 1 <= minimum <= 99 or end <= start:
            raise ValueError("Bölüm, saat aralığı veya asgari kadro değeri geçersiz.")
        window = {"section": section, "start": f"{start // 60:02d}:{start % 60:02d}",
                  "end": "24:00" if end == 24 * 60 else f"{end // 60:02d}:{end % 60:02d}",
                  "minimum": minimum}
        for current in result:
            if current["section"] == section:
                current_start = _minutes(current["start"])
                current_end = _minutes(current["end"], allow_end_of_day=True)
                if max(start, current_start) < min(end, current_end):
                    raise ValueError(f"{section} için saat aralıkları birbiriyle çakışamaz.")
        result.append(window)
    return sorted(result, key=lambda window: (window["section"].casefold(), window["start"]))


def staffing_items(
    month: str,
    entries: list[dict[str, Any]],
    minimums: dict[str, int],
    windows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Bölüm başına günlük çalışan sayısı asgari kadronun altındaysa uyarı üretir."""
    if not minimums and not windows:
        return []
    counts: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in entries:
        section = str(row.get("section") or "")
        if section not in minimums:
            continue
        for day_text, item in row.get("days", {}).items():
            if item.get("kind") == "work" and day_text.startswith(month):
                counts[(section, day_text)].add(int(row["employee_id"]))
    items = []
    for day in scheduling.month_dates(month):
        key = day.isoformat()
        for section, minimum in minimums.items():
            count = len(counts.get((section, key), ()))
            if count < minimum:
                items.append(_item(
                    "min_staffing", "warning", None, section, day,
                    f"{section}: {day.strftime('%d.%m')} günü {count} çalışan; asgari kadro {minimum}",
                ))
    intervals: dict[str, list[tuple[int, datetime, datetime]]] = defaultdict(list)
    window_sections = {window["section"] for window in (windows or [])}
    for row in entries:
        section = str(row.get("section") or "")
        for day_text, item in row.get("days", {}).items():
            if section not in window_sections or item.get("kind") != "work":
                continue
            start, end = scheduling._time_interval(day_text, item["start"], item["end"])
            intervals[section].append((int(row["employee_id"]), start, end))
    for day in scheduling.month_dates(month):
        day_text = day.isoformat()
        for window in windows or []:
            start_minute = _minutes(window["start"])
            end_minute = _minutes(window["end"], allow_end_of_day=True)
            begin = datetime.combine(day, time.min) + timedelta(minutes=start_minute)
            finish = datetime.combine(day, time.min) + timedelta(minutes=end_minute)
            shifts = [
                (employee_id, shift_start, shift_end)
                for employee_id, shift_start, shift_end in intervals[window["section"]]
                if shift_start < finish and shift_end > begin
            ]
            boundaries = {begin, finish}
            for _, shift_start, shift_end in shifts:
                if shift_start < finish and shift_end > begin:
                    boundaries.add(max(begin, shift_start))
                    boundaries.add(min(finish, shift_end))
            ordered = sorted(boundaries)
            worst = None
            for segment_start, segment_end in zip(ordered, ordered[1:]):
                count = len({
                    employee_id for employee_id, shift_start, shift_end in shifts
                    if shift_start < segment_end and shift_end > segment_start
                })
                if worst is None or count < worst[0]:
                    worst = (count, segment_start, segment_end)
            if worst and worst[0] < window["minimum"]:
                count, gap_start, gap_end = worst
                items.append(_item(
                    "min_staffing", "warning", None, window["section"], day,
                    f"{window['section']}: {gap_start.strftime('%H:%M')}–{gap_end.strftime('%H:%M')} "
                    f"aralığında en az {window['minimum']} çalışan gerekir; en düşük sayı {count}",
                ))
    return items


def hourly_staffing(day: date, entries: list[dict[str, Any]], windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    midnight = datetime.combine(day, time.min)
    next_day = midnight + timedelta(days=1)
    dates = {day.isoformat(), (day - timedelta(days=1)).isoformat()}
    sections = {window["section"] for window in windows}
    intervals: dict[str, list[tuple[int, datetime, datetime]]] = defaultdict(list)
    for row in entries:
        if row["section"] not in sections:
            continue
        for date_text in row["days"].keys() & dates:
            shift = row["days"][date_text]
            if shift["kind"] == "work":
                start, end = scheduling._time_interval(date_text, shift["start"], shift["end"])
                if start < next_day and end > midnight:
                    intervals[row["section"]].append((row["employee_id"], start, end))
    result = []
    for window in windows:
        start = _minutes(window["start"])
        end = _minutes(window["end"], allow_end_of_day=True)
        while start < end:
            until = min(end, (start // 60 + 1) * 60)
            begin, finish = (midnight + timedelta(minutes=value) for value in (start, until))
            shifts = [(employee, max(shift_start, begin), min(shift_end, finish))
                      for employee, shift_start, shift_end in intervals[window["section"]]
                      if shift_start < finish and shift_end > begin]
            boundaries = sorted({begin, finish} | {edge for _, first, last in shifts for edge in (first, last)})
            counts = [(first, last, len({employee for employee, a, b in shifts if a < last and b > first}))
                      for first, last in zip(boundaries, boundaries[1:])]

            def label(value):
                return "24:00" if value == next_day else value.strftime("%H:%M")

            result.append({
                "section": window["section"], "start": label(begin), "end": label(finish),
                "required": window["minimum"], "lowest": min(count for _, _, count in counts),
                "gaps": [{"start": label(first), "end": label(last), "shortfall": window["minimum"] - count}
                         for first, last, count in counts if count < window["minimum"]],
            })
            start = until
    return result


def normalize_statuses(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("Özel durum listesi geçersiz.")
    return [key for key in SPECIAL_STATUSES if key in value]


def _intervals(entries) -> dict[int, list[tuple[datetime, datetime]]]:
    result: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    for row in entries:
        for day_text, item in row.get("days", {}).items():
            if item.get("kind") == "work" and item.get("start") and item.get("end"):
                result[int(row["employee_id"])].append(
                    scheduling._time_interval(day_text, item["start"], item["end"])
                )
    return {key: _merge(value) for key, value in result.items()}


def _merge(intervals):
    merged: list[list[datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def night_minutes(start: datetime, end: datetime) -> float:
    total = 0.0
    day = start.date() - timedelta(days=1)
    while day <= end.date():
        begin = max(start, datetime.combine(day, scheduling.NIGHT_START))
        finish = min(end, datetime.combine(day + timedelta(days=1), scheduling.NIGHT_END))
        if begin < finish:
            total += (finish - begin).total_seconds() / 60
        day += timedelta(days=1)
    return total


def _longest_gap(intervals, begin: datetime, end: datetime) -> float:
    cursor, best = begin, 0.0
    for start, finish in intervals:
        if finish <= begin or start >= end:
            continue
        best = max(best, (max(start, begin) - cursor).total_seconds() / 3600)
        cursor = max(cursor, min(finish, end))
    return max(best, (end - cursor).total_seconds() / 3600)


def _age(birth: str, on_day: date) -> int | None:
    try:
        born = date.fromisoformat(birth)
    except (TypeError, ValueError):
        return None
    return on_day.year - born.year - ((on_day.month, on_day.day) < (born.month, born.day))


def _item(kind, severity, employee_id, name, day, message):
    return {
        "type": kind, "severity": severity, "employee_id": employee_id, "name": name,
        "date": day.isoformat() if isinstance(day, date) else day, "message": message,
    }


def compliance_items(
    month: str,
    entries: list[dict[str, Any]],
    names: dict[int, str],
    profiles: dict[str, dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
    minimum_rest_hours: float = 0,
) -> list[dict[str, Any]]:
    """`entries` may include previous months; findings are reported only for `month`."""
    rules = normalize_settings(settings)
    dates = scheduling.month_dates(month)
    month_begin = datetime.combine(dates[0], time.min)
    month_end = datetime.combine(dates[-1] + timedelta(days=1), time.min)
    items: list[dict[str, Any]] = []
    for employee_id, intervals in _intervals(entries).items():
        if employee_id not in names:
            continue
        name = names[employee_id]
        in_month = [pair for pair in intervals if month_begin <= pair[0] < month_end]

        legal_rest = rules["legal_rest_hours"]
        if legal_rest and minimum_rest_hours < legal_rest:
            for (_, previous_end), (start, _) in zip(intervals, intervals[1:]):
                gap = (start - previous_end).total_seconds() / 3600
                if month_begin <= start < month_end and 0 < gap < legal_rest:
                    items.append(_item(
                        "legal_rest", "warning", employee_id, name, start.date(),
                        f"{previous_end.strftime('%d.%m %H:%M')} çıkış → {start.strftime('%d.%m %H:%M')} giriş: "
                        f"{gap:g} saat dinlenme; yasal en az {legal_rest:g} saat",
                    ))

        if in_month:
            cursor = dates[0] - timedelta(days=dates[0].weekday())
            while cursor <= dates[-1]:
                begin = datetime.combine(cursor, time.min)
                end = begin + timedelta(days=7)
                gap = _longest_gap(intervals, begin, end)
                if gap < WEEKLY_REST_HOURS:
                    items.append(_item(
                        "legal_weekly_rest", "warning", employee_id, name, max(cursor, dates[0]),
                        f"{cursor.strftime('%d.%m')} haftasında en uzun kesintisiz dinlenme {gap:g} saat; "
                        f"hafta tatili en az {WEEKLY_REST_HOURS} saat olmalı (md. 46)",
                    ))
                cursor += timedelta(days=7)

        limit = rules["max_night_weeks"]
        if limit:
            night_weeks: set[date] = set()
            for start, end in intervals:
                if night_minutes(start, end) >= NIGHT_SHIFT_MINUTES:
                    night_weeks.add(start.date() - timedelta(days=start.date().weekday()))
            run: list[date] = []
            for week in sorted(night_weeks):
                run = run + [week] if run and week - run[-1] == timedelta(days=7) else [week]
                if len(run) > limit and dates[0] - timedelta(days=6) <= week <= dates[-1]:
                    items.append(_item(
                        "night_rotation", "warning", employee_id, name, max(week, dates[0]),
                        f"{run[0].strftime('%d.%m')} haftasından beri {len(run)} hafta üst üste gece vardiyası; "
                        f"sınır {limit} hafta, gündüz postasına geçirilmeli",
                    ))

        weeks = rules["balancing_weeks"]
        if weeks and in_month:
            last_week = dates[-1] - timedelta(days=dates[-1].weekday())
            first_week = last_week - timedelta(days=7 * (weeks - 1))
            begin = datetime.combine(first_week, time.min)
            end = datetime.combine(last_week + timedelta(days=7), time.min)
            minutes = scheduling._union_minutes(intervals, begin, end)
            average = minutes / 60 / weeks
            if average > WEEKLY_AVERAGE_LIMIT:
                items.append(_item(
                    "balancing", "warning", employee_id, name, None,
                    f"{first_week.strftime('%d.%m')}–{(last_week + timedelta(days=6)).strftime('%d.%m')} "
                    f"denkleştirme döneminde ({weeks} hafta) haftalık ortalama {average:.1f} saat; "
                    f"sınır {WEEKLY_AVERAGE_LIMIT} saat (md. 63)",
                ))

        profile = (profiles or {}).get(str(employee_id), {})
        statuses = set(normalize_statuses(profile.get("special_status", [])))
        age = _age(profile.get("birth_date", ""), dates[0])
        if age is not None and age < 18:
            statuses.add("under18")
        if statuses and in_month:
            labels = ", ".join(SPECIAL_STATUSES[key] for key in SPECIAL_STATUSES if key in statuses)
            restricted_night = statuses & {"under18", "pregnant", "nursing"}
            for start, end in in_month:
                if night_minutes(start, end) > 0 and restricted_night:
                    items.append(_item(
                        "special_night", "error" if "under18" in statuses else "warning",
                        employee_id, name, start.date(),
                        f"{labels}: {start.strftime('%d.%m %H:%M')} vardiyası gece saatlerine (20:00-06:00) giriyor"
                        + ("" if "under18" in statuses else "; sağlık raporu yoksa gece çalıştırılamaz"),
                    ))
                elif night_minutes(start, end) > 0 and "disabled" in statuses:
                    items.append(_item(
                        "special_night", "info", employee_id, name, start.date(),
                        f"{labels}: gece vardiyası için hekim görüşünü kontrol edin",
                    ))
            if statuses & {"under18", "pregnant", "nursing"}:
                cursor = dates[0] - timedelta(days=dates[0].weekday())
                while cursor <= dates[-1]:
                    begin = datetime.combine(cursor, time.min)
                    hours = scheduling._union_minutes(intervals, begin, begin + timedelta(days=7)) / 60
                    weekly_limit = 40 if "under18" in statuses else WEEKLY_AVERAGE_LIMIT
                    if hours > weekly_limit:
                        items.append(_item(
                            "special_overtime", "error", employee_id, name, max(cursor, dates[0]),
                            f"{labels}: {cursor.strftime('%d.%m')} haftası {hours:g} saat; "
                            f"fazla çalışma yaptırılamaz (sınır {weekly_limit} saat)",
                        ))
                    cursor += timedelta(days=7)
            for start, end in in_month:
                if "under18" in statuses and (end - start).total_seconds() / 3600 > 8:
                    items.append(_item(
                        "special_daily", "error", employee_id, name, start.date(),
                        f"{labels}: günlük {(end - start).total_seconds() / 3600:g} saat; en fazla 8 saat",
                    ))
    return items
