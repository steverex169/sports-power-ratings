"""Does college luck carry any signal, and does the market already know?

"Luck" here is wins minus CFBD's expectedWins for the prior season: a team
that went 10-2 on a performance worth 8.1 wins was carried by close games and
turnovers, and the claim worth testing is that it comes back to earth.

The NFL side of this model already runs that tilt (RegTilt). The college side
never has, because two things were missing: a luck series, and any way to score
college predictions against a betting market. A CFBD key supplies both --
/records carries expectedWins back years, and /lines carries historical
spreads, which backtest.py names as its one blind spot.

So this answers the question before the tilt is allowed to move a line:

  1. Does adding a luck tilt to point-in-time FPI reduce prediction error?
  2. Does it help against the closing line, which is the only test that counts?

    .venv/bin/python cfbluck.py                  # 2023-2025
    .venv/bin/python cfbluck.py --years 2022-2025
    .venv/bin/python cfbluck.py --betas 0,0.2,0.4,0.6
"""
import argparse
import json
import os
import statistics
import sys
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import backtest as bt          # noqa: E402  point-in-time FPI + results
import data_sources as ds      # noqa: E402

# The published host answers some networks and not others; the newer one is
# tried first because it is the one that is actually maintained.
HOSTS = ("https://apinext.collegefootballdata.com",
         "https://api.collegefootballdata.com")


def _get(path, cache, **params):
    cached = os.path.join(ds.CACHE_DIR, f"cfbd_{cache}.json")
    if os.path.exists(cached):
        try:
            return json.load(open(cached))
        except ValueError:
            pass
    key = ds._cfbd_key()
    if not key:
        raise SystemExit("no CFBD key — put CFBD_API_KEY in cfbd_key.txt")
    q = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    last = None
    for host in HOSTS:
        try:
            req = urllib.request.Request(
                f"{host}/{path}?{q}",
                headers={"Authorization": f"Bearer {key}", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            if data:
                os.makedirs(ds.CACHE_DIR, exist_ok=True)
                json.dump(data, open(cached, "w"))
                return data
        except Exception as e:  # noqa: BLE001 — try the other host before giving up
            last = e
    if last:
        raise last
    return []


def luck(year):
    """{team: wins - expectedWins} for `year`, FBS only.

    Positive means the team won more than its game-by-game performance implied.
    Used to predict the FOLLOWING season, so it is known before a ball is
    snapped — no lookahead.
    """
    out = {}
    for r in _get("records", f"records_{year}", year=year):
        if r.get("classification") != "fbs" or r.get("expectedWins") is None:
            continue
        wins = (r.get("total") or {}).get("wins")
        if wins is None:
            continue
        out[ds.canon(r["team"])] = float(wins) - float(r["expectedWins"])
    return out


def lines(year):
    """{(week, home, away): (spread_home_margin, home_pts, away_pts)}.

    CFBD quotes the spread from the home side; the sign convention is settled
    empirically below rather than trusted, because getting it backwards would
    silently invert every result in this file.
    """
    out = {}
    for g in _get("lines", f"lines_{year}", year=year, seasonType="regular"):
        hs, as_ = g.get("homeScore"), g.get("awayScore")
        if hs is None or as_ is None:
            continue
        vals = [l.get("spread") for l in (g.get("lines") or [])
                if l.get("spread") is not None]
        if not vals:
            continue
        spread = statistics.median(float(v) for v in vals)   # consensus of the books
        out[(int(g["week"]), ds.canon(g["homeTeam"]), ds.canon(g["awayTeam"]))] = \
            (spread, int(hs), int(as_))
    return out


def settle_sign(all_lines):
    """+1 if CFBD's spread is already a home margin, -1 if it is the home
    handicap. Decided by which reading correlates with what actually happened."""
    xs = [(sp, hs - as_) for (sp, hs, as_) in all_lines.values()]
    agree = sum(1 for sp, m in xs if (sp > 0) == (m > 0))
    return 1 if agree >= len(xs) / 2 else -1


def run(years, betas):
    lk = {y: luck(y) for y in range(min(years) - 1, max(years) + 1)}
    per_year_lines = {y: lines(y) for y in years}
    merged = {}
    for y, d in per_year_lines.items():
        for k, v in d.items():
            merged[(y,) + k] = v
    sign = settle_sign(merged)
    print(f"CFBD spread convention: {'home margin' if sign > 0 else 'home handicap'} "
          f"(settled from {len(merged)} games)\n")

    rows = {b: {"err": [], "ats": []} for b in betas}
    matched = unmatched = no_fpi = 0

    for yr in years:
        prior = lk.get(yr - 1, {})
        L = per_year_lines[yr]
        for wk in bt.LEAGUES["cfb"]["weeks"]:
            games = bt.results_week("cfb", yr, wk)
            if not games:
                continue
            first = min((bt._dt(g["kickoff"]) for g in games if g.get("kickoff")),
                        default=None)
            fpi, _ = bt.fpi_asof("cfb", yr, wk, before=first)
            if not fpi:
                continue
            for g in games:
                home = ds.canon(g["home"]["name"])
                away = ds.canon(g["away"]["name"])
                got = L.get((wk, home, away))
                if not got:
                    unmatched += 1
                    continue
                hid, aid = g["home"]["id"], g["away"]["id"]
                if hid not in fpi or aid not in fpi:
                    no_fpi += 1
                    continue
                matched += 1
                spread, hs, as_ = got
                spread *= sign
                actual = hs - as_
                hfa = 0.0 if g["neutral"] else bt.LEAGUES["cfb"]["hfa"]
                base = fpi[hid] - fpi[aid] + hfa
                # last season's luck, regressed against: a team that over-won is
                # faded, one that under-won is bought
                tilt = -(prior.get(home, 0.0) - prior.get(away, 0.0))
                for b in betas:
                    pred = base + b * tilt
                    rows[b]["err"].append(pred - actual)
                    if actual != spread:
                        rows[b]["ats"].append(
                            (abs(pred - spread),
                             int((pred > spread) == (actual > spread))))
    return rows, (matched, unmatched, no_fpi), merged, sign


def report(rows, counts, merged, years, sign):
    matched, unmatched, no_fpi = counts
    print(f"{matched} games scored  ({unmatched} without a line, {no_fpi} without FPI)")
    print(f"seasons {min(years)}-{max(years)}, break-even at -110 is 52.4%\n")

    print(f"  {'beta':>5}  {'MAE':>6}  {'RMSE':>6}   {'ATS':>12}  {'hit':>6}")
    for b, d in rows.items():
        e = d["err"]
        if not e:
            continue
        mae = statistics.mean(abs(x) for x in e)
        rmse = (statistics.mean(x * x for x in e)) ** 0.5
        hits = sum(w for _, w in d["ats"])
        n = len(d["ats"])
        pct = 100 * hits / n if n else 0
        flag = "  <-- beats the number" if n > 200 and pct >= 52.4 else ""
        label = "none (FPI alone)" if b == 0 else f"{b}"
        print(f"  {label:>5}  {mae:6.2f}  {rmse:6.2f}   {hits:>5}-{n - hits:<5}  {pct:5.1f}%{flag}")

    best = max((b for b in rows if rows[b]["err"]),
               key=lambda b: statistics.mean(abs(x) for x in rows[b]["err"]) * -1)
    zero = rows.get(0)
    if zero and zero["err"] and best != 0:
        m0 = statistics.mean(abs(x) for x in zero["err"])
        mb = statistics.mean(abs(x) for x in rows[best]["err"])
        print(f"\n  Best beta {best}: {m0 - mb:+.3f} pts/game of MAE against FPI alone.")
        if m0 - mb < 0.05:
            print("  That is inside the noise. The tilt is not earning its place.")

    # what the market itself scores, as the benchmark
    merr = [sp * sign - (hs - as_) for (sp, hs, as_) in merged.values()]
    if merr:
        print(f"\n  Closing line, same games: MAE "
              f"{statistics.mean(abs(x) for x in merr):.2f} pts/game.")

    if zero and zero["ats"]:
        print("\n  FPI alone, ATS by size of disagreement:")
        for lo in (0, 1, 2, 3, 4, 6):
            sel = [w for e, w in zero["ats"] if e >= lo]
            if len(sel) < 50:
                continue
            h, n = sum(sel), len(sel)
            print(f"    edge >= {lo:<2} n={n:<5} {h}-{n - h}  {100 * h / n:5.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2023-2025")
    ap.add_argument("--betas", default="0,0.2,0.4,0.6,0.8")
    a = ap.parse_args()
    lo, hi = (a.years.split("-") + [a.years])[:2]
    years = list(range(int(lo), int(hi) + 1))
    betas = [float(x) for x in a.betas.split(",")]
    rows, counts, merged, sign = run(years, betas)
    report(rows, counts, merged, years, sign)


if __name__ == "__main__":
    main()
