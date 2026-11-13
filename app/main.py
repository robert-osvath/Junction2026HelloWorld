from fastapi import FastAPI

from app.catalog.router import router as catalog
from app.jury.router import router as jury
from app.orders.router import router as orders

app = FastAPI(title="Trusted Trade Agents")
for r in (catalog, jury, orders):
    app.include_router(r)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
