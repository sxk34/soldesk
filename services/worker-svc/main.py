"""
SQS FIFO Worker — 예매 메시지 처리.
theaters_write._commit_booking_sync / concert_write._commit_concert_booking_sync 로직을 재사용.
"""
import os
import sys
import json
import asyncio
import logging

import boto3
import redis
import pymysql

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker-svc")

AWS_REGION    = os.getenv("AWS_REGION", "ap-northeast-2")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")
DB_HOST       = os.getenv("DB_WRITER_HOST", "127.0.0.1")
DB_PORT       = int(os.getenv("DB_PORT", "3306"))
DB_NAME       = os.getenv("DB_NAME", "ticketing_test")
DB_USER       = os.getenv("DB_USER", "root")
DB_PASSWORD   = os.getenv("DB_PASSWORD", "soldesk1")
REDIS_HOST    = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT    = int(os.getenv("REDIS_PORT", "6379"))

sqs = boto3.client("sqs", region_name=AWS_REGION)
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def get_tx_conn():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def _to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _parse_seat_key(value):
    parts = str(value or "").strip().split("-")
    if len(parts) != 2:
        return None
    r, c = _to_int(parts[0]), _to_int(parts[1])
    return (r, c) if r > 0 and c > 0 else None


def _generate_booking_code():
    import secrets, string
    letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(2))
    digits = "".join(secrets.choice(string.digits) for _ in range(6))
    return f"{letters}{digits}"


def store_result(booking_ref, result):
    """처리 결과를 Redis에 저장 (write-api에서 조회)."""
    key = f"booking:result:{booking_ref}"
    redis_client.setex(key, 600, json.dumps(result, default=str))
    log.info("결과 저장: %s", key)


# ── 극장 예매 처리 ────────────────────────────────────────────────────────────
def process_theater_booking(body):
    booking_ref = body["booking_ref"]
    user_id = _to_int(body["user_id"])
    schedule_id = _to_int(body["schedule_id"])
    seats = body.get("seats") or []

    parsed_seats = [_parse_seat_key(s) for s in seats]
    parsed_seats = [s for s in parsed_seats if s]
    req_count = len(parsed_seats)

    conn = get_tx_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT schedule_id, hall_id, total_count, remain_count "
                "FROM schedules WHERE schedule_id = %s FOR UPDATE",
                (schedule_id,),
            )
            schedule = cur.fetchone()
            if not schedule:
                store_result(booking_ref, {"ok": False, "code": "NOT_FOUND"})
                return

            hall_id = _to_int(schedule.get("hall_id"))

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
                    store_result(booking_ref, {"ok": False, "code": "INVALID_SEAT"})
                    return
                seat_ids.append(_to_int(seat_row.get("seat_id")))

            cur.execute(
                "UPDATE schedules SET remain_count = remain_count - %s "
                "WHERE schedule_id = %s AND remain_count >= %s",
                (req_count, schedule_id, req_count),
            )
            if cur.rowcount != 1:
                conn.rollback()
                store_result(booking_ref, {"ok": False, "code": "SOLD_OUT"})
                return

            cur.execute(
                "INSERT INTO booking (user_id, schedule_id, reg_count, book_status, created_at) "
                "VALUES (%s, %s, %s, 'PAID', NOW())",
                (user_id, schedule_id, req_count),
            )
            booking_id = cur.lastrowid

            booking_code = ""
            for _ in range(12):
                code = _generate_booking_code()
                try:
                    cur.execute("UPDATE booking SET booking_code = %s WHERE booking_id = %s", (code, booking_id))
                    booking_code = code
                    break
                except pymysql.err.IntegrityError:
                    continue

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

            cur.execute("SELECT remain_count FROM schedules WHERE schedule_id = %s", (schedule_id,))
            remain = cur.fetchone()
            remain_count_after = _to_int(remain.get("remain_count") if remain else 0)

            if remain_count_after <= 0:
                cur.execute("UPDATE schedules SET status = 'CLOSED' WHERE schedule_id = %s", (schedule_id,))

        conn.commit()
        store_result(booking_ref, {
            "ok": True, "code": "OK",
            "booking_id": booking_id, "booking_code": booking_code,
            "payment_id": payment_id, "remain_count_after": remain_count_after,
        })

    except pymysql.err.IntegrityError:
        conn.rollback()
        store_result(booking_ref, {"ok": False, "code": "DUPLICATE_SEAT"})
    except Exception as e:
        conn.rollback()
        store_result(booking_ref, {"ok": False, "code": "ERROR", "message": str(e)})
        log.error("극장 예매 처리 실패: %s", e)
    finally:
        conn.close()

    # 캐시 무효화
    try:
        for key in redis_client.keys("theaters:*"):
            redis_client.delete(key)
    except Exception:
        pass


# ── 콘서트 예매 처리 ─────────────────────────────────────────────────────────
def process_concert_booking(body):
    booking_ref = body["booking_ref"]
    user_id = _to_int(body["user_id"])
    show_id = _to_int(body["show_id"])
    seats = body.get("seats") or []

    parsed_seats = [_parse_seat_key(s) for s in seats]
    parsed_seats = [s for s in parsed_seats if s]
    req_count = len(parsed_seats)

    conn = get_tx_conn()
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
                store_result(booking_ref, {"ok": False, "code": "NOT_FOUND"})
                return

            seat_rows = _to_int(show.get("seat_rows"))
            seat_cols = _to_int(show.get("seat_cols"))

            for row_no, col_no in parsed_seats:
                if row_no > seat_rows or col_no > seat_cols:
                    conn.rollback()
                    store_result(booking_ref, {"ok": False, "code": "INVALID_SEAT"})
                    return

            cur.execute(
                "UPDATE concert_shows SET remain_count = remain_count - %s "
                "WHERE show_id = %s AND remain_count >= %s "
                "AND UPPER(COALESCE(status, '')) = 'OPEN'",
                (req_count, show_id, req_count),
            )
            if cur.rowcount != 1:
                conn.rollback()
                store_result(booking_ref, {"ok": False, "code": "SOLD_OUT"})
                return

            cur.execute(
                "INSERT INTO concert_booking (user_id, show_id, reg_count, book_status, created_at) "
                "VALUES (%s, %s, %s, 'PAID', NOW())",
                (user_id, show_id, req_count),
            )
            booking_id = cur.lastrowid

            import secrets, string
            booking_code = ""
            for _ in range(12):
                letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(2))
                digits = "".join(secrets.choice(string.digits) for _ in range(6))
                code = f"C{letters}{digits}"
                try:
                    cur.execute("UPDATE concert_booking SET booking_code = %s WHERE booking_id = %s", (code, booking_id))
                    booking_code = code
                    break
                except pymysql.err.IntegrityError:
                    continue

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

            cur.execute("SELECT remain_count FROM concert_shows WHERE show_id = %s", (show_id,))
            remain_row = cur.fetchone()
            remain_count_after = _to_int(remain_row.get("remain_count") if remain_row else 0)

            if remain_count_after <= 0:
                cur.execute("UPDATE concert_shows SET status = 'CLOSED' WHERE show_id = %s", (show_id,))

        conn.commit()
        store_result(booking_ref, {
            "ok": True, "code": "OK",
            "booking_id": booking_id, "booking_code": booking_code,
            "payment_id": payment_id, "remain_count_after": remain_count_after,
        })

    except pymysql.err.IntegrityError:
        conn.rollback()
        store_result(booking_ref, {"ok": False, "code": "DUPLICATE_SEAT"})
    except Exception as e:
        conn.rollback()
        store_result(booking_ref, {"ok": False, "code": "ERROR", "message": str(e)})
        log.error("콘서트 예매 처리 실패: %s", e)
    finally:
        conn.close()


# ── SQS 폴링 루프 ────────────────────────────────────────────────────────────
def poll_loop():
    log.info("worker-svc 시작 — SQS 폴링: %s", SQS_QUEUE_URL)
    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=20,
            )
            for msg in resp.get("Messages", []):
                try:
                    body = json.loads(msg["Body"])
                    booking_type = body.get("booking_type", "theater")
                    if booking_type == "concert":
                        process_concert_booking(body)
                    else:
                        process_theater_booking(body)
                except Exception as e:
                    log.error("메시지 처리 실패: %s", e)
                finally:
                    sqs.delete_message(
                        QueueUrl=SQS_QUEUE_URL,
                        ReceiptHandle=msg["ReceiptHandle"],
                    )
        except Exception as e:
            log.error("SQS 폴링 오류: %s", e)
            import time
            time.sleep(3)


# ── FastAPI (헬스체크 + 메트릭) ───────────────────────────────────────────────
from fastapi import FastAPI
from contextlib import asynccontextmanager
import threading


@asynccontextmanager
async def lifespan(app):
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "worker-svc"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5002")))
