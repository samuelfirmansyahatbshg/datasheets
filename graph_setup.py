"""
One-time Microsoft Graph auth check + discovery.

Uses the Microsoft Graph PowerShell public client, which is a Microsoft-published
multi-tenant app - so this needs NO Azure AD app registration of your own.

Run it once to sign in. The token is cached in .token_cache.json next to this file,
so later runs (and the dashboard tool) reuse it silently until it expires.

    python graph_setup.py
"""

import atexit
import json
import os

import msal
import requests

from ovendash.config import load_env

load_env()

# Microsoft Graph PowerShell SDK's own client id - published by Microsoft,
# multi-tenant, not a secret.
CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
AUTHORITY = os.environ.get(
    "OVENDASH_AUTHORITY", "https://login.microsoftonline.com/organizations"
)
SCOPES = ["Sites.Read.All", "Files.Read.All"]

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, ".token_cache.json")

# The site holding the two lists, addressed the way Graph wants it:
#   {hostname}:{server-relative-path}
# Read from the environment - this repo is public and the real value embeds a
# tenant hostname and a user's UPN.
SITE_PATH = os.environ.get("OVENDASH_SITE_PATH", "")

GRAPH = "https://graph.microsoft.com/v1.0"


def _load_cache():
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE_PATH):
        cache.deserialize(open(CACHE_PATH, "r", encoding="utf-8").read())

    def _save():
        if cache.has_state_changed:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                f.write(cache.serialize())

    atexit.register(_save)
    return cache


def get_token():
    """A Graph access token, from cache if possible, otherwise via device code."""
    app = msal.PublicClientApplication(
        CLIENT_ID, authority=AUTHORITY, token_cache=_load_cache()
    )

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            print(f"Reusing cached token for {accounts[0].get('username')}")
            return result["access_token"]

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise SystemExit(
            "Could not start device flow:\n" + json.dumps(flow, indent=2)
        )

    print()
    print(flow["message"])
    print()
    print("Waiting for you to finish signing in...")

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print("\nAUTH FAILED")
        print("  error:      ", result.get("error"))
        print("  description:", result.get("error_description"))
        print()
        print("If this mentions consent or an administrator, that is the IT ask:")
        print("  admin consent for Sites.Read.All and Files.Read.All")
        print("  on app 14d82eec-204b-4c2f-b7e8-296a70dab67e (Microsoft Graph PowerShell)")
        raise SystemExit(1)

    return result["access_token"]


def main():
    if not SITE_PATH:
        raise SystemExit(
            "OVENDASH_SITE_PATH is not set.\n"
            "Copy .env.example to .env and fill it in, e.g.\n"
            "  OVENDASH_SITE_PATH=contoso-my.sharepoint.com:/personal/user_contoso_com"
        )

    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    print("\nAUTH OK\n")

    print(f"Resolving site {SITE_PATH} ...")
    r = requests.get(f"{GRAPH}/sites/{SITE_PATH}", headers=headers, timeout=30)
    print("  status:", r.status_code)
    if not r.ok:
        print(r.text[:800])
        raise SystemExit(1)

    site = r.json()
    site_id = site["id"]
    print("  name:   ", site.get("displayName") or site.get("name"))
    print("  site id:", site_id)

    print("\nLists on this site:")
    r = requests.get(f"{GRAPH}/sites/{site_id}/lists", headers=headers, timeout=30)
    print("  status:", r.status_code)
    if not r.ok:
        print(r.text[:800])
        raise SystemExit(1)

    wanted = {"Pending Scoring", "Test Submissions"}
    found = {}
    for lst in r.json().get("value", []):
        name = lst.get("displayName")
        marker = "  <-- needed" if name in wanted else ""
        print(f"  - {name}  |  {lst.get('id')}{marker}")
        if name in wanted:
            found[name] = lst["id"]

    print()
    missing = wanted - set(found)
    if missing:
        print("MISSING:", ", ".join(sorted(missing)))
        print("(check the exact list names in SharePoint)")
    else:
        print("Both lists found. Graph is ready.")
        print(json.dumps({"site_id": site_id, "lists": found}, indent=2))


if __name__ == "__main__":
    main()
