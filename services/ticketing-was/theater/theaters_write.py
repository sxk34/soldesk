"""
극장 예매 쓰기 — SQS FIFO 통합 버전.

원본: _ScheduleLockPool(threading.Lock)으로 in-process 직렬화
변경: SQS FIFO MessageGroupId=schedule_id로 다중 Pod 직렬화.
      실제 DB 트랜잭션은 worker-svc가 처리.
"""
import json
import secrets
import string
import threading
from contextlib import contextmanager

import pymysql
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cache.redis_client import redis_client
from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, SQS_QUEUE_URL
from sqs_client import send_booking_message, get_booking_result

router = APIRouter()


# ── 로컬 폴백 (SQS 미설정 시 원본 로직 그대로 사용) ──────────────────────────
class _ScheduleLockPool:
    """Local-only fallback. SQS_QUEUE_URL이 비어있을 때만 사용."""

    def __init__(self):
        self._global_lock = threading.Lock()
        self._locks = {}

    @contextmanager
    def acquire(self, schedule_id: int):
        with self._global_lock:
            lock = self._locks.get(schedule_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[schedule_id] = lock
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


_schedule_locks = _ScheduleLockPool()


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
    return f"{letters}{digits}"


@router.post("/api/write/theaters/booking/commit")
def commit_booking(payload: dict):
    data = payload if isinstance(payload, dict) else {}
    user_id = _to_int(data.get("user_id"))
    schedule_id = _to_int(data.get("schedule_id"))
    seats = data.get("seats") or []

    if user_id <= 0 or schedule_id <= 0:
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

    # ── SQS 모드: 메시지 큐잉 후 QUEUED 응답 ────────────────────────────────
    if SQS_QUEUE_URL:
        booking_ref = send_booking_message(
            booking_type="theater",
            group_id=str(schedule_id),
            payload={
                "user_id": user_id,
                "schedule_id": schedule_id,
                "seats": [f"{r}-{c}" for r, c in parsed_seats],
            },
        )
        return {
            "ok": True,
            "code": "QUEUED",
            "booking_ref": booking_ref,
            "message": "예매 요청이 접수되었습니다. 잠시 후 결과를 확인해주세요.",
        }

    # ── 로컬 폴백: 원본 동기 로직 (SQS 미설정 시) ────────────────────────────
    return _commit_booking_sync(user_id, schedule_id, parsed_seats, req_count)


def _commit_booking_sync(user_id, schedule_id, parsed_seats, req_count):
    """원본 동기 예매 로직 (worker-svc에서도 재사용)."""
    from theater.theaters_read import THEATERS_BOOTSTRAP_CACHE_KEY, refresh_theaters_bootstrap_cache

    booking_id = 0
    payment_id = 0
    booking_code = ""
    remain_count_after = 0

    with _schedule_locks.acquire(schedule_id):
        conn = _get_tx_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT s.schedule_id, s.hall_id, s.total_count, s.remain_count "
                    "FROM schedules s WHERE s.schedule_id = %s FOR UPDATE",
                    (schedule_id,),
                )
                schedule = cur.fetchone()
                if not schedule:
                    conn.rollback()
                    return JSONResponse(
                        status_code=404,
                        content={"ok": False, "code": "NOT_FOUND", "message": "회차를 찾을 수 없습니다."},
                    )

                hall_id = _to_int(schedule.get("hall_id"))
                if hall_id <= 0:
                    conn.rollback()
                    return JSONResponse(status_code=500, content={"ok": False, "code": "ERROR"})

                seat_ids = []
                for row_no, col_no in parsed_seats:
                    cur.execute(
                        "SELECT seat_id FROM hall_seats "
                        "WHERE hall_id = %s AND seat_row_no = %s AND seat_col_no = %s",
                        (hall_id, row_no, col_no),
                    )
                    seat_row = cur.fetchone()
                    if not seat_row:
                        conn.rollback()
                        return JSONResponse(
                            status_code=400,
                            content={"ok": False, "code": "INVALID_SEAT", "message": "유효하지 않은 좌석입니다."},
                        )
                    seat_ids.append(_to_int(seat_row.get("seat_id")))

                cur.execute(
                    "UPDATE schedules SET remain_count = remain_count - %s "
                    "WHERE schedule_id = %s AND remain_count >= %s",
                    (req_count, schedule_id, req_count),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    try:
                        redis_client.delete(THEATERS_BOOTSTRAP_CACHE_KEY)
                        refresh_theaters_bootstrap_cache()
                    except Exception:
                        pass
                    return {"ok": False, "code": "SOLD_OUT"}

                cur.execute(
                    "INSERT INTO booking (user_id, schedule_id, reg_count, book_status, created_at) "
                    "VALUES (%s, %s, %s, 'PAID', NOW())",
                    (user_id, schedule_id, req_count),
                )
                booking_id = cur.lastrowid

                for _ in range(12):
                    code = _generate_booking_code()
                    try:
                        cur.execute(
                            "UPDATE booking SET booking_code = %s WHERE booking_id = %s",
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

                for seat_id in seat_ids:
                    cur.execute(
                        "INSERT INTO booking_seats (booking_id, schedule_id, seat_id, created_at) "
                        "VALUES (%s, %s, %s, NOW())",
                        (booking_id, schedule_id, seat_id),
                    )

                cur.execute(
                    "INSERT INTO payment (booking_id, pay_yn, paid_at, created_at) "
                    "VALUES (%s, 'Y', NOW(), NOW())",
                    (booking_id,),
                )
                payment_id = cur.lastrowid

                cur.execute(
                    "SELECT remain_count FROM schedules WHERE schedule_id = %s",
                    (schedule_id,),
                )
                remain_after = cur.fetchone()
                remain_count_after = _to_int(remain_after.get("remain_count") if remain_after else 0)

                if remain_count_after <= 0:
                    cur.execute(
                        "UPDATE schedules SET status = 'CLOSED' WHERE schedule_id = %s",
                        (schedule_id,),
                    )

            conn.commit()

        except Exception as exc:
            conn.rollback()
            if _is_duplicate_key_error(exc):
                try:
                    redis_client.delete(THEATERS_BOOTSTRAP_CACHE_KEY)
                    refresh_theaters_bootstrap_cache()
                except Exception:
                    pass
                return {"ok": False, "code": "DUPLICATE_SEAT"}
            return JSONResponse(
                status_code=500,
                content={"ok": False, "code": "ERROR", "message": str(exc)},
            )
        finally:
            conn.close()

    try:
        redis_client.delete(THEATERS_BOOTSTRAP_CACHE_KEY)
        refresh_theaters_bootstrap_cache()
    except Exception:
        pass

    return {
        "ok": True,
        "code": "OK",
        "booking_id": booking_id,
        "booking_code": booking_code,
        "payment_id": payment_id,
        "remain_count_after": remain_count_after,
    }


@router.get("/api/write/booking/status/{booking_ref}")
def check_booking_status(booking_ref: str):
    """SQS 비동기 예매 결과 조회 (프론트엔드에서 폴링)."""
    result = get_booking_result(booking_ref)
    if result is None:
        return {"status": "PROCESSING", "booking_ref": booking_ref}
    return result
