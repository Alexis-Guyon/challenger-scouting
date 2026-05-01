import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import Base, engine
from . import models  # noqa: F401  (register models)
from .routers import admin, auth, compare, players, tournaments, watchlist

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Ensure data directory exists for SQLite
Path("data").mkdir(parents=True, exist_ok=True)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Challenger Lab", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # Allow same-origin (when bundled with the backend) plus Vercel/localhost
    # for the standalone frontend deployment. Auth is via Bearer JWT, not
    # cookies, so a permissive CORS surface is acceptable.
    allow_origin_regex=r"https://.*\.vercel\.app|https://.*\.trycloudflare\.com|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?",
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(players.router)
app.include_router(compare.router)
app.include_router(watchlist.router)
app.include_router(tournaments.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Serve frontend at /
frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
