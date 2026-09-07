"""Key numbers: how often games land on a margin, and how that relates to the spread.

Answered from closing lines and final scores rather than from folklore:

  1. How often does the final margin land on each number (3, 7, 10, 14 ...)?
  2. When the spread is N, how often does the game push, and how far off the
     number does it usually finish? The push rate is what a half-point at that
     number is worth — that is what "the worth of each number" means.
  3. Is a 3-point spread stickier than a 24-point one?
  4. Spread is 7: where does the game actually land?

Favourite-centric throughout, matching how a line is read: "favoured by 7, won
by 9" is +2 against the number. Ties count as a margin of 0. NFL only — the
CFB half needs historical college lines, which sit behind a CFBD key.

    .venv/bin/python keynumbers.py                  # NFL, 2010-2025
    .venv/bin/python keynumbers.py --years 2015-2025

`compute()` returns the same tables as plain dicts so build.py can put them on
the dashboard.
"""
import argparse
import collections
import csv
import io
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import data_sources as ds  # noqa: E402

NFLVERSE = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
KEY_SPREADS = (3, 7, 10)          # the landing-spot breakdown is shown for these
SPREAD_BUCKETS = [("1 to 3", 0, 3), ("3.5 to 7", 3, 7), ("7.5 to 10", 7, 10),
                  ("10.5 to 14", 10, 14), ("14.5 and up", 14, 99)]


def load_games(years):
    """[(spread, fav_margin)] — spread as a positive magnitude, fav_margin the
    favourite's actual margin (negative when the dog won outright)."""
    # The build fetches this file moments earlier for market lines; reuse it.
    cached = os.path.join(ds.CACHE_DIR, "nflverse_games.csv")
    if os.path.exists(cached):
        text = open(cached).read()
    else:
        text, _ = ds.http_get_text(NFLVERSE, "nflverse_games.csv")
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            yr = int(r["season"])
            if yr not in years or r.get("game_type") != "REG":
                continue
            result = float(r["result"])          # home margin
            line = float(r["spread_line"])       # + = home favoured
        except (KeyError, TypeError, ValueError):
            continue
        if line == 0:
            continue                             # pick'em has no favourite
        out.append((abs(line), result if line > 0 else -result))
    return out


def compute(years, top=14):
    games = load_games(years)
    n = len(games)
    if not n:
        return None

    margins = collections.Counter(int(abs(m)) for _, m in games)
    margin_rows = [{"margin": m, "n": k, "pct": round(100 * k / n, 1)}
                   for m, k in margins.most_common(top)]

    by = collections.defaultdict(list)
    for s, m in games:
        if s == int(s):
            by[int(s)].append(m)
    push_rows = []
    for s in sorted(by):
        ms = by[s]
        if len(ms) < 40:
            continue
        k = len(ms)
        push_rows.append({
            "spread": s, "n": k,
            "push": round(100 * sum(1 for m in ms if m == s) / k, 1),
            "within1": round(100 * sum(1 for m in ms if abs(m - s) <= 1) / k, 1),
            "within3": round(100 * sum(1 for m in ms if abs(m - s) <= 3) / k, 1),
            "fav": round(100 * sum(1 for m in ms if m > s) / k, 1),
            "dog": round(100 * sum(1 for m in ms if m < s) / k, 1)})

    dist_rows = []
    for label, lo, hi in SPREAD_BUCKETS:
        ms = [(s, m) for s, m in games if lo < s <= hi]
        if not ms:
            continue
        k = len(ms)
        off = sorted(abs(m - s) for s, m in ms)
        dist_rows.append({
            "bucket": label, "n": k,
            "exact": round(100 * sum(1 for o in off if o == 0) / k, 1),
            "within1": round(100 * sum(1 for o in off if o <= 1) / k, 1),
            "within3": round(100 * sum(1 for o in off if o <= 3) / k, 1),
            "within7": round(100 * sum(1 for o in off if o <= 7) / k, 1),
            "median": off[k // 2]})

    landing = {}
    for key in KEY_SPREADS:
        at = [m for s, m in games if s == key]
        if len(at) < 40:
            continue
        c = collections.Counter(int(m) for m in at)
        landing[key] = {"n": len(at),
                        "spots": [{"margin": m, "pct": round(100 * k / len(at), 1)}
                                  for m, k in c.most_common(10)]}

    return {"years": [min(years), max(years)], "games": n, "margins": margin_rows,
            "pushes": push_rows, "distance": dist_rows, "landing": landing}


# ---------------- terminal report ----------------

def _p(x):
    return f"{x:5.1f}%"


def print_report(d):
    y0, y1 = d["years"]
    print(f"NFL regular season {y0}-{y1}: {d['games']} games with a closing spread")

    print("\n=== 1. Where games land — final margin, any favourite, any spread ===\n")
    print(f"  {'margin':>7}  {'games':>6}  {'share':>6}")
    for i, r in enumerate(d["margins"], 1):
        bar = "█" * int(round(r["pct"] * 1.6))
        print(f"  {r['margin']:>7}  {r['n']:>6}  {_p(r['pct'])}   {bar:<24}#{i}")

    print("\n=== 2. Spread is N: push rate (= what the half-point is worth), and where it finishes ===")
    print("     (integer spreads only — a hooked line like 3.5 cannot push)\n")
    print(f"  {'spread':>6}  {'games':>5}  {'push':>6}  {'fav ±1':>7}  {'fav ±3':>7}   "
          f"{'fav covers':>10}  {'dog covers':>10}")
    for r in d["pushes"]:
        print(f"  {r['spread']:>6}  {r['n']:>5}  {_p(r['push'])}  {_p(r['within1'])}  "
              f"{_p(r['within3'])}   {_p(r['fav']):>10}  {_p(r['dog']):>10}")

    print("\n=== 3. Distance from the number, by spread size ===")
    print("     (favourite's margin minus the spread; 'favoured by 7, won by 9' is +2)\n")
    print(f"  {'spread':<12}{'games':>6}  {'exact':>6}  {'within 1':>9}  {'within 3':>9}  "
          f"{'within 7':>9}  {'median off':>11}")
    for r in d["distance"]:
        print(f"  {r['bucket']:<12}{r['n']:>6}  {_p(r['exact'])}  {_p(r['within1']):>9}  "
              f"{_p(r['within3']):>9}  {_p(r['within7']):>9}  {r['median']:>11.1f}")

    print("\n=== 4. Spread is N: where the favourite's margin actually lands (top spots) ===\n")
    for key, L in d["landing"].items():
        spots = "  ".join(f"{s['margin']:+d}:{s['pct']:.0f}%" if s['margin'] != key
                          else f"[{s['margin']:+d}:{s['pct']:.0f}% push]"
                          for s in L["spots"])
        print(f"  spread {key:>2}  ({L['n']} games)   {spots}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2010-2025")
    args = ap.parse_args()
    lo, hi = (args.years.split("-") + [args.years])[:2]
    d = compute(set(range(int(lo), int(hi) + 1)))
    if not d:
        print("no games loaded")
        return
    print_report(d)


if __name__ == "__main__":
    main()
