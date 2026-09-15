"""
The dashboard server.

    python app.py
    http://127.0.0.1:8000/whole-chicken/1/1

Renders on request and returns the HTML directly - nothing is written to disk.
A URL is the whole interface, which is what makes this usable as the link a
Power Automate flow emails after a submission lands.
"""

from __future__ import annotations

import html

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from ovendash.render import render
from ovendash.source import SubmissionNotFound, build_source

app = FastAPI(title="Oven Run Dashboard", docs_url=None, redoc_url=None)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    """Minimal chrome for the index and error pages."""
    return HTMLResponse(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
 body{{font:14px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;max-width:680px;
      margin:60px auto;padding:0 20px;color:#16191c;background:#eef1f4}}
 code{{font-family:ui-monospace,Consolas,monospace;background:#dde3e9;
       padding:1px 5px;border-radius:3px}}
 a{{color:#1f5f8b}} li{{margin:5px 0}}
 .box{{background:#fff;border:1px solid #ccd3da;padding:18px 22px}}
 @media (prefers-color-scheme:dark){{
   body{{background:#12151a;color:#e8ecf0}} code{{background:#262d36}}
   .box{{background:#1a1f26;border-color:#333c47}} a{{color:#63a8d4}}}}
</style></head><body><div class="box">{body}</div></body></html>""",
        status_code=status,
    )


@app.get("/", response_class=HTMLResponse)
def index():
    source = build_source()
    mode = (
        "live — reading SharePoint through Graph"
        if source.name == "graph"
        else "sample data — Graph consent still pending, serving local fixtures"
    )

    links = ""
    lister = getattr(source, "list_available", None)
    if lister:
        rows = lister()
        if rows:
            links = "<p>Available now:</p><ul>" + "".join(
                '<li><a href="/{slug}/{tr}/{tn}">{food} — {tr}/{tn}</a></li>'.format(
                    slug=html.escape(r["slug"]),
                    tr=html.escape(r["test_request_num"]),
                    tn=html.escape(r["test_num"]),
                    food=html.escape(r["food"]),
                )
                for r in rows
            ) + "</ul>"

    return _page(
        "Oven Run Dashboard",
        f"""<h1>Oven Run Dashboard</h1>
<p>Open a dashboard by URL:</p>
<p><code>/{{food}}/{{test-request-num}}/{{test-num}}</code></p>
<p>e.g. <code>/whole-chicken/1/1</code></p>
{links}
<p style="color:#5a636b;margin-top:22px">Source: {mode}</p>""",
    )


@app.get("/favicon.ico")
def favicon():
    return HTMLResponse("", status_code=204)


@app.get("/{food}/{test_request_num}/{test_num}", response_class=HTMLResponse)
def dashboard(food: str, test_request_num: str, test_num: str):
    source = build_source()
    try:
        submission = source.get(food, test_request_num, test_num)
    except SubmissionNotFound as exc:
        return _page(
            "Not found",
            f"""<h1>No such submission</h1>
<p>{html.escape(str(exc))}</p>
<p><a href="/">Back</a></p>""",
            status=404,
        )

    # Raw log parsing lands here once Graph can fetch the file; until then the
    # chart panel renders its own explanation rather than a blank frame.
    try:
        from ovendash.rawdata import attach_raw_data

        attach_raw_data(submission, source)
    except Exception:
        pass

    return HTMLResponse(render(submission, source=source, source_name=source.name))


@app.get("/{food}/{test_request_num}", response_class=HTMLResponse)
def dashboard_missing_testnum(food: str, test_request_num: str):
    return RedirectResponse("/")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
