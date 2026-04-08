import json
from datetime import date, datetime, timedelta

from cache.redis_client import redis_client
from db import get_db_connection

THEATERS_LIST_CACHE_KEY = "theaters:list:v1"
THEATER_DETAIL_CACHE_KEY_FORMAT = "theater:detail:{theater_id}:v1"

_WEEKDAY_MAP = ["월", "화", "수", "목", "금", "토", "일"]


def _get_theater_detail_cache_key(theater_id):
    return THEATER_DETAIL_CACHE_KEY_FORMAT.format(theater_id=theater_id)


def _write_theaters_cache(rows):
    redis_client.set(THEATERS_LIST_CACHE_KEY, json.dumps(rows, default=str, ensure_ascii=False))


def _write_theater_detail_cache(theater_id, result):
    redis_client.set(_get_theater_detail_cache_key(theater_id), json.dumps(result, default=str, ensure_ascii=False))


def _to_datetime_value(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _format_weekday(value):
    parsed = _to_datetime_value(value)
    return _WEEKDAY_MAP[parsed.weekday()] if parsed else ""


def _format_date_text(value):
    parsed = _to_datetime_value(value)
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def _format_month(value):
    parsed = _to_datetime_value(value)
    return parsed.month if parsed else 0


def _format_day(value):
    parsed = _to_datetime_value(value)
    return parsed.day if parsed else 0


def _format_time(value):
    parsed = _to_datetime_value(value)
    return parsed.strftime("%H:%M") if parsed else ""


def _make_end_time(show_date, runtime_minutes):
    parsed = _to_datetime_value(show_date)
    if not parsed:
        return ""
    try:
        runtime = int(runtime_minutes or 0)
    except (TypeError, ValueError):
        runtime = 0
    return (parsed + timedelta(minutes=runtime)).strftime("%H:%M")


def _fetch_theaters_from_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.theater_id, t.address, h.hall_id, h.hall_name, h.total_seats
                FROM theaters t LEFT JOIN halls h ON t.theater_id = h.theater_id
                ORDER BY t.theater_id ASC, h.hall_id ASC
            """)
            rows = cur.fetchall()
        theaters_map = {}
        for row in rows:
            tid = row["theater_id"]
            if tid not in theaters_map:
                theaters_map[tid] = {"theater_id": tid, "address": row["address"], "halls": []}
            if row.get("hall_id") is not None:
                theaters_map[tid]["halls"].append({"hall_id": row["hall_id"], "hall_name": row["hall_name"], "total_seats": row["total_seats"]})
        return list(theaters_map.values())
    finally:
        conn.close()


def _fetch_theater_detail_from_db(theater_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.theater_id, t.address, h.hall_id, h.hall_name, h.total_seats
                FROM theaters t LEFT JOIN halls h ON t.theater_id = h.theater_id
                WHERE t.theater_id = %s ORDER BY h.hall_id ASC
            """, (theater_id,))
            theater_rows = cur.fetchall()
            if not theater_rows:
                return None
            cur.execute("""
                SELECT s.schedule_id, s.movie_id, s.hall_id, s.show_date, s.total_count, s.status,
                    m.title, m.runtime_minutes, h.hall_name, h.total_seats, t.theater_id, t.address
                FROM schedules s
                INNER JOIN movies m ON s.movie_id = m.movie_id
                INNER JOIN halls h ON s.hall_id = h.hall_id
                INNER JOIN theaters t ON h.theater_id = t.theater_id
                WHERE t.theater_id = %s AND s.status = 'OPEN' AND m.status = 'ACTIVE' AND m.hide = 'N'
                ORDER BY m.movie_id ASC, s.show_date ASC, h.hall_id ASC, s.schedule_id ASC
            """, (theater_id,))
            schedule_rows = cur.fetchall()
        theater = {"theater_id": theater_rows[0]["theater_id"], "address": theater_rows[0]["address"], "halls": []}
        for row in theater_rows:
            if row.get("hall_id") is not None:
                theater["halls"].append({"hall_id": row["hall_id"], "hall_name": row["hall_name"], "total_seats": row["total_seats"]})
        movies_map = {}
        for row in schedule_rows:
            mid = row["movie_id"]
            sdt = _format_date_text(row["show_date"])
            if mid not in movies_map:
                movies_map[mid] = {"movie_id": mid, "title": row["title"], "runtime_minutes": row["runtime_minutes"], "dates": []}
            entry = movies_map[mid]
            date_entry = next((d for d in entry["dates"] if d["show_date"] == sdt), None)
            if date_entry is None:
                date_entry = {"show_date": sdt, "month": _format_month(row["show_date"]), "day": _format_day(row["show_date"]), "week_day": _format_weekday(row["show_date"]), "schedules": []}
                entry["dates"].append(date_entry)
            date_entry["schedules"].append({
                "schedule_id": row["schedule_id"], "hall_id": row["hall_id"], "hall_name": row["hall_name"],
                "show_date": row["show_date"], "start_time": _format_time(row["show_date"]),
                "end_time": _make_end_time(row["show_date"], row["runtime_minutes"]),
                "runtime_minutes": row["runtime_minutes"], "total_count": row["total_count"] or row["total_seats"],
            })
        return {"theater": theater, "movies": list(movies_map.values())}
    finally:
        conn.close()


def refresh_theaters_cache():
    rows = _fetch_theaters_from_db()
    _write_theaters_cache(rows)
    return {"cache_key": THEATERS_LIST_CACHE_KEY, "count": len(rows)}


def refresh_theater_detail_cache(theater_id):
    result = _fetch_theater_detail_from_db(theater_id)
    if result is None:
        redis_client.delete(_get_theater_detail_cache_key(theater_id))
        return {"theater_id": theater_id, "cached": False}
    _write_theater_detail_cache(theater_id, result)
    return {"theater_id": theater_id, "cached": True}


def rebuild_theaters_cache():
    list_result = refresh_theaters_cache()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT theater_id FROM theaters ORDER BY theater_id ASC")
            theater_rows = cur.fetchall()
    finally:
        conn.close()
    detail_cached_count = 0
    detail_deleted_count = 0
    for row in theater_rows:
        result = refresh_theater_detail_cache(row["theater_id"])
        if result["cached"]:
            detail_cached_count += 1
        else:
            detail_deleted_count += 1
    from theater.theaters_read import refresh_theaters_bootstrap_cache
    booking_bootstrap_result = refresh_theaters_bootstrap_cache()
    return {
        "name": "theaters_read", "list": list_result,
        "detail_cached_count": detail_cached_count, "detail_deleted_count": detail_deleted_count,
        "booking_bootstrap": booking_bootstrap_result,
    }
