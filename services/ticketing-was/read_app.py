from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth.auth_user_read import router as auth_user_read_router
from concert.concert_read import router as concert_read_router
from movie.movie_cache_builder import rebuild_movie_cache
from movie.movie_read import router as movie_read_router
from theater.theaters_cache_builder import rebuild_theaters_cache
from theater.theaters_read import router as theaters_read_router
from user.user_read import router as user_read_router

READ_ROUTERS = [
    user_read_router,
    movie_read_router,
    theaters_read_router,
    concert_read_router,
    auth_user_read_router,
]

READ_CACHE_TARGETS = [
    {
        "name": "movie_read",
        "router": movie_read_router,
        "refresher": rebuild_movie_cache,
    },
    {
        "name": "theaters_read",
        "router": theaters_read_router,
        "refresher": rebuild_theaters_cache,
    },
]

app = FastAPI(title="Ticketing Read API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in READ_ROUTERS:
    app.include_router(router)


@app.get("/api/read/health")
def health():
    return {"message": "read api ok"}
