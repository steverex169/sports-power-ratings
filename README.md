# Sports Power Ratings 2026

Automated CFB + NFL power-ratings dashboard. A GitHub Action rebuilds it daily
from US-based runners (avoiding regional API blocks) and publishes it to
GitHub Pages.

## How it works

```
ESPN FPI + schedules (live APIs)
CFBD talent/returning production (live, if CFBD_API_KEY secret is set)
curated snapshots (portal, QB status, win totals — fallback_data.py)
        │
        ▼
build.py ──► models.py ──► index.html (self-contained dashboard) + CSVs
```

- **`build.py`** — one-command pipeline: fetch → model → generate `index.html`
- **`data_sources.py`** — fetchers with live → disk-cache → snapshot resilience
- **`models.py`** — CFB (talent/portal/returning-production) and NFL
  (regression/QB/roster) rating models
- **`fallback_data.py`** — curated annual inputs; edit these once a year
- **`template.html`** — dashboard UI; `build.py` injects fresh JSON into it

## Local build

```bash
python3 -m venv .venv && .venv/bin/pip install numpy pandas
.venv/bin/python build.py --open
```

## Automation

`.github/workflows/build.yml` rebuilds and redeploys the dashboard:
- daily on a schedule,
- on every push to `main`,
- on demand via the **Actions → Run workflow** button.

Optional: add a `CFBD_API_KEY` repository secret (free key from
collegefootballdata.com) to switch talent + returning production from
snapshot to live.

*For research and entertainment only — not betting advice.*
