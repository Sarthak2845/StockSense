"""StockSense dashboard API (FastAPI). Docs at /docs once running.

Read-only for now (Phase 1). Login with roles (FR-6.7), restock, drifts,
suggestions, what-if and /ask arrive in later phases.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

import db
from routes import conflicts, products


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.open_pool()
    yield
    db.close_pool()


app = FastAPI(title="StockSense API", version="0.1.0", lifespan=lifespan)
app.include_router(products.router)
app.include_router(conflicts.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
