"""Intake: turning a clinic's wish-list into request lines.

A spreadsheet is parsed deterministically; typed text goes through the model
with a deterministic fallback, so an outage never stops a clinic filing a
request. Neither path decides whether an item suits the facility - that is
app.checks' job, against facts rather than wording.
"""

from app.intake.freetext import MAX_ITEMS, parse_list
from app.intake.tables import MAX_ROWS, IntakeError, parse_table

__all__ = [
    "IntakeError",
    "MAX_ITEMS",
    "MAX_ROWS",
    "parse_list",
    "parse_table",
]
