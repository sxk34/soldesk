from typing import Any, Dict, Optional

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from cache.redis_client import redis_client
from db import get_db_connection

router = APIRouter()


@router.post("/api/write/review")
def review(payload: Optional[Dict[str, Any]] = Body(default=None)):
    data = payload or {}
    user_id = data.get("user_id")
    movie_id = data.get("movie_id")
    rating = data.get("rating")
    content = (data.get("content") or "").strip()
    if not user_id or not movie_id or not rating or not content:
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    try:
        user_id_int = int(user_id)
        movie_id_int = int(movie_id)
        rating_int = int(rating)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"message": "invalid input"})
    if rating_int < 1 or rating_int > 5:
        return JSONResponse(status_code=400, content={"message": "rating must be 1~5"})
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id_int,))
            if not cur.fetchone():
                return JSONResponse(status_code=404, content={"message": "user not found"})
            cur.execute("SELECT movie_id FROM movies WHERE movie_id = %s", (movie_id_int,))
            if not cur.fetchone():
                return JSONResponse(status_code=404, content={"message": "movie not found"})
            cur.execute(
                "INSERT INTO reviews (user_id, movie_id, rating, content, review_status) VALUES (%s, %s, %s, %s, 'ACTIVE')",
                (user_id_int, movie_id_int, rating_int, content),
            )
        redis_client.delete(f"movie:detail:{movie_id_int}")
        return {"message": "review success"}
    finally:
        conn.close()
