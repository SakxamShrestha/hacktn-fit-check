"""HTTP routes. Three pages: add a device, pick a clinic, read the verdict."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.ai.client import GenAiError
from app.ai.extract import extract_device, image_to_data_url
from app.ai.research import research_supply
from app.checks import check_fit
from app.data import repository
from app.data.repository import RepositoryError
from app.data.seed import FACILITIES, get_facility
from app.domain.result import CheckStatus, Outcome
from app.web.presenters import (
    TONE_BY_OUTCOME,
    TONE_BY_OUTCOME_VALUE,
    TONE_BY_STATUS,
    device_kind,
    identity_pairs,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()

def _error(request: Request, message: str, back: str = "/donor") -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="error.html",
        context={"message": message, "back": back},
        status_code=400,
    )


@router.get("/donor", response_class=HTMLResponse)
def donor_form(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name="donor.html", context={}
    )


@router.post("/donor", response_class=HTMLResponse)
async def donor_submit(
    request: Request,
    model_name: str = Form(default=""),
    spec_text: str = Form(default=""),
    nameplate: UploadFile | None = File(default=None),
) -> HTMLResponse:
    typed = "\n".join(part for part in (model_name.strip(), spec_text.strip()) if part)

    image_data_url = ""
    if nameplate is not None and nameplate.filename:
        try:
            raw = await nameplate.read()
            image_data_url = image_to_data_url(raw, nameplate.content_type or "")
        except ValueError as exc:
            return _error(request, str(exc))

    if not typed and not image_data_url:
        return _error(
            request, "Give a model name, paste some spec text, or add a nameplate photo."
        )

    try:
        device = extract_device(typed_text=typed, image_data_url=image_data_url)
        device_id = repository.save_device(device)
    except (GenAiError, RepositoryError, ValueError) as exc:
        return _error(request, str(exc))

    return RedirectResponse(url=f"/clinic?device_id={device_id}", status_code=303)


@router.get("/clinic", response_class=HTMLResponse)
def clinic_form(request: Request, device_id: int) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="clinic.html",
        context={"device_id": device_id, "facilities": FACILITIES},
    )


@router.post("/check", response_class=HTMLResponse)
def run_check(
    request: Request,
    device_id: int = Form(...),
    facility_slug: str = Form(...),
) -> HTMLResponse:
    stored = repository.load_device(device_id)
    if stored is None:
        return _error(request, "That device is no longer in the database.")

    try:
        facility = get_facility(facility_slug)
    except KeyError as exc:
        return _error(request, str(exc), back=f"/clinic?device_id={device_id}")

    try:
        intel = research_supply(stored, facility.country)
        verdict = check_fit(stored, facility, intel)
        verdict_id = repository.save_verdict(
            device_id=device_id,
            device=stored,
            facility=facility,
            verdict=verdict,
            intel=intel,
        )
    except (GenAiError, RepositoryError) as exc:
        return _error(request, str(exc), back=f"/clinic?device_id={device_id}")

    return RedirectResponse(url=f"/verdict/{verdict_id}", status_code=303)


@router.get("/verdict/{verdict_id}", response_class=HTMLResponse)
def verdict_page(request: Request, verdict_id: str) -> HTMLResponse:
    try:
        record = repository.load_verdict(verdict_id)
    except RepositoryError as exc:
        return _error(request, str(exc))
    if record is None:
        return _error(request, f"No verdict with id {verdict_id}.")

    facility = get_facility(record["facility_slug"])
    return TEMPLATES.TemplateResponse(
        request=request,
        name="verdict.html",
        context={
            "record": record,
            "verdict": record["verdict"],
            "device": record["device"],
            "intel": record["intel"],
            "facility": facility,
            "pairs": identity_pairs(record["device"]),
            "kind": device_kind(record["device"]),
            "symbol": TONE_BY_STATUS,
            "tone": TONE_BY_OUTCOME,
        },
    )


@router.get("/impact", response_class=HTMLResponse)
def impact_page(request: Request) -> HTMLResponse:
    try:
        reasons = repository.failures_by_reason()
        context = {
            "counts": repository.count_by_outcome(),
            "reasons": reasons,
            "peak": max((n for _, n in reasons), default=1),
            "recent": repository.recent_verdicts(),
            "tone": TONE_BY_OUTCOME_VALUE,
        }
    except RepositoryError as exc:
        return _error(request, str(exc), back="/donor")
    return TEMPLATES.TemplateResponse(
        request=request, name="impact.html", context=context
    )
