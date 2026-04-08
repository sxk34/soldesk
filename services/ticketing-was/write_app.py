from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth.auth_user_write import router as auth_user_write_router
from concert.concert_write import router as concert_write_router
from cache.cache_builder import router as cache_builder_router
from inquiry.inquiry_write import router as inquiry_write_router
from review.review_write import router as review_write_router
from theater.theaters_write import router as theaters_write_router
from user.user_write import router as user_write_router

WRITE_ROUTERS = [
    user_write_router,
    review_write_router,
    inquiry_write_router,
    auth_user_write_router,
    theaters_write_router,
    concert_write_router,
    cache_builder_router,
]

app = FastAPI(title="Ticketing Write API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in WRITE_ROUTERS:
    app.include_router(router)


@app.get("/api/write/health")
def health():
    return {"message": "write api ok"}
