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
    .venv/bin/python backtest.py --tilts             # do the NFL tilts earn their place?
    .venv/bin/python backtest.py --fit-reg           # what RegTilt's coefficients should be
    .venv/bin/python backtest.py --box               # does box-score luck beat the market?

What it can and cannot answer, stated up front:

  * CAN  — how much predictive power FPI itself carries, and how fast a frozen
           preseason rating decays against a weekly-refreshed one. That is the
           evidence for or against decaying the preseason tilts.
  * CAN  — for the NFL, how the rating compares to the closing spread, which is
           the only benchmark that matters (nflverse publishes closing lines
           free, back to 1999).
  * CAN  — rebuild and score the NFL RegTilt exactly (it is a formula over the
           prior season) and approximate the QB adjustment from nflverse depth
           charts, then ask whether either beats plain FPI. See `--tilts`.
  * CANNOT — validate NFL RosterAdj or the CFB QB designation. Those are
           judgement calls with no historical series behind them.
  * CANNOT — score CFB against the market, or replay the CFB portal tilt,
           without a CFBD API key. The key is free and covers both: /lines
           carries spread and spreadOpen, /player/portal is by season.
"""
import argparse
import csv
import datetime
import io
import math
import os
import statistics
import sys
import time
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
        comp = next(iter(ev.get("competitions") or []), None)
        if not comp or not comp.get("competitors"):
            continue
        if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FINAL":
            continue
        home = away = None
        for c in comp["competitors"]:
            try:
                score = int(c.get("score"))
                c["team"]["id"]
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
    ap.add_argument("--tilts", action="store_true",
                    help="NFL only: score the reconstructable tilts against the market")
    ap.add_argument("--fit-reg", action="store_true",
                    help="NFL only: fit the RegTilt coefficients, scored out of sample")
    ap.add_argument("--box", action="store_true",
                    help="NFL only: does fading box-score luck beat FPI and the market?")
    ap.add_argument("--hfa", action="store_true",
                    help="fit the home-field constant from results")
    args = ap.parse_args()

    if "-" in args.years:
        lo, hi = args.years.split("-", 1)
        years = list(range(int(lo), int(hi) + 1))
    else:
        years = [int(args.years)]
    leagues = ["nfl", "cfb"] if args.league == "both" else [args.league]

    if args.hfa:
        for lg in leagues:
            report_hfa(lg, years, fit_hfa(lg, years))
        return

    if args.box:
        report_fit_box(fit_box(years))
        return

    if args.fit_reg:
        report_fit_reg(fit_reg(years))
        return

    if args.tilts:
        v, m, p = replay_nfl_model(years)
        report_nfl_model(years, v, m, p)
        return

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



# ---------------- NFL tilt reconstruction ----------------
#
# The dashboard's NFL tilts are curated for 2026 only, but two of the three are
# formulas over the prior season, so they can be rebuilt for any year and
# replayed honestly. Verified against the curated 2026 snapshot (fallback_data
# NFL_T, built off 2025): point differential and wins match all 32 teams
# exactly, Pythagorean luck at exponent 2.37 matches within 0.04, and turnover
# margin matches 29/32. RosterAdj stays a judgement call with no series to
# replay, so it is left out rather than guessed at.

_NV_TEAMSTATS = ("https://github.com/nflverse/nflverse-data/releases/download/"
                 "stats_team/stats_team_reg_{yr}.csv")
_NV_DEPTH = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "depth_charts/depth_charts_{yr}.csv")
PYTH_EXP = 2.37
BETA_TO, BETA_LUCK, CAP_REG = 0.06, 0.40, 2.5
NEW_QB_ADJ = -1.3   # the curated model's "clear new-QB downgrade" midpoint


def _csv_cached(url, name, retries=5):
    """nflverse assets are large and GitHub occasionally 503s; cache and retry."""
    path = os.path.join(ds.CACHE_DIR, name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return open(path).read()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                text = r.read().decode(errors="replace")
            open(path, "w").write(text)
            return text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{name}: {last}")


def espn_to_nflverse(years):
    """{espn_team_id: nflverse abbrev} — the join between ESPN and nflverse."""
    out = {}
    for yr in years:
        for wk in (1, 2, 3):
            for g in results_week("nfl", yr, wk):
                for side in ("home", "away"):
                    t = g[side]
                    out[t["id"]] = NV_ALIAS.get(t["abbr"], t["abbr"])
        if len(out) >= 32:
            break
    return out


def nfl_reg_inputs(year):
    """{nv_abbrev: (turnover_margin, luck_wins)} for `year`, from `year - 1`."""
    prior = year - 1
    rows = list(csv.DictReader(io.StringIO(
        _csv_cached(_NV_TEAMSTATS.format(yr=prior), f"bt_teamstats_{prior}.csv"))))
    I = lambda r, k: int(float(r.get(k) or 0))  # noqa: E731
    to_margin = {r["team"]: (I(r, "def_interceptions") + I(r, "fumble_recovery_opp"))
                            - (I(r, "passing_interceptions") + I(r, "fumbles_lost_total"))
                 for r in rows}

    pf, pa, wins, played = {}, {}, {}, {}
    for r in csv.DictReader(io.StringIO(
            _csv_cached(_NFLVERSE, "bt_nflverse_games.csv"))):
        if r["season"] != str(prior) or r.get("game_type") != "REG" or not r.get("result"):
            continue
        hs, as_ = int(float(r["home_score"])), int(float(r["away_score"]))
        for team, sf, sa in ((r["home_team"], hs, as_), (r["away_team"], as_, hs)):
            pf[team] = pf.get(team, 0) + sf
            pa[team] = pa.get(team, 0) + sa
            played[team] = played.get(team, 0) + 1
            wins[team] = wins.get(team, 0) + (1 if sf > sa else 0.5 if sf == sa else 0)

    out = {}
    for team in pf:
        pyth = played[team] * pf[team] ** PYTH_EXP / (pf[team] ** PYTH_EXP + pa[team] ** PYTH_EXP)
        out[team] = (to_margin.get(team, 0), wins[team] - pyth)
    return out


def reg_tilt(to, luck, b_to=BETA_TO, b_luck=BETA_LUCK):
    return max(-CAP_REG, min(CAP_REG, -(b_to * to + b_luck * luck)))


def nfl_reg_tilt(year):
    """RegTilt for `year`, rebuilt from `year - 1` results: {nv_abbrev: tilt}."""
    return {t: reg_tilt(to, luck) for t, (to, luck) in nfl_reg_inputs(year).items()}


def nfl_qb_adj(year):
    """{(nv_abbrev, week): adj} — a downgrade in any week the listed QB1 is not
    the one the team opened the season with. A mechanical stand-in for the
    curated QB call, using that model's own magnitude."""
    rows = list(csv.DictReader(io.StringIO(
        _csv_cached(_NV_DEPTH.format(yr=year), f"bt_depth_{year}.csv"))))
    qb1 = {}
    for r in rows:
        if r.get("position") != "QB" or str(r.get("depth_team")) != "1":
            continue
        if r.get("game_type") not in (None, "", "REG"):
            continue
        try:
            wk = int(float(r["week"]))
        except (KeyError, TypeError, ValueError):
            continue
        name = r.get("full_name") or r.get("football_name")
        if name:
            qb1[(r["club_code"], wk)] = name

    opener = {}
    for (team, wk), name in sorted(qb1.items(), key=lambda kv: kv[0][1]):
        opener.setdefault(team, name)
    return {k: (0.0 if v == opener.get(k[0]) else NEW_QB_ADJ) for k, v in qb1.items()}


def fit_hfa(lg, years):
    """What home-field edge do the results actually imply?

    The rating difference is taken as given and only the constant is fitted, on
    non-neutral games. Two answers are reported because they optimise different
    things: the mean residual zeroes the bias (least squares), while the sweep
    minimises MAE, which is what the dashboard's lines are judged on. Neutral
    games are scored separately as a control — a correct fit leaves them near
    zero, since no home edge should apply there.
    """
    cfg = LEAGUES[lg]
    home, neutral = [], []
    for yr in years:
        for wk in cfg["weeks"]:
            games = results_week(lg, yr, wk)
            if not games:
                continue
            first = min((_dt(g["kickoff"]) for g in games if g.get("kickoff")), default=None)
            fpi, _ = fpi_asof(lg, yr, wk, before=first)
            for g in games:
                hid, aid = g["home"]["id"], g["away"]["id"]
                if hid not in fpi or aid not in fpi:
                    continue
                resid = g["margin"] - (fpi[hid] - fpi[aid])
                (neutral if g["neutral"] else home).append(resid)
    if not home:
        return None
    best, best_mae = None, None
    for step in range(0, 81):
        cand = step * 0.1
        mae = statistics.mean(abs(r - cand) for r in home)
        if best_mae is None or mae < best_mae:
            best, best_mae = cand, mae
    return {"n": len(home), "mean": statistics.mean(home), "median": statistics.median(home),
            "mae_opt": best, "mae_at_opt": best_mae,
            "current": cfg["hfa"],
            "mae_at_current": statistics.mean(abs(r - cfg["hfa"]) for r in home),
            "neutral_n": len(neutral),
            "neutral_mean": statistics.mean(neutral) if neutral else None}


def report_hfa(lg, years, f):
    print(f"\n{'='*66}\n{lg.upper()} HOME-FIELD FIT  {years[0]}-{years[-1]}\n{'='*66}")
    if not f:
        print("  no data")
        return
    print(f"  non-neutral games          n={f['n']}")
    print(f"  current constant           {f['current']:.2f}  -> MAE {f['mae_at_current']:.3f}")
    print(f"  bias-zeroing (mean resid)  {f['mean']:.2f}")
    print(f"  median residual            {f['median']:.2f}")
    print(f"  MAE-minimising             {f['mae_opt']:.2f}  -> MAE {f['mae_at_opt']:.3f}")
    print(f"  MAE gained by refitting    {f['mae_at_current'] - f['mae_at_opt']:+.3f} pts/game")
    if f["neutral_mean"] is not None:
        print(f"  control: neutral-site mean residual {f['neutral_mean']:+.2f}"
              f" over {f['neutral_n']} games (should sit near 0)")


def replay_nfl_model(years):
    """Score FPI alone against FPI + the reconstructable tilts, both versus the
    closing line."""
    cfg = LEAGUES["nfl"]
    lines = nfl_closing_lines()
    id2nv = espn_to_nflverse(years)
    variants = {"FPI alone": [], "FPI + RegTilt": [], "FPI + RegTilt + QB": []}
    market, picks = [], {k: [] for k in variants}

    for yr in years:
        reg = nfl_reg_tilt(yr)
        qbs = nfl_qb_adj(yr)
        for wk in cfg["weeks"]:
            games = results_week("nfl", yr, wk)
            if not games:
                continue
            first = min((_dt(g["kickoff"]) for g in games if g.get("kickoff")), default=None)
            fpi, _ = fpi_asof("nfl", yr, wk, before=first)
            for g in games:
                hid, aid = g["home"]["id"], g["away"]["id"]
                if hid not in fpi or aid not in fpi:
                    continue
                hnv, anv = id2nv.get(hid), id2nv.get(aid)
                key = (yr, wk, hnv, anv)
                if key not in lines:
                    continue
                spread, actual = lines[key], g["margin"]
                hfa = 0.0 if g["neutral"] else cfg["hfa"]
                tilt = lambda nv, with_qb: (  # noqa: E731
                    reg.get(nv, 0.0) + (qbs.get((nv, wk), 0.0) if with_qb else 0.0))
                preds = {
                    "FPI alone": fpi[hid] - fpi[aid] + hfa,
                    "FPI + RegTilt": (fpi[hid] + tilt(hnv, False)) - (fpi[aid] + tilt(anv, False)) + hfa,
                    "FPI + RegTilt + QB": (fpi[hid] + tilt(hnv, True)) - (fpi[aid] + tilt(anv, True)) + hfa,
                }
                market.append(spread - actual)
                for name, pred in preds.items():
                    variants[name].append(pred - actual)
                    if actual != spread:
                        picks[name].append((abs(pred - spread),
                                            int((pred > spread) == (actual > spread))))
    return variants, market, picks


def report_nfl_model(years, variants, market, picks):
    print(f"\n{'='*66}\nNFL MODEL vs MARKET  {years[0]}-{years[-1]}\n{'='*66}")
    for name, errs in variants.items():
        print(_fmt(_stats(errs), name))
    print(_fmt(_stats(market), "closing line (benchmark)"))
    print("\n  ATS by size of disagreement with the closing line (break-even 52.4%):")
    print(f"    {'variant':<22}{'edge>=1':>14}{'edge>=2':>14}{'edge>=3':>14}")
    for name, ps in picks.items():
        cells = []
        for lo in (1, 2, 3):
            sel = [w for e, w in ps if e >= lo]
            cells.append(f"{sum(sel)}-{len(sel)-sum(sel)} {sum(sel)/len(sel)*100:.1f}%"
                         if len(sel) >= 30 else "-")
        print(f"    {name:<22}{cells[0]:>14}{cells[1]:>14}{cells[2]:>14}")


# ---------------- RegTilt coefficient fit ----------------
#
# 0.06 and 0.40 were set by hand. This asks what the data would have chosen,
# and — the part that matters — whether the choice holds up on seasons it was
# not fitted on. Each season is scored with coefficients fitted only on the
# seasons before it (walk-forward), so the out-of-sample line is what fitting
# would actually have bought. The tilt is applied exactly as the live model
# does: per-team cap, then the in-season decay from playrules.

B_TO_GRID = [i * 0.01 for i in range(0, 16)]      # 0 to 0.15 per turnover
B_LUCK_GRID = [i * 0.05 for i in range(0, 21)]    # 0 to 1.0 per luck win


def reg_fit_rows(years):
    """Every priced game with what the tilt needs: one row per game."""
    import playrules
    cfg = LEAGUES["nfl"]
    lines = nfl_closing_lines()
    id2nv = espn_to_nflverse(years)
    rows = []
    for yr in years:
        inp = nfl_reg_inputs(yr)
        for wk in cfg["weeks"]:
            games = results_week("nfl", yr, wk)
            if not games:
                continue
            first = min((_dt(g["kickoff"]) for g in games if g.get("kickoff")), default=None)
            fpi, _ = fpi_asof("nfl", yr, wk, before=first)
            for g in games:
                hid, aid = g["home"]["id"], g["away"]["id"]
                hnv, anv = id2nv.get(hid), id2nv.get(aid)
                key = (yr, wk, hnv, anv)
                if hid not in fpi or aid not in fpi or key not in lines:
                    continue
                rows.append({"yr": yr, "base": fpi[hid] - fpi[aid] + (0.0 if g["neutral"] else cfg["hfa"]),
                             "h": inp.get(hnv, (0, 0.0)), "a": inp.get(anv, (0, 0.0)),
                             "decay": playrules.decay("nfl", wk),
                             "spread": lines[key], "actual": g["margin"]})
    return rows


def _score(rows, b_to, b_luck):
    """MAE against the result, and ATS on plays clearing the 1-point bar."""
    err, w, n = 0.0, 0, 0
    for r in rows:
        pred = r["base"] + r["decay"] * (reg_tilt(*r["h"], b_to, b_luck)
                                         - reg_tilt(*r["a"], b_to, b_luck))
        err += abs(pred - r["actual"])
        if abs(pred - r["spread"]) >= 1 and r["actual"] != r["spread"]:
            n += 1
            w += (pred > r["spread"]) == (r["actual"] > r["spread"])
    return {"mae": err / len(rows) if rows else None, "w": w, "n": n}


def _best(rows):
    return min(((bt, bl) for bt in B_TO_GRID for bl in B_LUCK_GRID),
               key=lambda c: _score(rows, *c)["mae"])


def fit_reg(years):
    rows = reg_fit_rows(years)
    out = {"years": years, "n": len(rows), "full": _best(rows), "seasons": []}
    for i, yr in enumerate(years):
        test = [r for r in rows if r["yr"] == yr]
        train = [r for r in rows if r["yr"] < yr]
        if not test or len({r["yr"] for r in train}) < 2:
            continue
        fit = _best(train)
        out["seasons"].append({"yr": yr, "fit": fit,
                               "fpi": _score(test, 0, 0),
                               "current": _score(test, BETA_TO, BETA_LUCK),
                               "fitted": _score(test, *fit)})
    return out


def report_fit_reg(f):
    yrs = f["years"]
    print(f"\n{'='*70}\nNFL RegTilt COEFFICIENT FIT  {yrs[0]}-{yrs[-1]}  ({f['n']} priced games)\n{'='*70}")
    print(f"  current, set by hand        turnovers {BETA_TO:.2f}  luck {BETA_LUCK:.2f}")
    print(f"  best fit, all seasons       turnovers {f['full'][0]:.2f}  luck {f['full'][1]:.2f}"
          "   (in-sample — flatters itself)")
    print("\n  Out of sample: each season scored with coefficients fitted only on")
    print("  the seasons before it. MAE is points per game against the result;")
    print("  ATS is plays 1+ point off the closing line (break-even 52.4%).\n")
    print(f"    {'season':<8}{'fit (to, luck)':<16}{'FPI alone':>18}{'current tilt':>18}{'fitted tilt':>18}")
    tot = {k: [0.0, 0, 0, 0] for k in ("fpi", "current", "fitted")}
    for s in f["seasons"]:
        cells = []
        for k in ("fpi", "current", "fitted"):
            x = s[k]
            cells.append(f"{x['mae']:.2f}  {x['w']}-{x['n']-x['w']}")
            tot[k][0] += x["mae"]; tot[k][1] += x["w"]; tot[k][2] += x["n"]; tot[k][3] += 1
        print(f"    {s['yr']:<8}{f'{s["fit"][0]:.2f}, {s["fit"][1]:.2f}':<16}"
              + "".join(f"{c:>18}" for c in cells))
    if f["seasons"]:
        cells = []
        for k in ("fpi", "current", "fitted"):
            mae, w, n, k_ = tot[k]
            cells.append(f"{mae / k_:.3f}  {100 * w / n:.1f}%" if n else "-")
        print(f"    {'all':<8}{'':<16}" + "".join(f"{c:>18}" for c in cells))


# ---------------- box-score luck ("who really won") ----------------
#
# A final score is the box score plus noise: a pick-six, a missed kick, a
# fumble that bounced the right way. Net EPA from scrimmage (own passing and
# rushing EPA minus the opponent's) is what the plays were worth; the gap
# between that and the final margin is luck. Net EPA explains 2025 margins at
# r=0.92, so the remaining ~5 points a game is what this measures.
#
# The question for the model is whether the luck is still in the ratings: if
# FPI or the market chase a team's lucky scores, fading each team's luck so
# far this season should beat them. Scored walk-forward like --fit-reg.

_NV_TEAMWEEK = ("https://github.com/nflverse/nflverse-data/releases/download/"
                "stats_team/stats_team_week_{yr}.csv")
BOX_W_GRID = [i * 0.05 for i in range(0, 21)]


def box_luck(year, cached=True):
    """{(nv_team, week): final margin minus net scrimmage EPA}, regular season."""
    name = f"bt_teamweek_{year}.csv"
    text = (_csv_cached(_NV_TEAMWEEK.format(yr=year), name) if cached
            else ds.http_get_text(_NV_TEAMWEEK.format(yr=year), name)[0])
    epa, opp = {}, {}
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("season_type") != "REG":
            continue
        k = (r["team"], int(r["week"]))
        epa[k] = float(r.get("passing_epa") or 0) + float(r.get("rushing_epa") or 0)
        opp[k] = r["opponent_team"]
    margin = {}
    for r in csv.DictReader(io.StringIO(_csv_cached(_NFLVERSE, "bt_nflverse_games.csv"))):
        if r["season"] != str(year) or r.get("game_type") != "REG" or not r.get("result"):
            continue
        wk, m = int(r["week"]), float(r["result"])
        margin[(r["home_team"], wk)], margin[(r["away_team"], wk)] = m, -m
    return {k: margin[k] - (e - epa[(opp[k], k[1])])
            for k, e in epa.items() if k in margin and (opp[k], k[1]) in epa}


def _prior_luck(luck, team, wk):
    xs = [v for (t, w), v in luck.items() if t == team and w < wk]
    return sum(xs) / len(xs) if xs else 0.0


def box_fit_rows(years):
    import playrules
    cfg = LEAGUES["nfl"]
    lines = nfl_closing_lines()
    id2nv = espn_to_nflverse(years)
    rows = []
    for yr in years:
        inp, luck = nfl_reg_inputs(yr), box_luck(yr)
        for wk in cfg["weeks"]:
            games = results_week("nfl", yr, wk)
            if not games:
                continue
            first = min((_dt(g["kickoff"]) for g in games if g.get("kickoff")), default=None)
            fpi, _ = fpi_asof("nfl", yr, wk, before=first)
            for g in games:
                hid, aid = g["home"]["id"], g["away"]["id"]
                hnv, anv = id2nv.get(hid), id2nv.get(aid)
                key = (yr, wk, hnv, anv)
                if hid not in fpi or aid not in fpi or key not in lines:
                    continue
                h, a = inp.get(hnv, (0, 0.0)), inp.get(anv, (0, 0.0))
                rows.append({"yr": yr, "wk": wk,
                             "base": fpi[hid] - fpi[aid] + (0.0 if g["neutral"] else cfg["hfa"]),
                             "reg": playrules.decay("nfl", wk) * (reg_tilt(*h) - reg_tilt(*a)),
                             "luck": _prior_luck(luck, hnv, wk) - _prior_luck(luck, anv, wk),
                             "spread": lines[key], "actual": g["margin"]})
    return rows


def _score_box(rows, w, reg=True):
    err, won, n = 0.0, 0, 0
    for r in rows:
        pred = r["base"] + (r["reg"] if reg else 0.0) - w * r["luck"]
        err += abs(pred - r["actual"])
        if abs(pred - r["spread"]) >= 1 and r["actual"] != r["spread"]:
            n += 1
            won += (pred > r["spread"]) == (r["actual"] > r["spread"])
    return {"mae": err / len(rows) if rows else None, "w": won, "n": n}


def fit_box(years):
    rows = box_fit_rows(years)
    full = min(BOX_W_GRID, key=lambda w: _score_box(rows, w)["mae"])
    seasons = []
    for yr in years:
        test = [r for r in rows if r["yr"] == yr]
        train = [r for r in rows if r["yr"] < yr]
        if not test or len({r["yr"] for r in train}) < 2:
            continue
        w = min(BOX_W_GRID, key=lambda w: _score_box(train, w)["mae"])
        seasons.append({"yr": yr, "w": w,
                        "fpi": _score_box(test, 0, reg=False),
                        "current": _score_box(test, 0),
                        "box": _score_box(test, w)})
    # The market's own view: does the closing line still carry the luck?
    mkt = [(r["spread"] - r["actual"], r["luck"]) for r in rows if r["wk"] > 1]
    return {"years": years, "n": len(rows), "full": full, "seasons": seasons, "mkt": mkt}


def report_fit_box(f):
    yrs = f["years"]
    print(f"\n{'='*72}\nNFL BOX-SCORE LUCK  {yrs[0]}-{yrs[-1]}  ({f['n']} priced games)\n{'='*72}")
    print(f"  best weight, all seasons    {f['full']:.2f} of each point of luck faded"
          "   (in-sample)")
    print("\n  Out of sample (weight fitted only on earlier seasons). MAE against the")
    print("  result; ATS on plays 1+ point off the closing line (break-even 52.4%).\n")
    print(f"    {'season':<8}{'weight':<9}{'FPI alone':>18}{'FPI + RegTilt':>18}{'+ box luck':>18}")
    tot = {k: [0.0, 0, 0, 0] for k in ("fpi", "current", "box")}
    for s in f["seasons"]:
        cells = []
        for k in ("fpi", "current", "box"):
            x = s[k]
            cells.append(f"{x['mae']:.2f}  {x['w']}-{x['n']-x['w']}")
            tot[k][0] += x["mae"]; tot[k][1] += x["w"]; tot[k][2] += x["n"]; tot[k][3] += 1
        print(f"    {s['yr']:<8}{s['w']:<9.2f}" + "".join(f"{c:>18}" for c in cells))
    if f["seasons"]:
        cells = [f"{t[0] / t[3]:.3f}  {100 * t[1] / t[2]:.1f}%" if t[2] else "-"
                 for t in tot.values()]
        print(f"    {'all':<8}{'':<9}" + "".join(f"{c:>18}" for c in cells))
    xs = [l for _, l in f["mkt"]]
    ys = [e for e, _ in f["mkt"]]
    if len(xs) > 30:
        mx, my = statistics.mean(xs), statistics.mean(ys)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        print(f"\n  Closing line's miss per point of luck so far: {b:+.3f}"
              "   (0 = the market already fades it; + = it still chases luck)")


if __name__ == "__main__":
    main()
