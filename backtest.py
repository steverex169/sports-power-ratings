"""Backtest harness: is the rating actually predictive, and does it beat the market?

The honest version of this question needs *point-in-time* inputs — what the
model would have known on the morning of the game, not what we know now.
ESPN's core API archives FPI as it stood each week, so predictions can be
replayed with no lookahead. Every run audits that claim (see `--audit`) rather
than assuming it.

    .venv/bin/python backtest.py                     # NFL + CFB, last 3 seasons
    .venv/bin/python backtest.py --league nfl        # one league
    .venv/bin/python backtest.py --years 2023-2025
    .venv/bin/python backtest.py --audit             # lookahead check only

What it can and cannot answer, stated up front:

  * CAN  — how much predictive power FPI itself carries, and how fast a frozen
           preseason rating decays against a weekly-refreshed one. That is the
           evidence for or against decaying the preseason tilts.
  * CAN  — for the NFL, how the rating compares to the closing spread, which is
           the only benchmark that matters (nflverse publishes closing lines
           free, back to 1999).
  * CANNOT — validate the curated tilts (CFB portal, QB status, NFL roster
           adjustments). Those are human judgement calls recorded for 2026
           only; there is no historical series to replay them against.
  * CANNOT — score CFB against the market. No free source of historical CFB
           closing lines exists; CFBD has them behind a (free) API key.
"""
import argparse
import csv
import datetime
import io
import math
import os
import statistics
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import data_sources as ds
import fallback_data as fb

# ESPN stamps its "week N" power index AFTER week N is played, so week N must be
# predicted from week N-1's file; week 1 uses the preseason (types/1) release.
# `seasons/{yr}/powerindex` and `types/2/weeks/0` are end-of-season files — using
# either to predict that same season is lookahead. The audit enforces all of this.
_CORE = ("https://sports.core.api.espn.com/v2/sports/football/leagues/{lg}/"
         "seasons/{yr}/types/2/weeks/{wk}/powerindex?limit=400")
_CORE_PRE = ("https://sports.core.api.espn.com/v2/sports/football/leagues/{lg}/"
             "seasons/{yr}/types/1/weeks/1/powerindex?limit=400")
_NFLVERSE = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

LEAGUES = {
    "nfl": {"espn": "nfl", "weeks": range(1, 19), "extra": "",
            "hfa": fb.NFL_HFA_DEFAULT, "market": True},
    "cfb": {"espn": "college-football", "weeks": range(1, 16), "extra": "&groups=80",
            "hfa": fb.CFB_HFA_DEFAULT, "market": False},
}

# ESPN abbreviation -> nflverse abbreviation (the only two that differ)
NV_ALIAS = {"LAR": "LA", "WSH": "WAS"}


# ---------------- point-in-time inputs ----------------

def _parse_fpi(data):
    out, published = {}, None
    for it in data.get("items", []):
        tid = it["team"]["$ref"].split("/teams/")[1].split("?")[0]
        published = published or it.get("lastUpdated")
        for p in it.get("predictives", []):
            if p.get("name") == "fpi" and p.get("value") is not None:
                out[tid] = float(p["value"])
    return out, published


def _dt(iso):
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


def preseason_fpi(lg, yr):
    """The preseason release — what the model would have had before week 1."""
    cfg = LEAGUES[lg]
    data, _ = ds.http_get_json(_CORE_PRE.format(lg=cfg["espn"], yr=yr),
                               f"bt_fpi_{lg}_{yr}_pre")
    return _parse_fpi(data)


def fpi_asof(lg, yr, wk, before=None):
    """FPI genuinely knowable before week `wk` kicks off.

    Normally that is the week N-1 file, but ESPN sometimes republishes an early
    week at season's end (CFB week 1 in both 2024 and 2025 carries a December
    timestamp), which would smuggle final ratings into a week-2 prediction. So
    when `before` — the week's first kickoff — is supplied, walk back through
    earlier files and fall back to the preseason release, taking the first one
    actually published in time.
    """
    cfg = LEAGUES[lg]
    for src in range(wk - 1, 0, -1):
        data, _ = ds.http_get_json(_CORE.format(lg=cfg["espn"], yr=yr, wk=src),
                                   f"bt_fpi_{lg}_{yr}_w{src}")
        fpi, published = _parse_fpi(data)
        if not fpi:
            continue
        if before is None or (published and _dt(published) < before):
            return fpi, published
    return preseason_fpi(lg, yr)


def results_week(lg, yr, wk):
    """Final scores for a week, keyed by ESPN team id (no name matching)."""
    cfg = LEAGUES[lg]
    data, _ = ds.http_get_json(
        ds._SB_URL.format(league=cfg["espn"], year=yr, week=wk, extra=cfg["extra"]),
        f"bt_res_{lg}_{yr}_w{wk}")
    games = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FINAL":
            continue
        home = away = None
        for c in comp["competitors"]:
            try:
                score = int(c.get("score"))
            except (TypeError, ValueError):
                continue
            rec = {"id": c["team"]["id"], "abbr": c["team"].get("abbreviation"),
                   "name": c["team"].get("shortDisplayName"), "score": score}
            if c.get("homeAway") == "home":
                home = rec
            else:
                away = rec
        if not home or not away:
            continue
        games.append({"year": yr, "week": wk, "home": home, "away": away,
                      "margin": home["score"] - away["score"],
                      "neutral": bool(comp.get("neutralSite")),
                      "kickoff": ev.get("date")})
    return games


def nfl_closing_lines():
    """{(season, week, home_nv, away_nv): spread} from nflverse — positive means
    the home team is favoured by that many. Regular season only."""
    path = os.path.join(ds.CACHE_DIR, "bt_nflverse_games.csv")
    if not os.path.exists(path):
        req = urllib.request.Request(_NFLVERSE, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            open(path, "w").write(r.read().decode())
    lines = {}
    for r in csv.DictReader(io.StringIO(open(path).read())):
        if r.get("game_type") != "REG" or not r.get("spread_line"):
            continue
        try:
            lines[(int(r["season"]), int(r["week"]), r["home_team"], r["away_team"])] = \
                float(r["spread_line"])
        except ValueError:
            continue
    return lines


# ---------------- scoring ----------------

def _stats(errors):
    if not errors:
        return {"n": 0}
    return {"n": len(errors),
            "mae": statistics.mean(abs(e) for e in errors),
            "rmse": math.sqrt(statistics.mean(e * e for e in errors)),
            "bias": statistics.mean(errors)}


def replay(lg, years, verbose=True):
    """Predict every completed game from point-in-time FPI and from a frozen
    week-1 FPI, and (NFL) compare both against the closing line."""
    cfg = LEAGUES[lg]
    lines = nfl_closing_lines() if cfg["market"] else {}
    live_err, frozen_err, market_err = [], [], []
    picks = []   # (edge_vs_line, won_ats) for every priced game
    by_week = {}
    skipped_no_fpi = skipped_no_line = 0

    for yr in years:
        frozen, _ = preseason_fpi(lg, yr)
        for wk in cfg["weeks"]:
            games = results_week(lg, yr, wk)
            if not games:
                continue
            first = min((_dt(g["kickoff"]) for g in games if g.get("kickoff")),
                        default=None)
            fpi, _ = fpi_asof(lg, yr, wk, before=first)
            if not fpi:
                continue
            for g in games:
                hid, aid = g["home"]["id"], g["away"]["id"]
                hfa = 0.0 if g["neutral"] else cfg["hfa"]
                actual = g["margin"]
                if hid not in fpi or aid not in fpi:
                    skipped_no_fpi += 1
                    continue
                pred = fpi[hid] - fpi[aid] + hfa
                live_err.append(pred - actual)
                w = by_week.setdefault(wk, {"live": [], "frozen": [], "mkt": []})
                w["live"].append(pred - actual)
                if hid in frozen and aid in frozen:
                    fpred = frozen[hid] - frozen[aid] + hfa
                    frozen_err.append(fpred - actual)
                    w["frozen"].append(fpred - actual)
                if cfg["market"]:
                    key = (yr, wk,
                           NV_ALIAS.get(g["home"]["abbr"], g["home"]["abbr"]),
                           NV_ALIAS.get(g["away"]["abbr"], g["away"]["abbr"]))
                    if key in lines:
                        spread = lines[key]
                        market_err.append(spread - actual)
                        w["mkt"].append(spread - actual)
                        if actual != spread:  # pushes are returned, not graded
                            took_home = pred > spread
                            home_covered = actual > spread
                            picks.append((abs(pred - spread),
                                          int(took_home == home_covered)))
                    else:
                        skipped_no_line += 1

    return {"live": _stats(live_err), "frozen": _stats(frozen_err),
            "market": _stats(market_err), "by_week": by_week,
            "picks": picks,
            "skipped": (skipped_no_fpi, skipped_no_line)}


def audit_lookahead(lg, years):
    """Confirm each week's FPI was published before that week's first kickoff.
    A backtest that fails this is scoring itself on answers it already had."""
    import datetime
    cfg, bad, checked = LEAGUES[lg], [], 0
    for yr in years:
        for wk in cfg["weeks"]:
            games = results_week(lg, yr, wk)
            kicks = [g["kickoff"] for g in games if g.get("kickoff")]
            if not kicks:
                continue
            first = _dt(min(kicks))
            _, published = fpi_asof(lg, yr, wk, before=first)
            if not published:
                continue
            pub = _dt(published)
            checked += 1
            if pub >= first:
                bad.append((yr, wk, published, min(kicks)))
    return checked, bad


# ---------------- reporting ----------------

def _fmt(s, label):
    if not s.get("n"):
        return f"  {label:<26} — no data"
    return (f"  {label:<26} n={s['n']:<5} MAE {s['mae']:5.2f}  "
            f"RMSE {s['rmse']:5.2f}  bias {s['bias']:+5.2f}")


def report(lg, years, res):
    print(f"\n{'='*66}\n{lg.upper()}  {years[0]}-{years[-1]}\n{'='*66}")
    print(_fmt(res["live"], "FPI, point-in-time"))
    print(_fmt(res["frozen"], "FPI, frozen at preseason"))
    if res["market"].get("n"):
        print(_fmt(res["market"], "closing line (benchmark)"))
    live, frozen = res["live"], res["frozen"]
    if live.get("n") and frozen.get("n"):
        d = frozen["mae"] - live["mae"]
        print(f"\n  Staleness cost: the frozen preseason rating is {d:+.2f} pts/game worse"
              f" ({d/live['mae']*100:+.1f}% MAE).")
    if res["market"].get("n"):
        gap = live["mae"] - res["market"]["mae"]
        print(f"  Market gap: FPI is {gap:+.2f} pts/game worse than the closing line.")
    picks = res.get("picks") or []
    if picks:
        print("\n  ATS by size of disagreement with the closing line"
              " (break-even at -110 is 52.4%):")
        for lo in (0, 1, 2, 3, 4, 6):
            sel = [w for e, w in picks if e >= lo]
            if len(sel) < 30:
                continue
            hits, n = sum(sel), len(sel)
            flag = "" if n < 100 else ("  <-- edge" if hits / n >= 0.524 else "")
            print(f"    edge >= {lo:<2} n={n:<5} {hits}-{n-hits}"
                  f"  {hits/n*100:5.1f}%{flag}")
    nf, nl = res["skipped"]
    if nf or nl:
        print(f"  Skipped: {nf} games without an FPI entry, {nl} without a closing line.")

    weeks = sorted(res["by_week"])
    if weeks:
        print("\n  MAE by week (live vs frozen):")
        for wk in weeks:
            w = res["by_week"][wk]
            lv, fz = _stats(w["live"]), _stats(w["frozen"])
            if not lv.get("n"):
                continue
            bar = f"{lv['mae']:5.2f}"
            fzs = f"{fz['mae']:5.2f}" if fz.get("n") else "  -  "
            gap = f"{fz['mae']-lv['mae']:+5.2f}" if fz.get("n") else "     "
            print(f"    wk {wk:<2} n={lv['n']:<4} live {bar}   frozen {fzs}   gap {gap}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=["nfl", "cfb", "both"], default="both")
    ap.add_argument("--years", default="2023-2025",
                    help="season range, e.g. '2023-2025' or '2024'")
    ap.add_argument("--audit", action="store_true",
                    help="only run the lookahead audit")
    args = ap.parse_args()

    if "-" in args.years:
        lo, hi = args.years.split("-", 1)
        years = list(range(int(lo), int(hi) + 1))
    else:
        years = [int(args.years)]
    leagues = ["nfl", "cfb"] if args.league == "both" else [args.league]

    for lg in leagues:
        if args.audit:
            checked, bad = audit_lookahead(lg, years)
            print(f"\n{lg.upper()} lookahead audit: {checked} weeks checked, {len(bad)} suspect")
            for yr, wk, pub, kick in bad[:10]:
                print(f"  {yr} wk{wk}: FPI published {pub} but first kickoff {kick}")
            continue
        print(f"\nReplaying {lg.upper()} {years[0]}-{years[-1]} "
              f"(cached after the first run)…")
        report(lg, years, replay(lg, years))


if __name__ == "__main__":
    main()
