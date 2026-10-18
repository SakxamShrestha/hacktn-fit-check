"""Entry point. Run with: uvicorn app.main:app --reload"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.data import repository
from app.web.marketplace_routes import TEMPLATES, router as marketplace_router
from app.web.routes import router

STATIC_DIR = Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository.setup()
    yield


app = FastAPI(title="Fit-Check", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(marketplace_router)
app.include_router(router)


NOT_FOUND = 404


@app.exception_handler(NOT_FOUND)
async def page_not_found(request: Request, _exc: object) -> HTMLResponse:
    """Render a missing page, rather than handing a visitor raw JSON.

    FastAPI answers an unknown path with {"detail": "Not Found"}, which is
    right for an API and wrong for a service people browse.
    """
    return TEMPLATES.TemplateResponse(
        request=request,
        name="not_found.html",
        context={"path": request.url.path},
        status_code=NOT_FOUND,
    )
