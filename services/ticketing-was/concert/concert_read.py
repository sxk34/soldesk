from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db import get_db_connection

router = APIRouter()


def _serialize_dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def _fetch_concerts_from_db() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.concert_id, c.title, c.category, c.genre, c.venue_summary,
                    c.poster_url, c.runtime_minutes, c.synopsis, c.synopsis_line,
                    c.status, c.hide, c.created_at, c.updated_at, s.next_show_date
                FROM concerts c
                LEFT JOIN (
                    SELECT concert_id, MIN(show_date) AS next_show_date
                    FROM concert_shows GROUP BY concert_id
                ) s ON s.concert_id = c.concert_id
                ORDER BY concert_id ASC
            """)
            rows = cur.fetchall() or []
        out = []
        for r in rows:
            out.append({
                "concert_id": int(r["concert_id"]), "title": r.get("title"),
                "category": r.get("category"), "genre": r.get("genre"),
                "venue_summary": r.get("venue_summary"), "poster_url": r.get("poster_url"),
                "runtime_minutes": int(r.get("runtime_minutes") or 0),
                "synopsis": r.get("synopsis"), "synopsis_line": r.get("synopsis_line"),
                "status": r.get("status"), "hide": r.get("hide"),
                "next_show_date": _serialize_dt(r.get("next_show_date")),
                "created_at": _serialize_dt(r.get("created_at")),
                "updated_at": _serialize_dt(r.get("updated_at")),
            })
        return out
    finally:
        conn.close()


def _fetch_concert_row(concert_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT concert_id, title, category, genre, venue_summary, poster_url,
                    runtime_minutes, synopsis, synopsis_line, status, hide, created_at, updated_at
                FROM concerts WHERE concert_id = %s
            """, (concert_id,))
            r = cur.fetchone()
        if not r:
            return None
        return {
            "concert_id": int(r["concert_id"]), "title": r.get("title"),
            "category": r.get("category"), "genre": r.get("genre"),
            "venue_summary": r.get("venue_summary"), "poster_url": r.get("poster_url"),
            "runtime_minutes": int(r.get("runtime_minutes") or 0),
            "synopsis": r.get("synopsis"), "synopsis_line": r.get("synopsis_line"),
            "status": r.get("status"), "hide": r.get("hide"),
            "release_date": None, "release_date_display": None,
            "created_at": _serialize_dt(r.get("created_at")),
            "updated_at": _serialize_dt(r.get("updated_at")),
        }
    finally:
        conn.close()


def _fetch_reserved_seat_keys_by_show(show_ids: List[int]) -> Dict[str, List[str]]:
    if not show_ids:
        return {}
    conn = get_db_connection()
    try:
        placeholders = ",".join(["%s"] * len(show_ids))
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT show_id, seat_row_no, seat_col_no FROM concert_booking_seats "
                f"WHERE show_id IN ({placeholders}) AND UPPER(COALESCE(status, '')) = 'ACTIVE' "
                f"ORDER BY show_id, seat_row_no, seat_col_no",
                tuple(show_ids),
            )
            rows = cur.fetchall() or []
        result: Dict[str, List[str]] = {}
        for r in rows:
            sid = str(int(r["show_id"]))
            key = f"{int(r['seat_row_no'])}-{int(r['seat_col_no'])}"
            result.setdefault(sid, []).append(key)
        return result
    finally:
        conn.close()


@router.get("/api/read/concerts")
def list_concerts():
    return _fetch_concerts_from_db()


@router.get("/api/read/concert/{concert_id}")
def get_concert_detail(concert_id: int):
    concert = _fetch_concert_row(concert_id)
    if not concert:
        return JSONResponse(status_code=404, content={"message": "not found"})
    concert["release_date_display"] = concert.get("venue_summary") or ""
    return {"concert": concert, "reviews": []}


@router.get("/api/read/concert/{concert_id}/booking-bootstrap")
def get_concert_booking_bootstrap(concert_id: int):
    concert = _fetch_concert_row(concert_id)
    if not concert:
        return JSONResponse(status_code=404, content={"message": "not found"})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT show_id, concert_id, show_date, venue_name, venue_address,
                    hall_name, seat_rows, seat_cols, total_count, remain_count, price, status
                FROM concert_shows WHERE concert_id = %s ORDER BY show_date ASC
            """, (concert_id,))
            show_rows = cur.fetchall() or []
    finally:
        conn.close()
    show_ids = [int(r["show_id"]) for r in show_rows]
    reserved_map = _fetch_reserved_seat_keys_by_show(show_ids)
    shows = []
    for r in show_rows:
        sid = int(r["show_id"])
        shows.append({
            "show_id": sid, "concert_id": int(r["concert_id"]),
            "show_date": _serialize_dt(r.get("show_date")),
            "venue_name": r.get("venue_name"), "venue_address": r.get("venue_address"),
            "hall_name": r.get("hall_name"),
            "seat_rows": int(r.get("seat_rows") or 0),
            "seat_cols": int(r.get("seat_cols") or 0),
            "total_count": int(r.get("total_count") or 0),
            "remain_count": int(r.get("remain_count") or 0),
            "price": int(r.get("price") or 0), "status": r.get("status"),
            "reserved_seats": reserved_map.get(str(sid), []),
        })
    return {"concert": concert, "shows": shows}
