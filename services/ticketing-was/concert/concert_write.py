"""
콘서트 예매 쓰기 — SQS FIFO 통합 버전.
원본: _ShowLockPool(threading.Lock) → SQS FIFO MessageGroupId=show_id
"""
import json
import secrets
import string
import threading
from contextlib import contextmanager

import pymysql
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, SQS_QUEUE_URL
from sqs_client import send_booking_message, get_booking_result

router = APIRouter()


class _ShowLockPool:
    """Local-only fallback. SQS_QUEUE_URL이 비어있을 때만 사용."""

    def __init__(self):
        self._global_lock = threading.Lock()
        self._locks = {}

    @contextmanager
    def acquire(self, show_id: int):
        with self._global_lock:
            lock = self._locks.get(show_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[show_id] = lock
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


_show_locks = _ShowLockPool()


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_seat_key(value: str):
    text = str(value or "").strip()
    parts = text.split("-")
    if len(parts) != 2:
        return None
    row = _to_int(parts[0])
    col = _to_int(parts[1])
    if row <= 0 or col <= 0:
        return None
    return row, col


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


def _is_duplicate_key_error(exc: Exception) -> bool:
    if not isinstance(exc, pymysql.err.IntegrityError):
        return False
    try:
        return int(exc.args[0]) == 1062
    except Exception:
        return False


def _generate_booking_code() -> str:
    letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(2))
    digits = "".join(secrets.choice(string.digits) for _ in range(6))
    return f"C{letters}{digits}"


@router.post("/api/write/concerts/booking/commit")
def commit_concert_booking(payload: dict):
    data = payload if isinstance(payload, dict) else {}
    user_id = _to_int(data.get("user_id"))
    show_id = _to_int(data.get("show_id"))
    seats = data.get("seats") or []

    if user_id <= 0 or show_id <= 0:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "BAD_REQUEST", "message": "요청값이 올바르지 않습니다."},
        )

    if not isinstance(seats, list) or not seats:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "NO_SEATS", "message": "좌석을 선택해주세요."},
        )

    parsed_seats = []
    seat_set = set()
    for item in seats:
        parsed = _parse_seat_key(item)
        if not parsed:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "code": "BAD_SEAT_KEY", "message": "좌석 형식이 올바르지 않습니다."},
            )
        if parsed in seat_set:
            continue
        seat_set.add(parsed)
        parsed_seats.append(parsed)

    req_count = len(parsed_seats)
    if req_count <= 0:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "NO_SEATS", "message": "좌석을 선택해주세요."},
        )

    # ── SQS 모드 ─────────────────────────────────────────────────────────────
    if SQS_QUEUE_URL:
        booking_ref = send_booking_message(
            booking_type="concert",
            group_id=str(show_id),
            payload={
                "user_id": user_id,
                "show_id": show_id,
                "seats": [f"{r}-{c}" for r, c in parsed_seats],
            },
        )
        return {
            "ok": True,
            "code": "QUEUED",
            "booking_ref": booking_ref,
            "message": "예매 요청이 접수되었습니다.",
        }

    # ── 로컬 폴백 ────────────────────────────────────────────────────────────
    return _commit_concert_booking_sync(user_id, show_id, parsed_seats, req_count)


def _commit_concert_booking_sync(user_id, show_id, parsed_seats, req_count):
    """원본 동기 콘서트 예매 로직 (worker-svc에서도 재사용)."""
    booking_id = 0
    payment_id = 0
    booking_code = ""
    remain_count_after = 0

    with _show_locks.acquire(show_id):
        conn = _get_tx_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT show_id, concert_id, seat_rows, seat_cols, "
                    "total_count, remain_count, status "
                    "FROM concert_shows WHERE show_id = %s FOR UPDATE",
                    (show_id,),
                )
                show = cur.fetchone()
                if not show:
                    conn.rollback()
                    return JSONResponse(
                        status_code=404,
                        content={"ok": False, "code": "NOT_FOUND", "message": "회차를 찾을 수 없습니다."},
                    )

                seat_rows = _to_int(show.get("seat_rows"))
                seat_cols = _to_int(show.get("seat_cols"))
                if seat_rows <= 0 or seat_cols <= 0:
                    conn.rollback()
                    return JSONResponse(status_code=500, content={"ok": False, "code": "ERROR"})

                for row_no, col_no in parsed_seats:
                    if row_no > seat_rows or col_no > seat_cols:
                        conn.rollback()
                        return JSONResponse(
                            status_code=400,
                            content={"ok": False, "code": "INVALID_SEAT", "message": "유효하지 않은 좌석입니다."},
                        )

                cur.execute(
                    "UPDATE concert_shows SET remain_count = remain_count - %s "
                    "WHERE show_id = %s AND remain_count >= %s "
                    "AND UPPER(COALESCE(status, '')) = 'OPEN'",
                    (req_count, show_id, req_count),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return {"ok": False, "code": "SOLD_OUT"}

                cur.execute(
                    "INSERT INTO concert_booking (user_id, show_id, reg_count, book_status, created_at) "
                    "VALUES (%s, %s, %s, 'PAID', NOW())",
                    (user_id, show_id, req_count),
                )
                booking_id = cur.lastrowid

                for _ in range(12):
                    code = _generate_booking_code()
                    try:
                        cur.execute(
                            "UPDATE concert_booking SET booking_code = %s WHERE booking_id = %s",
                            (code, booking_id),
                        )
                        booking_code = code
                        break
                    except Exception as exc:
                        if _is_duplicate_key_error(exc):
                            continue
                        raise
                if not booking_code:
                    raise RuntimeError("booking_code generation failed")

                for row_no, col_no in parsed_seats:
                    cur.execute(
                        "INSERT INTO concert_booking_seats "
                        "(booking_id, show_id, seat_row_no, seat_col_no, created_at) "
                        "VALUES (%s, %s, %s, %s, NOW())",
                        (booking_id, show_id, row_no, col_no),
                    )

                cur.execute(
                    "INSERT INTO concert_payment (booking_id, pay_yn, paid_at, created_at) "
                    "VALUES (%s, 'Y', NOW(), NOW())",
                    (booking_id,),
                )
                payment_id = cur.lastrowid

                cur.execute(
                    "SELECT remain_count FROM concert_shows WHERE show_id = %s",
                    (show_id,),
                )
                remain_row = cur.fetchone()
                remain_count_after = _to_int(remain_row.get("remain_count") if remain_row else 0)

                if remain_count_after <= 0:
                    cur.execute(
                        "UPDATE concert_shows SET status = 'CLOSED' WHERE show_id = %s",
                        (show_id,),
                    )

            conn.commit()

        except Exception as exc:
            conn.rollback()
            if _is_duplicate_key_error(exc):
                return {"ok": False, "code": "DUPLICATE_SEAT"}
            return JSONResponse(
                status_code=500,
                content={"ok": False, "code": "ERROR", "message": str(exc)},
            )
        finally:
            conn.close()

    return {
        "ok": True,
        "code": "OK",
        "booking_id": booking_id,
        "booking_code": booking_code,
        "payment_id": payment_id,
        "remain_count_after": remain_count_after,
    }
