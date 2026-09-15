"""
One submission, assembled from the pieces SharePoint stores it in.

A row in `Pending Scoring` carries three JSON blobs as *strings*, and inside
FormResponseJSON the file-upload answers are themselves JSON strings holding a
one-element array. So parsing is two levels deep in places, and every value
arrives as text - including the numbers. This module does that unwrapping once
so nothing downstream has to think about it.

The Info Sheet half lives in a different list (`Test Submissions`), joined on
TestRequestNum + TestNum. A submission is only complete when both halves are
present; `info` is an empty dict rather than None when the Info row is missing,
so templates can render a partial dashboard instead of failing outright.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _loads(raw: Any) -> dict:
    """Parse a JSON string into a dict, tolerating None/blank/already-parsed."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass
class Attachment:
    """A file answer from a Forms upload question.

    `drive_id` + `item_id` are what Graph needs to download the bytes
    (`/drives/{drive_id}/items/{item_id}/content`) - far more reliable than
    parsing the sharing URL, which is why they are kept separately from `link`.
    """

    name: str = ""
    link: str = ""
    item_id: str = ""
    drive_id: str = ""
    size: int = 0

    @property
    def present(self) -> bool:
        return bool(self.item_id and self.drive_id)

    @classmethod
    def from_field(cls, raw: Any) -> "Attachment | None":
        """Forms stores these as a JSON string holding a list of one file."""
        if not raw:
            return None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return None
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            return None

        first = raw[0]
        if not isinstance(first, dict):
            return None

        return cls(
            name=first.get("name") or "",
            link=first.get("link") or "",
            item_id=first.get("id") or "",
            drive_id=first.get("driveId") or "",
            size=first.get("size") or 0,
        )


def _num(value: Any, default: float = 0.0) -> float:
    """Text -> float. Everything from the JSON blobs arrives as a string."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


@dataclass
class Submission:
    food_type: str = ""
    test_request_num: str = ""
    test_num: str = ""
    timestamp: str = ""

    scoring: dict = field(default_factory=dict)      # FormResponseJSON
    calculated: dict = field(default_factory=dict)   # CalculatedDataJSON
    info: dict = field(default_factory=dict)         # InfoDataJSON

    attachments: dict = field(default_factory=dict)  # name -> Attachment

    # Filled by rawdata.py once the log file is downloadable. Until then the
    # template shows these as unavailable rather than inventing numbers.
    raw_series: list = field(default_factory=list)
    energy: dict = field(default_factory=dict)

    ATTACHMENT_FIELDS = (
        "PhotoTop",
        "PhotoBreast",
        "PhotoThigh",
        "DigiEyePhotoOriginal",
        "DigiEyeIndexMap",
        "RawDataCSV",
    )

    @classmethod
    def from_row(cls, row: dict, info_row: dict | None = None) -> "Submission":
        """Build from a Pending Scoring row, optionally with its Test Submissions match.

        `info_row` is separate because it comes from the other list; when the
        row itself already carries InfoDataJSON (as the offline fixture does),
        that is used instead.
        """
        scoring = _loads(row.get("FormResponseJSON"))
        calculated = _loads(row.get("CalculatedDataJSON"))

        info_raw = row.get("InfoDataJSON")
        if not info_raw and info_row:
            info_raw = info_row.get("InfoDataJSON")
        info = _loads(info_raw)

        attachments = {}
        for name in cls.ATTACHMENT_FIELDS:
            found = Attachment.from_field(scoring.get(name))
            if found:
                attachments[name] = found

        return cls(
            food_type=row.get("FoodType") or "",
            test_request_num=str(row.get("TestRequestNum") or ""),
            test_num=str(row.get("TestNum") or ""),
            timestamp=row.get("Timestamp") or "",
            scoring=scoring,
            calculated=calculated,
            info=info,
            attachments=attachments,
        )

    # ---- convenience for templates -------------------------------------

    @property
    def title(self) -> str:
        return f"Final Assessment of {self.food_type}" if self.food_type else "Final Assessment"

    @property
    def label(self) -> str:
        return f"{self.test_request_num} / {self.test_num}"

    def calc(self, key: str, default: float = 0.0) -> float:
        return _num(self.calculated.get(key), default)

    @property
    def total_score(self) -> float:
        return self.calc("TotalScore")

    @property
    def verdict(self) -> str:
        """Acceptable above 9, matching the >9 threshold every workbook uses.

        Blank rather than a guess when no score has been computed - an
        unscored run must not read as a failed one.
        """
        if self.calculated.get("TotalScore") in (None, ""):
            return ""
        return "Acceptable" if self.total_score > 9 else "Unacceptable"

    @property
    def score_row(self) -> list:
        """The seven individual criterion scores, in the order the sheet lists them."""
        return [
            ("Index", self.calc("IndexScore")),
            ("Temp thigh", self.calc("ThighScore")),
            ("Temp breast", self.calc("BreastScore")),
            ("Meat texture", self.calc("MeatTextureScore")),
            ("Evenness", self.calc("EvennessScore")),
            ("Skin texture", self.calc("TextureScore")),
            ("Time", self.calc("TimeScore")),
        ]

    @property
    def minimum_row(self) -> list:
        """The four group scores that actually sum to the total.

        Temperature is MIN(thigh, breast); Quality is MIN(meat, evenness,
        texture). Index and Time pass through. This is the row the workbook
        adds up - the seven above are inputs to it, not addends.
        """
        return [
            ("Index", self.calc("IndexScore")),
            ("Temperature", self.calc("TemperatureGroup")),
            ("Quality", self.calc("QualityGroup")),
            ("Time", self.calc("TimeScore")),
        ]
