"""
사용자 예매 환불 (극장 / 콘서트)
"""
from typing import Any, Dict, Optional

import pymysql
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from cache.redis_client import redis_client
from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER

router = APIRouter()


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_tx_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _refund_movie_booking(user_id: int, booking_id: int):
    """극장(영화) 예매 환불: booking→CANCEL, payment→N, 좌석 반환, 캐시 무효화."""
    from theater.theaters_read import THEATERS_BOOTSTRAP_CACHE_KEY, refresh_theaters_bootstrap_cache

    conn = _get_tx_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT booking_id, user_id, schedule_id, reg_count, book_status "
                "FROM booking WHERE booking_id = %s AND user_id = %s FOR UPDATE",
                (booking_id, user_id),
            )
            booking = cur.fetchone()
            if not booking:
                conn.rollback()
                return JSONResponse(
                    status_code=404,
                    content={"ok": False, "message": "예매 내역을 찾을 수 없습니다."},
                )

            if (booking.get("book_status") or "").upper() in ("CANCEL", "CANCELED", "CANCELLED"):
                conn.rollback()
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "message": "이미 취소된 예매입니다."},
                )

            schedule_id = _to_int(booking.get("schedule_id"))
            reg_count = _to_int(booking.get("reg_count"))

            cur.execute(
                "UPDATE booking SET book_status = 'CANCEL' WHERE booking_id = %s",
                (booking_id,),
            )

            cur.execute(
                "UPDATE payment SET pay_yn = 'N' WHERE booking_id = %s",
                (booking_id,),
            )

            cur.execute(
                "DELETE FROM booking_seats WHERE booking_id = %s",
                (booking_id,),
            )

            if schedule_id > 0 and reg_count > 0:
                cur.execute(
                    "UPDATE schedules SET remain_count = remain_count + %s WHERE schedule_id = %s",
                    (reg_count, schedule_id),
                )
                cur.execute(
                    "UPDATE schedules SET status = 'OPEN' "
                    "WHERE schedule_id = %s AND UPPER(COALESCE(status, '')) = 'CLOSED'",
                    (schedule_id,),
                )

        conn.commit()
    except Exception as exc:
        conn.rollback()
        return JSONResponse(
            status_code=500,
            content={"ok": False, "message": str(exc)},
        )
    finally:
        conn.close()

    try:
        redis_client.delete(THEATERS_BOOTSTRAP_CACHE_KEY)
        refresh_theaters_bootstrap_cache()
    except Exception:
        pass

    return {"ok": True, "message": "환불이 완료되었습니다."}


def _refund_concert_booking(user_id: int, booking_id: int):
    """콘서트 예매 환불: concert_booking→CANCEL, concert_payment→N, 좌석 반환."""
    conn = _get_tx_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT booking_id, user_id, show_id, reg_count, book_status "
                "FROM concert_booking WHERE booking_id = %s AND user_id = %s FOR UPDATE",
                (booking_id, user_id),
            )
            booking = cur.fetchone()
            if not booking:
                conn.rollback()
                return JSONResponse(
                    status_code=404,
                    content={"ok": False, "message": "예매 내역을 찾을 수 없습니다."},
                )

            if (booking.get("book_status") or "").upper() in ("CANCEL", "CANCELED", "CANCELLED"):
                conn.rollback()
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "message": "이미 취소된 예매입니다."},
                )

            show_id = _to_int(booking.get("show_id"))
            reg_count = _to_int(booking.get("reg_count"))

            cur.execute(
                "UPDATE concert_booking SET book_status = 'CANCEL' WHERE booking_id = %s",
                (booking_id,),
            )

            cur.execute(
                "UPDATE concert_payment SET pay_yn = 'N' WHERE booking_id = %s",
                (booking_id,),
            )

            cur.execute(
                "DELETE FROM concert_booking_seats WHERE booking_id = %s",
                (booking_id,),
            )

            if show_id > 0 and reg_count > 0:
                cur.execute(
                    "UPDATE concert_shows SET remain_count = remain_count + %s WHERE show_id = %s",
                    (reg_count, show_id),
                )
                cur.execute(
                    "UPDATE concert_shows SET status = 'OPEN' "
                    "WHERE show_id = %s AND UPPER(COALESCE(status, '')) = 'CLOSED'",
                    (show_id,),
                )

        conn.commit()
    except Exception as exc:
        conn.rollback()
        return JSONResponse(
            status_code=500,
            content={"ok": False, "message": str(exc)},
        )
    finally:
        conn.close()

    return {"ok": True, "message": "환불이 완료되었습니다."}


@router.post("/api/write/user/bookings/refund")
def refund_booking(payload: Optional[Dict[str, Any]] = Body(default=None)):
    data = payload or {}
    user_id = _to_int(data.get("user_id"))
    booking_id = _to_int(data.get("booking_id"))
    booking_kind = (data.get("booking_kind") or "movie").strip().lower()

    if user_id <= 0 or booking_id <= 0:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "요청값이 올바르지 않습니다."},
        )

    if booking_kind == "concert":
        return _refund_concert_booking(user_id, booking_id)
    return _refund_movie_booking(user_id, booking_id)
