import hashlib
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from db import get_db_connection

router = APIRouter()


def make_password_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@router.post("/api/write/auth/signup")
def auth_signup(payload: Optional[Dict[str, Any]] = Body(default=None)):
    data = payload or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""
    if not name or not phone or not password:
        return JSONResponse(status_code=400, content={"message": "입력값이 올바르지 않습니다."})
    password_hash = make_password_hash(password)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE phone = %s", (phone,))
            if cur.fetchone():
                return JSONResponse(status_code=409, content={"message": "이미 사용 중인 핸드폰번호입니다."})
            cur.execute(
                "INSERT INTO users (phone, password_hash, name, created_at) VALUES (%s, %s, %s, NOW())",
                (phone, password_hash, name),
            )
        conn.commit()
        return {"message": "signup success", "success": True}
    finally:
        conn.close()


@router.post("/api/write/auth/reset-password")
def auth_reset_password(payload: Optional[Dict[str, Any]] = Body(default=None)):
    data = payload or {}
    phone = (data.get("phone") or "").strip()
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    if not phone or not name or not password:
        return JSONResponse(status_code=400, content={"message": "입력값이 올바르지 않습니다."})
    password_hash = make_password_hash(password)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE phone = %s AND name = %s", (phone, name))
            if not cur.fetchone():
                return JSONResponse(status_code=404, content={"message": "사용자를 찾을 수 없습니다."})
            cur.execute("UPDATE users SET password_hash = %s WHERE phone = %s AND name = %s", (password_hash, phone, name))
        conn.commit()
        return {"message": "password reset success", "success": True}
    finally:
        conn.close()


@router.post("/api/write/auth/change-password")
def auth_change_password(payload: Optional[Dict[str, Any]] = Body(default=None)):
    data = payload or {}
    user_id = data.get("user_id")
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    if not user_id or not current_password or not new_password:
        return JSONResponse(status_code=400, content={"message": "입력값이 올바르지 않습니다."})
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"message": "입력값이 올바르지 않습니다."})
    if current_password == new_password:
        return JSONResponse(status_code=400, content={"message": "현재 비밀번호와 다른 비밀번호를 입력해 주세요."})
    current_hash = make_password_hash(current_password)
    new_hash = make_password_hash(new_password)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, password_hash FROM users WHERE user_id = %s", (user_id_int,))
            user = cur.fetchone()
            if not user:
                return JSONResponse(status_code=404, content={"message": "사용자를 찾을 수 없습니다."})
            if (user.get("password_hash") or "") != current_hash:
                return JSONResponse(status_code=401, content={"message": "현재 비밀번호가 틀립니다."})
            cur.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (new_hash, user_id_int))
        conn.commit()
        return {"message": "password change success", "success": True}
    finally:
        conn.close()


@router.post("/api/write/auth/edit")
def auth_edit_user(payload: Optional[Dict[str, Any]] = Body(default=None)):
    data = payload or {}
    user_id = data.get("user_id")
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    if not user_id or not name or not phone:
        return JSONResponse(status_code=400, content={"message": "입력값이 올바르지 않습니다."})
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"message": "입력값이 올바르지 않습니다."})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id_int,))
            if not cur.fetchone():
                return JSONResponse(status_code=404, content={"message": "사용자를 찾을 수 없습니다."})
            cur.execute("SELECT user_id FROM users WHERE phone = %s AND user_id <> %s", (phone, user_id_int))
            if cur.fetchone():
                return JSONResponse(status_code=409, content={"message": "이미 사용 중인 핸드폰번호입니다."})
            cur.execute("UPDATE users SET name = %s, phone = %s WHERE user_id = %s", (name, phone, user_id_int))
        conn.commit()
        return {"message": "edit success", "success": True}
    finally:
        conn.close()
