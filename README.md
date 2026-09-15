# oven-run-dashboard

Per-submission dashboards for oven food tests. Open a URL, get that test's
dashboard rendered on the spot — nothing is written to disk.

```
http://127.0.0.1:8000/whole-chicken/1/1
                      └─ food      └┴─ test request # / test #
```

## Why this exists

The test pipeline is Microsoft Forms → Power Automate → SharePoint. Two forms
(a universal Information Sheet and a per-food scoring form) are joined on
Test Request # + Test #, scored in the flow, and written to two SharePoint
lists.

Rendering the result was the open problem. Power BI can't do per-record
dashboards at any tier — reports are templates you filter, not artifacts you
mint per row — and its paginated-report export path needs Premium capacity
this tenant doesn't grant. Import-mode refresh also lags by hours, so "submit
a form, see your dashboard" isn't achievable there.

So: a small renderer. No license, no refresh lag, one file per request.

## Running

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:8000/>. Sample data needs no configuration.

For live data, copy `.env.example` to `.env` and fill it in. Deployment
specifics — tenant, site path, list names — are read from the environment
rather than committed, because this repo is public and the real site path
embeds a tenant hostname and a user's UPN.

```
OVENDASH_SITE_PATH=contoso-my.sharepoint.com:/personal/user_contoso_com
```

## Data sources

`ovendash/source.py` picks one automatically:

| | |
|---|---|
| **SampleSource** | reads `samples/*.json`. No auth. Active now. |
| **GraphSource** | reads both SharePoint lists via Microsoft Graph. |

GraphSource activates on its own once a usable token is cached — no code
change. Until then the app serves fixtures and says so in the page header.

### Graph auth status: **pending admin consent**

`python graph_setup.py` runs device-code sign-in against the Microsoft Graph
PowerShell public client (`14d82eec-204b-4c2f-b7e8-296a70dab67e`), which needs
no app registration of our own. Device flow itself is permitted on the tenant —
verified, a code is issued.

What's blocked is consent. Signing in returns:

> Request pending — your admin has been notified of your request to access this
> app.

The ask is **admin consent for `Sites.Read.All` and `Files.Read.All`** on that
app. Once approved, rerun `graph_setup.py` once and everything downstream
works unchanged.

## Layout

```
app.py                  FastAPI server; URL -> HTML
graph_setup.py          one-time auth + list discovery
ovendash/
  submission.py         the model; unwraps the nested JSON
  source.py             SampleSource / GraphSource behind one interface
  rawdata.py            log parsing, chart series, energy lookups
  render.py             Jinja2 -> HTML, images inlined as data URIs
  templates/
    base.html.j2        shell, theming
    _chart.html.j2      canvas chart with toggleable legend
    whole_chicken.html.j2
samples/
  whole-chicken-1-1.json
```

### Per food

One template per food, chosen by `FoodType` and falling back to Whole
Chicken's layout. Each rubric differs enough that one template full of
conditionals would be worse than a copy. Add `turkey.html.j2` for Turkey.

### The chart

Hand-written canvas rather than a charting library, because the page has to
stay self-contained with no CDN. Time (min) on X, temperature and power on
separate Y axes, clickable legend, hover readout. Counter columns (the log's
row index) ship switched off so they don't flatten the real traces.

## Scoring

Mirrors `PCGDEOV-22325-6 Whole Chicken.xlsx`:

- Seven criterion scores: Index, Temp thigh, Temp breast, Meat texture,
  Evenness, Skin texture, Time
- Two are reduced: Temperature = `MIN(thigh, breast)`,
  Quality = `MIN(meat texture, evenness, skin texture)`
- **Total** = Index + Temperature + Quality + Time, out of **12**
- Acceptable above 9

> The reference screenshot says "out of 21" — that's the older Mehring-lineage
> template, which summed all seven. The authoritative workbook sums the four
> group values to 12, and that's what the flow's `TotalScore` computes. Flagged
> in case 21 is actually wanted.

## Raw log

`rawdata.py` ports three lookups that Power Automate couldn't do — it can't
read an uploaded file's contents, so these stayed blank in the flow:

```
Voltage        = INDEX(RawData!C:C, MATCH(3.01, RawData!B:B, 1))
Preheat Energy = INDEX(RawData!E:E, MATCH(preheat_decimal_min, RawData!B:B, 1))
Total Energy   = LOOKUP(2, 1/(RawData!E:E<>""), RawData!E:E)
```

`MATCH(...,1)` means *last row ≤ target*, not nearest — treating it as nearest
silently shifts the reading by a row, so `_match_le` implements it exactly.

The uploaded log is an `.xlsx` in every sample seen so far despite the form
question being called "Raw Data CSV"; both are handled.

## Known issues

- **`CookingTimeSeconds` comes back 0** from the flow even when the Info Sheet
  holds `00:30:00` — the `contains(..., ':')` guard in `CalculatedData` isn't
  firing. This tool recomputes from source so the dashboard is right, but the
  SharePoint value stays wrong until the flow expression is fixed.
- Photos and chart stay empty until Graph consent lands; the page says so
  rather than rendering blank frames.

## Security

This repo is public. Two things stay out of it:

- **`.token_cache.json`** — a live credential. Gitignored.
- **`.env`** — tenant hostname, site path, UPN. Gitignored; see `.env.example`.

The sample fixture carries placeholder `driveId`/item IDs rather than real
Graph handles. Sample mode never downloads, so nothing is lost by that.

The client id in `graph_setup.py` is Microsoft's own published multi-tenant
app id, not a secret.
