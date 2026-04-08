"""
SQS FIFO 클라이언트 — 예매 직렬화용 (theaters_write, concert_write에서 사용)
원본 코드의 _ScheduleLockPool / _ShowLockPool(in-process threading.Lock)을
EKS 다중 Pod 환경에서 안전한 SQS FIFO MessageGroupId 방식으로 교체.
"""
import json
import hashlib
import uuid
import logging

import boto3

from config import AWS_REGION, SQS_QUEUE_URL

log = logging.getLogger("sqs_client")

sqs = boto3.client("sqs", region_name=AWS_REGION) if SQS_QUEUE_URL else None


def send_booking_message(booking_type: str, group_id: str, payload: dict) -> str:
    """
    SQS FIFO 큐에 예매 메시지 전송.
    - booking_type: "theater" 또는 "concert"
    - group_id: schedule_id 또는 show_id (FIFO MessageGroupId)
    - payload: 예매 요청 데이터
    Returns: booking_ref (결과 조회용 UUID)
    """
    booking_ref = str(uuid.uuid4())

    body = {
        "booking_type": booking_type,
        "booking_ref": booking_ref,
        **payload,
    }

    raw = json.dumps(body, sort_keys=True)
    dedup_id = hashlib.sha256(raw.encode()).hexdigest()

    if not sqs or not SQS_QUEUE_URL:
        log.warning("SQS 미설정 — 메시지 전송 스킵 (로컬 테스트 모드)")
        return booking_ref

    sqs.send_message(
        QueueUrl=SQS_QUEUE_URL,
        MessageGroupId=str(group_id),
        MessageDeduplicationId=dedup_id,
        MessageBody=raw,
    )
    log.info("SQS 전송: type=%s, group=%s, ref=%s", booking_type, group_id, booking_ref)
    return booking_ref


def get_booking_result(booking_ref: str):
    """Redis에서 예매 처리 결과 조회 (worker-svc가 저장)."""
    from cache.redis_client import redis_client
    key = f"booking:result:{booking_ref}"
    data = redis_client.get(key)
    if data:
        return json.loads(data)
    return None
