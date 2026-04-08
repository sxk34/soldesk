"""
환경변수 기반 설정 (EKS ConfigMap/Secret에서 주입)
원본: config.py (하드코딩) → AWS 환경용으로 변환
"""
import os

# ── DB (Aurora RDS) ──────────────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_WRITER_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_NAME     = os.getenv("DB_NAME", "ticketing_test")
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "soldesk1")

# Reader 엔드포인트 (읽기 전용 쿼리용 — 현재 코드는 단일 커넥션이라 미사용)
DB_READER_HOST = os.getenv("DB_READER_HOST", DB_HOST)

# ── Redis (ElastiCache) ─────────────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# ── SQS ──────────────────────────────────────────────────────────────────────
AWS_REGION    = os.getenv("AWS_REGION", "ap-northeast-2")
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")

# ── API 포트 ─────────────────────────────────────────────────────────────────
READ_API_HOST  = "0.0.0.0"
READ_API_PORT  = int(os.getenv("READ_API_PORT", "5000"))
WRITE_API_HOST = "0.0.0.0"
WRITE_API_PORT = int(os.getenv("WRITE_API_PORT", "5001"))
