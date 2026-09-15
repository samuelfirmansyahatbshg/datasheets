"""
Where a submission comes from.

Two sources behind one interface, so the renderer never knows which it got:

  SampleSource  - reads samples/*.json. Works today, no auth.
  GraphSource   - reads both SharePoint lists via Graph. Needs admin consent
                  for Sites.Read.All / Files.Read.All, currently pending.

The app picks GraphSource when a cached token exists and falls back to
SampleSource otherwise, so the dashboard keeps rendering either way and the
switchover needs no code change.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .config import load_env
from .submission import Submission

load_env()

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SAMPLES = REPO / "samples"

GRAPH = "https://graph.microsoft.com/v1.0"

# Which SharePoint site holds the two lists, as Graph addresses it:
#   {hostname}:{server-relative-path}
# Kept out of source because this repo is public and the path embeds a tenant
# hostname and a user's UPN. Set it in .env or the environment.
SITE_PATH = os.environ.get("OVENDASH_SITE_PATH", "")

# Microsoft Graph PowerShell's own client id - published by Microsoft and
# multi-tenant, so no app registration of ours is needed. Not a secret.
CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
AUTHORITY = os.environ.get(
    "OVENDASH_AUTHORITY", "https://login.microsoftonline.com/organizations"
)
SCOPES = ["Sites.Read.All", "Files.Read.All"]

SCORING_LIST = os.environ.get("OVENDASH_SCORING_LIST", "Pending Scoring")
INFO_LIST = os.environ.get("OVENDASH_INFO_LIST", "Test Submissions")


class SubmissionNotFound(Exception):
    """No submission matched the food type and test numbers requested."""


def _slug(text: str) -> str:
    """'Whole Chicken' -> 'whole-chicken', for URLs and sample filenames."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


class SampleSource:
    """Local fixtures. Filename convention: {food-slug}-{testrequest}-{testnum}.json"""

    name = "sample"

    def get(self, food: str, test_request_num: str, test_num: str) -> Submission:
        path = SAMPLES / f"{_slug(food)}-{test_request_num}-{test_num}.json"
        if not path.exists():
            available = sorted(p.name for p in SAMPLES.glob("*.json"))
            raise SubmissionNotFound(
                f"No sample at {path.name}. Available: {', '.join(available) or 'none'}"
            )
        row = json.loads(path.read_text(encoding="utf-8"))
        return Submission.from_row(row)

    def list_available(self) -> list:
        out = []
        for path in sorted(SAMPLES.glob("*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out.append(
                {
                    "food": row.get("FoodType", ""),
                    "slug": _slug(row.get("FoodType", "")),
                    "test_request_num": str(row.get("TestRequestNum", "")),
                    "test_num": str(row.get("TestNum", "")),
                }
            )
        return out


class GraphSource:
    """SharePoint via Microsoft Graph.

    Reads Pending Scoring for the scoring + calculated halves, then Test
    Submissions for the Info Sheet half, joined on TestRequestNum + TestNum.
    """

    name = "graph"

    def __init__(self, token: str):
        self.headers = {"Authorization": f"Bearer {token}"}
        self._site_id = None
        self._list_ids = {}

    # -- lazy discovery, so construction never does network work ----------

    def _site(self) -> str:
        if self._site_id is None:
            import requests

            if not SITE_PATH:
                raise SubmissionNotFound(
                    "OVENDASH_SITE_PATH is not set - see .env.example"
                )
            r = requests.get(f"{GRAPH}/sites/{SITE_PATH}", headers=self.headers, timeout=30)
            r.raise_for_status()
            self._site_id = r.json()["id"]
        return self._site_id

    def _list_id(self, display_name: str) -> str:
        if display_name not in self._list_ids:
            import requests

            r = requests.get(
                f"{GRAPH}/sites/{self._site()}/lists", headers=self.headers, timeout=30
            )
            r.raise_for_status()
            for lst in r.json().get("value", []):
                self._list_ids[lst.get("displayName")] = lst["id"]
        if display_name not in self._list_ids:
            raise SubmissionNotFound(f"List not found on site: {display_name}")
        return self._list_ids[display_name]

    def _items(self, list_name: str) -> list:
        """Every row of a list, fields expanded, following pagination."""
        import requests

        url = (
            f"{GRAPH}/sites/{self._site()}/lists/{self._list_id(list_name)}"
            "/items?expand=fields&$top=200"
        )
        rows = []
        while url:
            r = requests.get(url, headers=self.headers, timeout=60)
            r.raise_for_status()
            payload = r.json()
            rows.extend(item.get("fields", {}) for item in payload.get("value", []))
            url = payload.get("@odata.nextLink")
        return rows

    @staticmethod
    def _matches(row: dict, food: str, trn: str, tn: str) -> bool:
        if str(row.get("TestRequestNum", "")) != str(trn):
            return False
        if str(row.get("TestNum", "")) != str(tn):
            return False
        # Food only constrains the scoring list; the Info list has no FoodType.
        if food and row.get("FoodType"):
            return _slug(row["FoodType"]) == _slug(food)
        return True

    def get(self, food: str, test_request_num: str, test_num: str) -> Submission:
        scoring_rows = [
            r
            for r in self._items(SCORING_LIST)
            if self._matches(r, food, test_request_num, test_num)
        ]
        if not scoring_rows:
            raise SubmissionNotFound(
                f"No {SCORING_LIST} row for {food} {test_request_num}/{test_num}"
            )
        # Most recent wins if a test was submitted twice.
        row = scoring_rows[-1]

        info_rows = [
            r
            for r in self._items(INFO_LIST)
            if self._matches(r, "", test_request_num, test_num)
        ]
        info_row = info_rows[-1] if info_rows else None

        return Submission.from_row(row, info_row=info_row)

    def download(self, drive_id: str, item_id: str) -> bytes:
        import requests

        r = requests.get(
            f"{GRAPH}/drives/{drive_id}/items/{item_id}/content",
            headers=self.headers,
            timeout=120,
        )
        r.raise_for_status()
        return r.content


def build_source():
    """GraphSource if a usable cached token exists, else SampleSource.

    Deliberately silent about failure: a missing or expired token is the
    expected state while consent is pending, not an error worth crashing on.
    """
    cache_path = REPO / ".token_cache.json"
    if not cache_path.exists():
        return SampleSource()

    try:
        import atexit

        import msal

        cache = msal.SerializableTokenCache()
        cache.deserialize(cache_path.read_text(encoding="utf-8"))
        atexit.register(
            lambda: cache_path.write_text(cache.serialize(), encoding="utf-8")
            if cache.has_state_changed
            else None
        )

        app = msal.PublicClientApplication(
            CLIENT_ID, authority=AUTHORITY, token_cache=cache
        )
        accounts = app.get_accounts()
        if not accounts:
            return SampleSource()

        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            return GraphSource(result["access_token"])
    except Exception:
        pass

    return SampleSource()
