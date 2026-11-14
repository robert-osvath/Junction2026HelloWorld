import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.catalog.router import router as catalog
from app.experience.router import router as experience
from app.jury.router import router as jury
from app.orders.router import router as orders

load_dotenv()  # before app imports read DB_URL and local API credentials

app = FastAPI(title="Trusted Trade Agents")
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


# The buyer cockpit is deliberately served by the same origin as the API so it
# can call the mock endpoints during the hackathon without a separate CORS or
# deployment configuration.
app.mount("/", StaticFiles(directory="web", html=True), name="buyer-cockpit")
