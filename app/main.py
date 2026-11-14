import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()  # before app imports read DB_URL and local API credentials

from app.catalog.router import router as catalog
from app.experience.router import router as experience
from app.jury.router import router as jury
from app.orders.router import router as orders


@asynccontextmanager
async def lifespan(_app):
    from app.catalog.warm import start_background
    start_background()  # non-blocking; no-op without GEMINI_API_KEY or embeddings
    yield


app = FastAPI(title="Trusted Trade Agents", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in (os.getenv("CORS_ORIGINS") or "http://localhost:3000").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
for r in (catalog, jury, orders, experience):
    app.include_router(r)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


GAME_DIR = Path(__file__).resolve().parent.parent / "web" / "public" / "game"  # also served by Vercel (Next public/)


def mount_game(target: FastAPI, game_dir: Path) -> bool:
    """Serve the pitch demo game; web/ is absent in the Modal image, so skip instead of crashing."""
    if not game_dir.is_dir():
        return False
    target.mount("/game", StaticFiles(directory=game_dir, html=True), name="game")
    return True


mount_game(app, GAME_DIR)

COCKPIT_DIR = Path(__file__).resolve().parent.parent / "cockpit"  # Vimo buyer cockpit (local + desktop app)


def mount_cockpit(target: FastAPI, cockpit_dir: Path) -> bool:
    """Serve the Vimo cockpit at / from the same origin as the API. Mounted last: a mount at / matches
    every path, so API routes and /game must be registered first. Absent in the Modal image -> skipped."""
    if not cockpit_dir.is_dir():
        return False
    target.mount("/", StaticFiles(directory=cockpit_dir, html=True), name="buyer-cockpit")
    return True


mount_cockpit(app, COCKPIT_DIR)
