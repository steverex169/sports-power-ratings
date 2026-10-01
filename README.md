# Sports Power Ratings 2026

Automated CFB + NFL power-ratings dashboard covering the **full regular
season** — CFB weeks 0-15 and NFL weeks 1-18, with a projected line for every
game. A GitHub Action rebuilds it weekly from US-based runners (avoiding
regional API blocks) and publishes it to GitHub Pages.

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

## Week coverage

`build.py` pulls one ESPN scoreboard request per week and the dashboard builds
its week filter from whatever comes back, so gaps are self-describing:

| League | Weeks | Notes |
|---|---|---|
| CFB | 0-15 | ESPN folds the late-August Week 0 games into its week 1; they are relabeled week 0. Week 14 (conference championships) stays empty until the matchups are decided — ESPN carries them as `TBD` and unrateable games are skipped, with the count shown in the build report. |
| NFL | 1-18 | Full regular season. |

Narrow the slate when iterating — each week is a separate fetch:

```bash
.venv/bin/python build.py --cfb-weeks 1-4 --nfl-weeks 1,2
```

Both flags accept `all` (default), a range (`1-15`), a list (`1,2,5`), or a
combination (`0-2,14`). Every week's projected line uses the same preseason
rating — results are not fed back in during the season.

## Local build

```bash
python3 -m venv .venv && .venv/bin/pip install numpy pandas
.venv/bin/python build.py --open
```

## Automation

`.github/workflows/build.yml` rebuilds and redeploys the dashboard:
- weekly on a schedule (Mondays, after the post-weekend FPI refresh),
- on every push to `main`,
- on demand via the **Actions → Run workflow** button.

Optional: add a `CFBD_API_KEY` repository secret (free key from
collegefootballdata.com) to switch talent + returning production from
snapshot to live.

*For research and entertainment only — not betting advice.*
