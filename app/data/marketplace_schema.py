"""Marketplace tables for the two-sided donation platform.

These are defined on the same METADATA as the single-device tables, so the
existing ``init_db()`` creates them with everything else. Every string column
states an explicit length: SQLite ignores lengths, MySQL refuses VARCHAR
without one, and the deployment target is MySQL.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Table,
    Text,
    func,
)

from app.data.db import METADATA

# Column widths. Named so the repository and the web layer can validate input
# against the same numbers instead of guessing them.
SLUG_LENGTH = 64
NAME_LENGTH = 200
EMAIL_LENGTH = 254  # RFC 5321 maximum for a full address.
COUNTRY_LENGTH = 100
CITY_LENGTH = 120
DEVICE_LABEL_LENGTH = 200
DEVICE_TYPE_LENGTH = 120
SUPPORT_KIND_LENGTH = 40
STATUS_LENGTH = 40
MATCH_ID_LENGTH = 12
TOKEN_LENGTH = 64

DEFAULT_QUANTITY = 1
PHOTO_KEY_LENGTH = 128
NO_QUANTITY = 0

EMPTY_JSON_OBJECT = "{}"

facilities_v2 = Table(
    "facilities_v2",
    METADATA,
    Column("slug", String(SLUG_LENGTH), primary_key=True),
    Column("name", String(NAME_LENGTH), nullable=False),
    Column("country", String(COUNTRY_LENGTH), nullable=False),
    # Denormalised out of profile_json so the matcher can filter and sort by
    # distance in SQL without parsing every stored profile.
    Column("latitude", Float, nullable=True),
    Column("longitude", Float, nullable=True),
    Column("profile_json", Text, nullable=False),
    # How hard anyone has looked before believing this clinic exists. Kept out
    # of profile_json because the clinic writes the profile and a reviewer
    # writes this, and a column can be listed and filtered on.
    Column("verification", String(STATUS_LENGTH), nullable=False, default="entered on the form"),
    Column("verification_note", Text, nullable=False, default=""),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

offers = Table(
    "offers",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("donor_name", String(NAME_LENGTH), nullable=False),
    Column("donor_email", String(EMAIL_LENGTH), nullable=False),
    Column("device_label", String(DEVICE_LABEL_LENGTH), nullable=False, index=True),
    Column("device_type", String(DEVICE_TYPE_LENGTH), nullable=False, default="", index=True),
    Column("support_kind", String(SUPPORT_KIND_LENGTH), nullable=False, default="", index=True),
    Column("device_json", Text, nullable=False),
    Column("country", String(COUNTRY_LENGTH), nullable=False),
    Column("city", String(CITY_LENGTH), nullable=False, default=""),
    Column("latitude", Float, nullable=True),
    Column("longitude", Float, nullable=True),
    Column("quantity_offered", Integer, nullable=False, default=DEFAULT_QUANTITY),
    Column("quantity_reserved", Integer, nullable=False, default=NO_QUANTITY),
    Column("quantity_shipped", Integer, nullable=False, default=NO_QUANTITY),
    Column("available_until", Date, nullable=True),
    # Object Storage key for the donor's photograph. Blank when none was given
    # or the upload failed; the page falls back to a placeholder either way.
    Column("photo_key", String(PHOTO_KEY_LENGTH), nullable=False, default=""),
    Column("status", String(STATUS_LENGTH), nullable=False, index=True),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

requests = Table(
    "requests",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("facility_slug", String(SLUG_LENGTH), nullable=False, index=True),
    Column("note", Text, nullable=False, default=""),
    Column("status", String(STATUS_LENGTH), nullable=False, index=True),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

# One row per kind of equipment on a request. A clinic needing two ventilators
# and five monitors is the normal case, so the quantity lives here and the
# request itself holds only what is true of the whole ask.
request_lines = Table(
    "request_lines",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("request_id", Integer, nullable=False, index=True),
    Column("device_type", String(DEVICE_TYPE_LENGTH), nullable=False, index=True),
    Column("quantity", Integer, nullable=False, default=DEFAULT_QUANTITY),
    Column("position", Integer, nullable=False, default=0),
)

matches = Table(
    "matches",
    METADATA,
    Column("id", String(MATCH_ID_LENGTH), primary_key=True),
    Column("request_id", Integer, nullable=False, index=True),
    Column("offer_id", Integer, nullable=False, index=True),
    Column("quantity", Integer, nullable=False, default=DEFAULT_QUANTITY),
    Column("distance_km", Float, nullable=True),
    Column("state", String(STATUS_LENGTH), nullable=False, index=True),
    # The donor's confirm link carries this and nothing else, so it is the
    # hottest lookup in the app and is both unique and indexed.
    Column("token", String(TOKEN_LENGTH), nullable=False, unique=True, index=True),
    Column("verdict_json", Text, nullable=False),
    Column("intel_json", Text, nullable=False, default=EMPTY_JSON_OBJECT),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Column("responded_at", DateTime, nullable=True),
)
