"""Routes for the two-sided platform: offers, facilities, requests, matches."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.ai.categorise import categorise_support
from app.ai.client import GenAiError
from app.ai.extract import extract_device, image_to_data_url
from app.data import marketplace_repo as repo
from app.data.repository import RepositoryError
from app.domain.device import DeviceFacts
from app.domain.facility import FacilityProfile
from app.domain.facility_trust import Verification, VerificationStatus
from app.domain.marketplace import (
    GeoPoint,
    MatchState,
    Offer,
    Request as DonationRequest,
    RequestLine,
)
from app.domain.provenance import Provenance, fact
from app.domain.result import Outcome
from app.matching.bundle import build_bundle
from app.intake.freetext import parse_list
from app.intake.tables import IntakeError, parse_table
from app.matching.coverage import LineCoverage
from app.matching.pipeline import propose_match, rank_candidates
from app.matching.substitutes import find_substitutes
from app.matching.verification import confirm, request_confirmation, withdraw
from app.reference.country_power import country_centroid, lookup
from app.reference.facility_registry import corroborates, find_candidates
from app.storage.photos import discard_photo, fetch_photo, store_photo
from app.web import forms
from app.web.forms import FormError
from app.web.presenters import TONE_BY_OUTCOME, TONE_BY_STATUS

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()


def _error(request: Request, message: str, back: str = "/") -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name="error.html",
        context={"message": message, "back": back}, status_code=400,
    )


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    """The landing workstation, with the seeded clinics as testbeds.

    The telemetry on each card is read from the stored profile rather than
    written into the template, so a figure on the front page cannot drift away
    from the figure the screening actually uses.
    """
    testbeds = [
        {
            "slug": f.slug,
            "name": f.name,
            "country": f.country,
            "voltage": f.mains_voltage_v,
            "frequency": f.mains_frequency_hz,
            "outages": f.outages_per_week,
            "temp": f.ambient_temp_max_c,
        }
        for f in repo.list_facilities()
    ]
    return TEMPLATES.TemplateResponse(
        request=request, name="landing.html", context={"testbeds": testbeds}
    )


# ---- donor side --------------------------------------------------------

@router.get("/offer", response_class=HTMLResponse)
def offer_form(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request=request, name="offer.html", context={})


@router.post("/offer", response_class=HTMLResponse)
async def offer_submit(
    request: Request,
    spec_text: str = Form(default=""),
    device_type: str = Form(default=""),
    quantity: str = Form(default="1"),
    available_until: str = Form(default=""),
    expiry_date: str = Form(default=""),
    country: str = Form(default=""),
    city: str = Form(default=""),
    donor_name: str = Form(default=""),
    donor_email: str = Form(default=""),
    nameplate: UploadFile | None = File(default=None),
) -> HTMLResponse:
    try:
        typed = spec_text.strip()
        image_data_url = ""
        photo = None
        if nameplate is not None and nameplate.filename:
            photo = (await nameplate.read(), nameplate.content_type or "")
            image_data_url = image_to_data_url(*photo)
        if not typed and not image_data_url:
            raise FormError("Give a model name, paste spec text, or add a nameplate photo.")

        dated = forms.iso_date(expiry_date, "Expiry date")
        offer = Offer(
            donor_name=forms.text(donor_name, "Your name", required=True),
            donor_email=forms.text(donor_email, "Email", required=True),
            device=_with_expiry(
                extract_device(typed_text=typed, image_data_url=image_data_url), dated
            ),
            device_type=forms.text(device_type, "Kind of device", required=True, limit=120),
            support_kind=_support_kind(device_type, typed),
            origin=_point(forms.text(country, "Country", required=True), city),
            quantity_offered=forms.whole_number(quantity, "How many", default=1, minimum=1),
            available_until=forms.iso_date(available_until, "Available until"),
            # Uploaded only once everything else has succeeded. Storing it
            # earlier left an object in the bucket that no offer pointed at
            # every time extraction or validation failed.
            photo_key=(store_photo(*photo) or "") if photo else "",
        )
        try:
            offer_id = repo.save_offer(offer)
        except RepositoryError:
            # The object is already up and nothing will ever reference it.
            if offer.photo_key:
                discard_photo(offer.photo_key)
            raise
    except (FormError, ValueError, GenAiError, RepositoryError) as exc:
        return _error(request, str(exc), back="/offer")

    return RedirectResponse(url=f"/offer/{offer_id}", status_code=303)


@router.get("/offer/{offer_id}", response_class=HTMLResponse)
def offer_posted(request: Request, offer_id: int) -> HTMLResponse:
    offer = repo.load_offer(offer_id)
    if offer is None:
        return _error(request, f"No offer with id {offer_id}.")
    return TEMPLATES.TemplateResponse(
        request=request, name="offer_posted.html",
        context={"offer": offer, "pairs": _device_pairs(offer)},
    )


# ---- clinic side -------------------------------------------------------

@router.get("/clinic", response_class=HTMLResponse)
def facility_form(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request=request, name="facility_form.html", context={})


@router.post("/clinic", response_class=HTMLResponse)
async def facility_submit(request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        name = forms.text(str(form.get("name", "")), "Facility name", required=True)
        country = forms.text(str(form.get("country", "")), "Country", required=True)
        grid = lookup(country)
        latitude, longitude = (country_centroid(country) or (None, None))

        profile = FacilityProfile(
            slug=forms.slugify(name),
            name=name,
            country=country,
            mains_voltage_v=forms.whole_number(
                str(form.get("mains_voltage_v", "")), "Mains voltage",
                default=grid.mains_voltage_v if grid else 230, minimum=1),
            mains_frequency_hz=forms.whole_number(
                str(form.get("mains_frequency_hz", "")), "Frequency",
                default=grid.mains_frequency_hz if grid else 50, minimum=1),
            plug_types=tuple(grid.plug_types) if grid else (),
            ambient_temp_max_c=forms.decimal(str(form.get("ambient_temp_max_c", "")), "Temperature", default=30.0),
            humidity_max_pct=forms.decimal(str(form.get("humidity_max_pct", "")), "Humidity", default=70.0),
            has_air_conditioning=forms.flag(form.get("has_air_conditioning")),
            outages_per_week=forms.whole_number(str(form.get("outages_per_week", "")), "Outages"),
            has_voltage_stabilizer=forms.flag(form.get("has_voltage_stabilizer")),
            has_generator=forms.flag(form.get("has_generator")),
            has_three_phase_power=forms.flag(form.get("has_three_phase_power")),
            has_oxygen_supply=forms.flag(form.get("has_oxygen_supply")),
            has_distilled_water=forms.flag(form.get("has_distilled_water")),
            has_compressed_air=forms.flag(form.get("has_compressed_air")),
            has_biomed_technician=forms.flag(form.get("has_biomed_technician")),
            trained_operators=forms.flag(form.get("trained_operators")),
            staff_languages=forms.comma_list(str(form.get("staff_languages", ""))) or ("English",),
            notes=forms.text(str(form.get("notes", "")), "Notes", limit=600),
            latitude=latitude,
            longitude=longitude,
        )
        repo.save_facility(profile)
    except (FormError, RepositoryError) as exc:
        return _error(request, str(exc), back="/clinic")

    return RedirectResponse(url="/request", status_code=303)


# ---- requests and matching --------------------------------------------

@router.get("/request", response_class=HTMLResponse)
def request_form(request: Request, facility: str = "") -> HTMLResponse:
    """The request form, optionally opened on a named facility.

    `facility` is what the landing page's demo chips carry, so a judge lands
    on the form with the clinic already chosen.
    """
    return TEMPLATES.TemplateResponse(
        request=request, name="request_form.html",
        context={"facilities": repo.list_facilities(), "chosen": facility},
    )


@router.post("/request", response_class=HTMLResponse)
async def request_submit(request: Request) -> HTMLResponse:
    """Accept a request of any length.

    The form posts one device_type and one quantity per row, so the fields
    arrive as parallel lists and are read from the raw form rather than
    declared as named parameters.
    """
    form = await request.form()
    try:
        row = DonationRequest(
            facility_slug=forms.text(
                str(form.get("facility_slug") or ""), "Facility", required=True
            ),
            lines=_lines_from(
                form.getlist("device_type"), form.getlist("quantity")
            ),
            note=forms.text(str(form.get("note") or ""), "Note", limit=600),
        )
        request_id = repo.save_request(row)
    except (FormError, ValueError, RepositoryError) as exc:
        return _error(request, str(exc), back="/request")
    return RedirectResponse(url=f"/request/{request_id}/matches", status_code=303)


@router.get("/request/upload", response_class=HTMLResponse)
def request_upload_form(request: Request) -> HTMLResponse:
    """Arrive with a spreadsheet or a sentence instead of typing a form."""
    return TEMPLATES.TemplateResponse(
        request=request, name="request_upload.html",
        context={"facilities": repo.list_facilities()},
    )


@router.post("/request/upload", response_class=HTMLResponse)
async def request_upload_submit(request: Request) -> HTMLResponse:
    """Turn an uploaded table or a typed list into one request.

    Both routes end at repo.save_request, so however a clinic arrives there is
    still exactly one way a request is stored.
    """
    form = await request.form()
    facility_slug = str(form.get("facility_slug") or "").strip()
    typed = str(form.get("wanted_text") or "").strip()
    upload = form.get("list_file")

    try:
        if not facility_slug:
            raise FormError("Choose which facility this request is for.")

        lines: tuple = ()
        if upload is not None and getattr(upload, "filename", ""):
            lines = parse_table(await upload.read(), upload.filename)
        elif typed:
            lines = parse_list(typed, vocabulary=repo.known_device_types())
        else:
            raise FormError("Upload a list, or type what you need.")

        request_id = repo.save_request(
            DonationRequest(facility_slug=facility_slug, lines=lines, note=typed[:600])
        )
    except (FormError, IntakeError, ValueError, RepositoryError) as exc:
        return _error(request, str(exc), back="/request/upload")

    return RedirectResponse(url=f"/request/{request_id}/select", status_code=303)


@router.get("/request/{request_id}/matches", response_class=HTMLResponse)
def matches(request: Request, request_id: int) -> HTMLResponse:
    row = repo.load_request(request_id)
    if row is None:
        return _error(request, f"No request with id {request_id}.")
    facility = repo.load_facility(row.facility_slug)
    if facility is None:
        return _error(request, f"No facility profile for {row.facility_slug}.")

    coverages = []
    bundles: dict[int, object] = {}
    for line in row.lines:
        offers = repo.open_offers_for_type(line.device_type)
        candidates = rank_candidates(facility, offers)
        coverages.append(
            LineCoverage(line=line, candidates=candidates, screened=len(offers))
        )
        for c in candidates:
            bundles[c.offer.id] = build_bundle(c.verdict, repo.offers_for_support)

    return TEMPLATES.TemplateResponse(
        request=request, name="matches.html",
        context={
            "request_row": row, "facility": facility,
            "coverages": coverages, "bundles": bundles,
            "symbol": TONE_BY_STATUS, "tone": TONE_BY_OUTCOME,
        },
    )


@router.get("/request/{request_id}/select", response_class=HTMLResponse)
def request_select(request: Request, request_id: int) -> HTMLResponse:
    """Choose a unit per line, from a list the screening has already filtered.

    Blocked offers are carried through rather than dropped. A clinic that sees
    a shorter list with no explanation learns nothing; one that sees the unit
    greyed out with the check that stopped it learns what to fix.
    """
    row = repo.load_request(request_id)
    if row is None:
        return _error(request, f"No request with id {request_id}.")
    facility = repo.load_facility(row.facility_slug)
    if facility is None:
        return _error(request, f"No facility profile for {row.facility_slug}.")

    groups = []
    for line in row.lines:
        offers = repo.open_offers_for_type(line.device_type)
        ranked = rank_candidates(facility, offers)
        blocked = _blocked_for(facility, offers)
        groups.append({
            "coverage": LineCoverage(line=line, candidates=ranked, screened=len(offers)),
            "blocked": blocked,
            "substitutes": (
                find_substitutes(line.device_type, facility, repo.open_offers_for_type)
                if not ranked else ()
            ),
        })

    return TEMPLATES.TemplateResponse(
        request=request, name="request_select.html",
        context={
            "request_row": row, "facility": facility, "groups": groups,
            "tone": TONE_BY_OUTCOME,
        },
    )


@router.post("/request/{request_id}/select", response_class=HTMLResponse)
async def request_select_submit(request: Request, request_id: int) -> HTMLResponse:
    """Record what the clinic chose and tell them what happens next."""
    row = repo.load_request(request_id)
    if row is None:
        return _error(request, f"No request with id {request_id}.")

    form = await request.form()
    chosen = []
    for line in row.lines:
        raw = str(form.get(f"choice_{line.device_type}") or "").strip()
        if not raw.isdigit():
            continue
        offer = repo.load_offer(int(raw))
        if offer is not None:
            chosen.append({"line": line, "offer": offer})

    if not chosen:
        return _error(
            request, "Choose at least one unit before sending the request.",
            back=f"/request/{request_id}/select",
        )

    return TEMPLATES.TemplateResponse(
        request=request, name="order_received.html",
        context={"request_row": row, "chosen": chosen},
    )


@router.post("/match", response_class=HTMLResponse)
def create_match(
    request: Request,
    request_id: int = Form(...),
    offer_id: int = Form(...),
) -> HTMLResponse:
    row = repo.load_request(request_id)
    offer = repo.load_offer(offer_id)
    if row is None or offer is None:
        return _error(request, "That request or offer no longer exists.")
    facility = repo.load_facility(row.facility_slug)

    candidates = [c for c in rank_candidates(facility, [offer]) if c.offer.id == offer_id]
    if not candidates:
        return _error(request, "That device no longer passes the screening for this facility.")

    try:
        wanted = next(
            (l.quantity for l in row.lines if l.device_type == offer.device_type),
            offer.quantity_available,
        )
        quantity = min(wanted, offer.quantity_available)
        match = request_confirmation(propose_match(row, candidates[0], quantity))
        match_id = repo.save_match(match)
        repo.update_offer(offer.reserve(quantity))
    except (ValueError, RepositoryError) as exc:
        return _error(request, str(exc), back=f"/request/{request_id}/matches")

    return RedirectResponse(url=f"/match/{match_id}", status_code=303)


@router.get("/match/{match_id}", response_class=HTMLResponse)
def match_sent(request: Request, match_id: str) -> HTMLResponse:
    match = repo.load_match(match_id)
    if match is None:
        return _error(request, f"No match with id {match_id}.")
    offer = repo.load_offer(match.offer_id)
    return TEMPLATES.TemplateResponse(
        request=request, name="match_sent.html",
        context={"match": match, "offer": offer},
    )


# ---- donor confirmation ------------------------------------------------

@router.get("/confirm/{token}", response_class=HTMLResponse)
def confirm_page(request: Request, token: str) -> HTMLResponse:
    match = repo.load_match_by_token(token)
    if match is None:
        return _error(request, "That confirmation link is not valid.")
    offer = repo.load_offer(match.offer_id)
    row = repo.load_request(match.request_id)
    facility = repo.load_facility(row.facility_slug) if row else None
    return TEMPLATES.TemplateResponse(
        request=request, name="confirm.html",
        context={
            "match": match, "offer": offer, "facility": facility,
            "trust": repo.load_verification(row.facility_slug) if row else VerificationStatus(),
        },
    )


@router.post("/confirm/{token}", response_class=HTMLResponse)
def confirm_submit(request: Request, token: str, answer: str = Form(...)) -> HTMLResponse:
    match = repo.load_match_by_token(token)
    if match is None:
        return _error(request, "That confirmation link is not valid.")
    offer = repo.load_offer(match.offer_id)
    now = datetime.now()

    try:
        if answer == "yes":
            updated = confirm(match, now)
        else:
            updated = withdraw(match, now)
            if offer is not None:
                repo.update_offer(offer.release(match.quantity))
        repo.update_match_state(match.id, updated.state, now)
    except (ValueError, RepositoryError) as exc:
        return _error(request, str(exc))

    return RedirectResponse(url=f"/confirm/{token}", status_code=303)


# ---- device photographs --------------------------------------------------

# Shown when an offer has no photograph, or the store cannot be reached. A
# drawn placeholder rather than a broken image, so a row never collapses.
PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120" '
    b'viewBox="0 0 160 120" role="img" aria-label="No photograph">'
    b'<rect width="160" height="120" fill="#19202C"/>'
    b'<rect x="40" y="36" width="80" height="52" rx="4" fill="none" '
    b'stroke="#39455C" stroke-width="2"/>'
    b'<circle cx="80" cy="62" r="13" fill="none" stroke="#39455C" stroke-width="2"/>'
    b'<text x="80" y="106" text-anchor="middle" font-family="monospace" '
    b'font-size="10" fill="#6B7A94">no photo</text></svg>'
)
PLACEHOLDER_TYPE = "image/svg+xml"
PHOTO_CACHE_SECONDS = 3600


@router.get("/photo/{offer_id}")
def device_photo(offer_id: int) -> Response:
    """Stream a donor's photograph back through the app.

    The bucket stays private: nothing here hands out a public or
    pre-authenticated URL, so there is no link to expire and no object a
    stranger can enumerate. A missing key or an unreachable store returns the
    placeholder rather than an error, because a photograph is never the reason
    a clinic cannot see an offer.
    """
    offer = repo.load_offer(offer_id)
    found = fetch_photo(offer.photo_key) if offer and offer.photo_key else None
    if found is None:
        return Response(content=PLACEHOLDER_SVG, media_type=PLACEHOLDER_TYPE)

    raw, content_type = found
    return Response(
        content=raw,
        media_type=content_type,
        headers={"Cache-Control": f"private, max-age={PHOTO_CACHE_SECONDS}"},
    )


# ---- live extraction ----------------------------------------------------

# The five readings a rating plate carries, in the order it prints them.
INSPECTOR_ROWS = (
    ("Model", "model"),
    ("Voltage", "voltage"),
    ("Frequency", "frequency"),
    ("Power", "power"),
    ("Operating temp", "temperature"),
)


def _inspector_rows(device: DeviceFacts) -> list[dict[str, str]]:
    """The extracted facts as rows, each carrying where it came from.

    An unknown field is still a row. Showing it blank with an `unverified` tag
    is the whole point: the gap is the finding.
    """
    supply = _range_text(device.voltage_min_v, device.voltage_max_v, "V")
    frequency = (
        "/".join(str(v) for v in device.frequencies_hz.value) + " Hz"
        if device.frequencies_hz.known else ""
    )
    power = f"{device.power_watts.value} W" if device.power_watts.known else ""
    temperature = _range_text(device.temp_min_c, device.temp_max_c, "\u00b0C")

    values = {
        "model": (device.label if device.model.known or device.manufacturer.known else "",
                  device.model if device.model.known else device.manufacturer),
        "voltage": (supply, device.voltage_min_v),
        "frequency": (frequency, device.frequencies_hz),
        "power": (power, device.power_watts),
        "temperature": (temperature, device.temp_min_c),
    }

    rows = []
    for label, key in INSPECTOR_ROWS:
        shown, fact_used = values[key]
        rows.append({
            "label": label,
            "value": shown,
            "source": fact_used.source.value if shown else "unverified",
            "known": bool(shown),
        })
    return rows


def _range_text(low, high, unit: str) -> str:
    if low.known and high.known:
        return f"{low.value}\u2013{high.value} {unit}"
    if low.known:
        return f"from {low.value} {unit}"
    if high.known:
        return f"to {high.value} {unit}"
    return ""


@router.post("/offer/extract")
async def offer_extract(request: Request) -> JSONResponse:
    """Extract facts from what the donor has typed or photographed, without a reload.

    Called by the inspector panel on the offer page. Never raises at the
    client: a model failure comes back as an error string the panel shows in
    place of the rows, because a blank panel would look like a device with no
    facts rather than a service that did not answer.
    """
    form = await request.form()
    combined = str(form.get("spec_text") or "").strip()

    image_data_url = ""
    upload = form.get("nameplate")
    if upload is not None and getattr(upload, "filename", ""):
        image_data_url = image_to_data_url(
            await upload.read(), getattr(upload, "content_type", "") or ""
        )

    if not combined and not image_data_url:
        return JSONResponse({"rows": [], "message": "Nothing to read yet."})

    try:
        device = extract_device(typed_text=combined, image_data_url=image_data_url)
    except (GenAiError, ValueError) as exc:
        return JSONResponse({"rows": [], "message": str(exc)}, status_code=200)

    return JSONResponse({"rows": _inspector_rows(device), "message": ""})


# ---- recipient verification --------------------------------------------

# Nominatim wants an ISO 3166-1 alpha-2 code to scope a search to one country.
COUNTRY_CODES = {
    "uganda": "ug", "kenya": "ke", "haiti": "ht", "nepal": "np",
    "nigeria": "ng", "ghana": "gh", "tanzania": "tz", "malawi": "mw",
    "india": "in", "bangladesh": "bd", "ethiopia": "et", "zambia": "zm",
}


@router.get("/review", response_class=HTMLResponse)
def review_list(request: Request) -> HTMLResponse:
    """Every registered clinic and how far its claim has been checked."""
    facilities = repo.list_facilities()
    statuses = repo.verifications_for([f.slug for f in facilities])
    return TEMPLATES.TemplateResponse(
        request=request, name="review_list.html",
        context={
            "facilities": facilities,
            "statuses": statuses,
            "default_status": VerificationStatus(),
        },
    )


@router.get("/review/{slug}", response_class=HTMLResponse)
def review_facility(request: Request, slug: str, check: int = 0) -> HTMLResponse:
    """One clinic, with registry evidence if the reviewer asked for it.

    The lookup only runs on `?check=1` so that opening the page never waits on
    a third-party service, and so a reviewer chooses to make the request.
    """
    facility = repo.load_facility(slug)
    if facility is None:
        return _error(request, f"No facility profile for {slug}.")

    candidates: tuple = ()
    if check:
        candidates = find_candidates(
            facility.name,
            COUNTRY_CODES.get(facility.country.strip().lower(), ""),
            facility.latitude,
            facility.longitude,
        )

    return TEMPLATES.TemplateResponse(
        request=request, name="review_facility.html",
        context={
            "facility": facility,
            "status": repo.load_verification(slug),
            "candidates": candidates,
            "checked": bool(check),
            "corroborates": corroborates,
            "supported": any(corroborates(c) for c in candidates),
        },
    )


@router.post("/review/{slug}", response_class=HTMLResponse)
def review_decide(
    request: Request,
    slug: str,
    decision: str = Form(...),
    note: str = Form(default=""),
) -> HTMLResponse:
    """Record a reviewer's decision. Only a person moves a clinic's tier."""
    try:
        tier = Verification(decision)
    except ValueError:
        return _error(request, f"{decision!r} is not a verification tier.")
    if repo.load_facility(slug) is None:
        return _error(request, f"No facility profile for {slug}.")

    repo.set_verification(slug, tier, forms.text(note, "Note", limit=400))
    return RedirectResponse(url=f"/review/{slug}", status_code=303)


# ---- helpers -----------------------------------------------------------

def _point(country: str, city: str) -> GeoPoint:
    latitude, longitude = (country_centroid(country) or (None, None))
    return GeoPoint(country=country, city=(city or "").strip(),
                    latitude=latitude, longitude=longitude)


def _blocked_for(facility: FacilityProfile, offers: list) -> list[dict]:
    """Offers the checks rejected, each with the first reason they were rejected.

    rank_candidates drops these on purpose, so they are re-screened here rather
    than changing what ranking means.
    """
    from app.checks.runner import check_fit
    from app.domain.device import SupplyIntel

    rejected = []
    for offer in offers:
        verdict = check_fit(offer.device, facility, SupplyIntel(country=facility.country))
        if verdict.outcome is not Outcome.WONT_WORK:
            continue
        reason = next((r for r in verdict.results if r.blocking), None)
        rejected.append({
            "offer": offer,
            "label": reason.label if reason else "Screening",
            "detail": reason.detail if reason else "",
        })
    return rejected


def _lines_from(types: list[str], quantities: list[str]) -> tuple[RequestLine, ...]:
    """Build the request lines from the form's parallel device/quantity lists.

    Blank rows are dropped so an empty extra row the clinic never filled in is
    not an error. A request with nothing left after that is refused.
    """
    lines = []
    for position, raw_type in enumerate(types):
        if not raw_type.strip():
            continue
        raw_quantity = quantities[position] if position < len(quantities) else "1"
        lines.append(
            RequestLine(
                device_type=raw_type,
                quantity=forms.whole_number(
                    raw_quantity, "How many", default=1, minimum=1
                ),
            )
        )
    if not lines:
        raise FormError("Name at least one kind of equipment you need.")
    return tuple(lines)


def _with_expiry(device: DeviceFacts, expiry: date | None) -> DeviceFacts:
    """Attach the donor's expiry date, leaving it unknown when none was given."""
    if expiry is None:
        return device
    return replace(device, expiry_date=fact(expiry, Provenance.DONOR_STATED))


def _device_pairs(offer: Offer) -> list[tuple[str, str]]:
    from app.web.presenters import identity_pairs
    return identity_pairs(offer.device)


def _support_kind(device_type: str, label: str) -> str:
    """Ask the model which support need this item meets, if any.

    Never blocks the donor: a model failure falls back to keywords inside
    categorise_support, and an uncategorised offer is still findable by the
    requirement's own search terms.
    """
    kind = categorise_support(device_type, label)
    return kind.value if kind else ""
