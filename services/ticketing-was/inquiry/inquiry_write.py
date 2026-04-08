from typing import Any, Dict, Optional

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from db import get_db_connection

router = APIRouter()


@router.post("/api/write/inquiry")
def inquiry(payload: Optional[Dict[str, Any]] = Body(default=None)):
    data = payload or {}
    user_id = data.get("user_id")
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    if not user_id or not title or not content:
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id_int,))
            if not cur.fetchone():
                return JSONResponse(status_code=404, content={"message": "user not found"})
            cur.execute(
                "INSERT INTO inquiries (user_id, title, content, inquiry_status) VALUES (%s, %s, %s, 'OPEN')",
                (user_id_int, title, content),
            )
        return {"message": "inquiry success"}
    finally:
        conn.close()
