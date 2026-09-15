"""
Submission -> HTML.

Field ordering in the two JSON panels is deliberate: the blobs come out of
Power Automate in insertion order, which is already the order the questions
appear on the form, so they are rendered as-is rather than sorted. That keeps
the page reading like the form it came from.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .submission import Submission

TEMPLATES = Path(__file__).resolve().parent / "templates"

# Attachments that are images and worth embedding.
IMAGE_FIELDS = (
    "PhotoTop",
    "PhotoBreast",
    "PhotoThigh",
    "DigiEyePhotoOriginal",
    "DigiEyeIndexMap",
)

# File answers are shown by filename in their own panels, not as raw JSON.
_HIDE_FROM_TABLES = set(Submission.ATTACHMENT_FIELDS)

# Test-method constants, straight off the workbook's scoring tab. These are
# properties of how the test is run, not answers, so the Form never asks for
# them - but the sheet shows them, so the dashboard does too.
FOOD_CONSTANTS = {
    "Whole Chicken": {
        "brand": "Whole Chicken (any brand that meets portion requirements)",
        "pan": "Broil Pan with Drip Tray",
        "reference_time": "13 - 17 minutes",
    },
}

# Fields each section places by hand; anything else falls through to that
# section's "Other" table so a newly added field is never silently dropped.
_FORM_PLACED = {
    "TestRequestNum", "TestNum",
    "TemperatureNarrative", "ChickenColorNarrative",
    "MeatTexture", "MeatTextureNotes",
    "SkinEvenness", "SkinEvennessNotes",
    "SkinTexture", "SkinTextureNotes",
    "TimeResult",
    "ThighOvenBack", "BreastOvenBack", "ThighOvenFront", "BreastOvenFront",
    "PanWeight", "PanAndDrippings", "PackageWeight",
    "WeightAfterCleaning", "WeightAfterCooking",
    "DigiEyeMean", "IndexScore",
}

_CALC_PLACED = {
    "IndexScore", "ThighScore", "BreastScore", "MeatTextureScore",
    "EvennessScore", "TextureScore", "TimeScore",
    "TemperatureGroup", "QualityGroup", "TotalScore",
    "ThighReading", "BreastReading",
    "AmbientTempC", "WaterUsed",
    "Drippings", "PercentDripLoss", "WeightDifference", "PercentLoss",
    "CookingTimeSeconds", "CookingTimeMinutes", "TransferTimeSeconds",
    "ReferenceMinPerLb", "ActualMinPerLb",
}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _humanise(key: str) -> str:
    """'ThighOvenBack' -> 'Thigh Oven Back'; keeps acronyms intact."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return spaced


def _pairs(blob: dict) -> list:
    """Readable (label, value) pairs, attachments omitted."""
    return [
        (_humanise(k), v)
        for k, v in blob.items()
        if k not in _HIDE_FROM_TABLES
    ]


def _leftovers(blob: dict, placed: set) -> list:
    """Fields the layout does not place by hand, so nothing goes missing."""
    return [
        (_humanise(k), v)
        for k, v in blob.items()
        if k not in placed and k not in _HIDE_FROM_TABLES
    ]


def template_for(food_type: str) -> str:
    """Per-food template, falling back to Whole Chicken's layout.

    A missing template is not an error worth a 500: an unfamiliar food still
    has scores and photos worth showing, and the fallback makes that visible
    rather than hiding it behind a stack trace.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (food_type or "").lower()).strip("_")
    candidate = TEMPLATES / f"{slug}.html.j2"
    return candidate.name if candidate.exists() else "whole_chicken.html.j2"


def _data_uri(raw: bytes, name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "jpg"
    mime = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def fetch_images(submission: Submission, source) -> dict:
    """Download and inline the photos, when the source can.

    Any single failure is swallowed to a missing key - one unreachable photo
    must not take the whole dashboard down, and the template already renders a
    labelled gap for anything absent.
    """
    images = {}
    downloader = getattr(source, "download", None)
    if downloader is None:
        return images

    for field in IMAGE_FIELDS:
        att = submission.attachments.get(field)
        if not att or not att.present:
            continue
        try:
            images[field] = _data_uri(downloader(att.drive_id, att.item_id), att.name)
        except Exception:
            continue
    return images


def render(submission: Submission, *, source=None, source_name: str = "sample") -> str:
    images = fetch_images(submission, source) if source else {}

    def energy(key):
        """Formatted energy value, or None so the template shows its own note."""
        value = submission.energy.get(key)
        return f"{value:,.1f}" if isinstance(value, (int, float)) else None

    constants = FOOD_CONSTANTS.get(submission.food_type, {})

    env = _env()
    template = env.get_template(template_for(submission.food_type))
    return template.render(
        s=submission,
        images=images,
        source_name=source_name,
        series=submission.raw_series,
        scoring_display=_pairs(submission.scoring),
        calculated_display=_pairs(submission.calculated),
        info_display=_pairs(submission.info),
        formresp_extra=_leftovers(submission.scoring, _FORM_PLACED),
        calculated_extra=_leftovers(submission.calculated, _CALC_PLACED),
        food_brand=constants.get("brand"),
        food_pan=constants.get("pan"),
        food_reference_time=constants.get("reference_time"),
        energy_preheat=energy("preheat_energy"),
        energy_cooking=energy("cooking_energy"),
        energy_total=energy("total_energy"),
        energy_voltage=energy("voltage"),
    )
