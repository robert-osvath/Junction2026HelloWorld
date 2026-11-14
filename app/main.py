import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()  # before app imports read DB_URL

from app.catalog.router import router as catalog
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
for r in (catalog, jury, orders):
    app.include_router(r)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
