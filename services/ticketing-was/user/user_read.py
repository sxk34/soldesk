from math import ceil
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from db import get_db_connection

router = APIRouter()

EXCLUDED_BOOKING_STATUSES_SQL = ("CANCEL", "CANCELED", "CANCELLED")


def _derive_region_name(address):
    text = str(address or "").strip()
    if not text:
        return "서울"
    if text.startswith("서울"):
        return "서울"
    if text.startswith("경기") or text.startswith("인천"):
        return "경기/인천"
    first = text.split()[0]
    if first in {"서울", "서울특별시"}:
        return "서울"
    if first in {"경기", "경기도", "인천", "인천광역시"}:
        return "경기/인천"
    return first


@router.get("/api/read/user/mypage")
def get_mypage(user_id: Optional[str] = Query(default=None)):
    if not user_id:
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, phone, name, created_at FROM users WHERE user_id = %s", (user_id_int,))
            user = cur.fetchone()
        if not user:
            return JSONResponse(status_code=404, content={"message": "user not found"})
        return user
    finally:
        conn.close()


@router.post("/api/read/user/check-phone")
def check_phone_duplicate(payload: Optional[Dict[str, Any]] = Body(default=None)):
    data = payload or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS count FROM users WHERE phone = %s", (phone,))
            row = cur.fetchone()
        count = int(row["count"] or 0)
        return {"message": "ok", "duplicated": count > 0, "count": count}
    finally:
        conn.close()


@router.post("/api/read/user/find-password")
def find_password_user(payload: Optional[Dict[str, Any]] = Body(default=None)):
    data = payload or {}
    phone = (data.get("phone") or "").strip()
    name = (data.get("name") or "").strip()
    if not phone or not name:
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, phone, name, created_at FROM users WHERE phone = %s", (phone,))
            user_by_phone = cur.fetchone()
            cur.execute("SELECT user_id, phone, name, created_at FROM users WHERE name = %s", (name,))
            user_by_name = cur.fetchone()
            cur.execute("SELECT user_id, phone, name, created_at FROM users WHERE phone = %s AND name = %s", (phone, name))
            matched_user = cur.fetchone()
        if matched_user:
            return {
                "message": "found", "success": True, "matched_phone": True, "matched_name": True,
                "user": {"user_id": matched_user["user_id"], "phone": matched_user["phone"], "name": matched_user["name"], "created_at": matched_user["created_at"]},
            }
        return {"message": "not matched", "success": False, "matched_phone": user_by_phone is not None, "matched_name": user_by_name is not None}
    finally:
        conn.close()


@router.get("/api/read/user/bookings/recent")
def get_recent_bookings(user_id: Optional[str] = Query(default=None)):
    if not user_id:
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM (
                    SELECT 'movie' AS booking_kind, b.booking_id, b.created_at AS booking_date,
                        m.title AS movie_title, t.address AS theater_address
                    FROM booking b
                    JOIN schedules s ON s.schedule_id = b.schedule_id
                    JOIN movies m ON m.movie_id = s.movie_id
                    JOIN halls h ON h.hall_id = s.hall_id
                    JOIN theaters t ON t.theater_id = h.theater_id
                    WHERE b.user_id = %s AND UPPER(COALESCE(b.book_status, '')) NOT IN %s
                    UNION ALL
                    SELECT 'concert' AS booking_kind, cb.booking_id, cb.created_at AS booking_date,
                        c.title AS movie_title, cs.venue_address AS theater_address
                    FROM concert_booking cb
                    JOIN concert_shows cs ON cs.show_id = cb.show_id
                    JOIN concerts c ON c.concert_id = cs.concert_id
                    WHERE cb.user_id = %s AND UPPER(COALESCE(cb.book_status, '')) NOT IN %s
                ) AS u ORDER BY u.booking_date DESC LIMIT 5
            """, (user_id_int, EXCLUDED_BOOKING_STATUSES_SQL, user_id_int, EXCLUDED_BOOKING_STATUSES_SQL))
            rows = cur.fetchall()
        bookings = []
        for row in rows:
            address = str(row.get("theater_address") or "").strip()
            bookings.append({
                "booking_kind": row.get("booking_kind") or "movie",
                "booking_id": row["booking_id"],
                "booking_date": row["booking_date"],
                "movie_title": row["movie_title"],
                "region_name": _derive_region_name(address),
            })
        return {"bookings": bookings}
    finally:
        conn.close()


@router.get("/api/read/user/bookings")
def get_bookings(
    user_id: Optional[str] = Query(default=None),
    page: Optional[str] = Query(default="1"),
    page_size: Optional[str] = Query(default="10"),
):
    if not user_id:
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    try:
        page_int = max(1, int(page))
    except (TypeError, ValueError):
        page_int = 1
    try:
        page_size_int = min(50, max(1, int(page_size)))
    except (TypeError, ValueError):
        page_size_int = 10
    offset = (page_int - 1) * page_size_int
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT (SELECT COUNT(*) FROM booking WHERE user_id = %s)
                    + (SELECT COUNT(*) FROM concert_booking WHERE user_id = %s) AS total_count
            """, (user_id_int, user_id_int))
            total_count = int(cur.fetchone()["total_count"] or 0)

            cur.execute("""
                SELECT * FROM (
                    SELECT 'movie' AS booking_kind, b.booking_id, b.booking_code, b.reg_count, b.book_status,
                        b.created_at AS booking_date, s.schedule_id, s.show_date,
                        m.title AS movie_title, h.hall_name, t.address AS theater_address, p.pay_yn, p.paid_at
                    FROM booking b
                    JOIN schedules s ON s.schedule_id = b.schedule_id
                    JOIN movies m ON m.movie_id = s.movie_id
                    JOIN halls h ON h.hall_id = s.hall_id
                    JOIN theaters t ON t.theater_id = h.theater_id
                    LEFT JOIN payment p ON p.booking_id = b.booking_id
                    WHERE b.user_id = %s
                    UNION ALL
                    SELECT 'concert' AS booking_kind, cb.booking_id, cb.booking_code, cb.reg_count, cb.book_status,
                        cb.created_at AS booking_date, NULL AS schedule_id, cs.show_date,
                        c.title AS movie_title, cs.hall_name, cs.venue_address AS theater_address, cp.pay_yn, cp.paid_at
                    FROM concert_booking cb
                    JOIN concert_shows cs ON cs.show_id = cb.show_id
                    JOIN concerts c ON c.concert_id = cs.concert_id
                    LEFT JOIN concert_payment cp ON cp.booking_id = cb.booking_id
                    WHERE cb.user_id = %s
                ) AS u ORDER BY u.booking_date DESC LIMIT %s OFFSET %s
            """, (user_id_int, user_id_int, page_size_int, offset))
            booking_rows = cur.fetchall()

            movie_ids = [r["booking_id"] for r in booking_rows if (r.get("booking_kind") or "movie") == "movie"]
            concert_ids = [r["booking_id"] for r in booking_rows if (r.get("booking_kind") or "movie") == "concert"]
            seats_map: Dict[str, List[str]] = {}

            if movie_ids:
                ph = ",".join(["%s"] * len(movie_ids))
                cur.execute(f"""
                    SELECT bs.booking_id, hs.seat_row_no, hs.seat_col_no
                    FROM booking_seats bs JOIN hall_seats hs ON hs.seat_id = bs.seat_id
                    WHERE bs.booking_id IN ({ph}) ORDER BY bs.booking_id, hs.seat_row_no, hs.seat_col_no
                """, tuple(movie_ids))
                for sr in cur.fetchall():
                    k = f"movie:{sr['booking_id']}"
                    seats_map.setdefault(k, []).append(f"{sr['seat_row_no']}-{sr['seat_col_no']}")

            if concert_ids:
                ph = ",".join(["%s"] * len(concert_ids))
                cur.execute(f"""
                    SELECT booking_id, seat_row_no, seat_col_no FROM concert_booking_seats
                    WHERE booking_id IN ({ph}) ORDER BY booking_id, seat_row_no, seat_col_no
                """, tuple(concert_ids))
                for sr in cur.fetchall():
                    k = f"concert:{sr['booking_id']}"
                    seats_map.setdefault(k, []).append(f"{sr['seat_row_no']}-{sr['seat_col_no']}")

        total_pages = ceil(total_count / page_size_int) if total_count > 0 else 1
        bookings = []
        for row in booking_rows:
            address = str(row.get("theater_address") or "").strip()
            kind = row.get("booking_kind") or "movie"
            bid = row["booking_id"]
            bookings.append({
                "booking_kind": kind, "booking_id": bid, "booking_code": row["booking_code"],
                "reg_count": row["reg_count"], "book_status": row["book_status"],
                "booking_date": row["booking_date"], "show_date": row["show_date"],
                "movie_title": row["movie_title"], "hall_name": row["hall_name"],
                "theater_address": address, "region_name": _derive_region_name(address),
                "pay_yn": row.get("pay_yn"), "paid_at": row.get("paid_at"),
                "seats": seats_map.get(f"{kind}:{bid}", []),
            })
        return {"bookings": bookings, "total_count": total_count, "page": page_int, "page_size": page_size_int, "total_pages": total_pages}
    finally:
        conn.close()
