"""What the model said, written down before the games were played.

A rating is not a fixed thing. FPI refreshes every week, so rebuilding last
week's pick from this week's numbers would quietly rewrite history in the
model's favour — the same lookahead trap backtest.py exists to catch. So a play
is written once, at first sighting, and never recomputed. Later builds may only
fill in what happened.

That single rule is what separates this from a spreadsheet that gets retyped
each Monday: the record cannot flatter itself, because the thing being graded
was fixed before the result existed.

    .venv/bin/python ledger.py            # show the standing record
    .venv/bin/python ledger.py --path X   # against a different ledger file
"""
import argparse
import datetime
import json
import os

import playrules

BASE = os.path.dirname(os.path.abspath(__file__))
# Kept out of data/cache (which deploy.sh excludes wholesale) and named
# explicitly in deploy.sh's excludes, so a redeploy cannot delete the record.
PATH = os.environ.get("LEDGER_PATH") or os.path.join(BASE, "data", "ledger.json")


def key(lg, p):
    return f"{lg}|{p['week']}|{p['away']}|{p['home']}"


def load(path=PATH):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and "plays" in d:
                return d
        except (ValueError, OSError):
            pass          # a corrupt ledger should not stop a build
    return {"version": 1, "plays": {}}


def save(d, path=PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    os.replace(tmp, path)          # atomic; a killed build leaves the old file


def record(d, lg, plays, seen_iso):
    """Add plays not seen before. Existing entries are never touched."""
    added = 0
    for p in plays:
        k = key(lg, p)
        if k in d["plays"]:
            continue
        d["plays"][k] = dict(p, lg=lg, seen=seen_iso,
                             away_pts=None, home_pts=None, ats=None)
        added += 1
    return added


def track_close(d, lg, games, now_iso):
    """Keep each play's closing line: the last market number seen before
    kickoff. The number the play was taken at never changes; `close` is the
    one thing besides the result that a later build may fill in.

    College lines come live from the book, which pulls a game at kickoff (and
    could be showing in-game odds after it), so they stop updating then — the
    close is the last 6-hourly capture. nflverse's NFL number is the closing
    line itself once a game is played, so NFL keeps reading it until graded,
    and a play graded before this existed takes it once."""
    by = {(g["week"], g["away"], g["home"]): g for g in games}
    now = datetime.datetime.fromisoformat(now_iso)
    n = 0
    for p in d["plays"].values():
        if p["lg"] != lg:
            continue
        if p.get("ats") is not None and not (lg == "nfl" and "close" not in p):
            continue
        g = by.get((p["week"], p["away"], p["home"]))
        if not g or g.get("mkt") is None:
            continue
        if lg != "nfl":
            ko = g.get("iso")
            if not ko or datetime.datetime.fromisoformat(ko.replace("Z", "+00:00")) <= now:
                continue
        p["close"] = float(g["mkt"])
        n += 1
    return n


def clv(p):
    """Points the market moved toward the side taken, from the number the play
    was recorded at to the close. Positive = the play beat the closing line."""
    if p.get("close") is None:
        return None
    move = p["close"] - p["mkt"]
    return round(move if p["side"] == p["home"] else -move, 1) + 0.0


def grade(d, year, fetch):
    """Fill in results for plays whose games have finished.

    `fetch(lg, year, weeks) -> {(week, away, home): (away_pts, home_pts)}`.
    """
    todo = {}
    for p in d["plays"].values():
        if p.get("ats") is None:
            todo.setdefault(p["lg"], set()).add(p["week"])
    graded = 0
    for lg, weeks in todo.items():
        try:
            res = fetch(lg, year, sorted(weeks))
        except Exception:  # noqa: BLE001 — grading is best-effort, never fatal
            continue
        for p in d["plays"].values():
            if p["lg"] != lg or p.get("ats") is not None:
                continue
            got = res.get((p["week"], p["away"], p["home"]))
            if not got:
                continue
            a_pts, h_pts = got
            actual, mkt = h_pts - a_pts, p["mkt"]
            if actual == mkt:
                ats = "Push"
            elif p["side"] == p["home"]:
                ats = "W" if actual > mkt else "L"
            else:
                ats = "W" if actual < mkt else "L"
            p.update(away_pts=a_pts, home_pts=h_pts, ats=ats)
            graded += 1
    return graded


# ---------------- reporting ----------------

def _rec(sel):
    w = sum(1 for p in sel if p["ats"] == "W")
    l = sum(1 for p in sel if p["ats"] == "L")
    push = sum(1 for p in sel if p["ats"] == "Push")
    n = w + l
    return {"w": w, "l": l, "push": push, "n": n,
            "pct": round(100 * w / n, 1) if n else None}


def _clv(sel):
    """How often the close moved toward the play. Only finished games count,
    so every close here is final."""
    xs = [clv(p) for p in sel if p.get("ats") and p.get("close") is not None]
    beat = sum(1 for x in xs if x > 0)
    worse = sum(1 for x in xs if x < 0)
    n = beat + worse
    return {"n": len(xs), "beat": beat, "worse": worse, "same": len(xs) - n,
            "pct": round(100 * beat / n, 1) if n else None,
            "avg": round(sum(xs) / len(xs), 2) if xs else None}


def summary(d, recent=25):
    """Aggregates for the dashboard. Only graded plays count toward a record;
    everything else is reported as pending so the two are never conflated."""
    plays = list(d["plays"].values())
    graded = [p for p in plays if p.get("ats")]
    pending = [p for p in plays if not p.get("ats")]

    def split(sel):
        return {
            "all": _rec(sel),
            "clv": _clv(sel),
            "by_bucket": [dict(_rec([p for p in sel
                                     if lo <= p["abs_edge"] < hi]), bucket=lab)
                          for lab, lo, hi in playrules.BUCKETS],
            "by_stars": [dict(_rec([p for p in sel
                                    if int(p["stars"]) == s]), stars=s)
                         for s in range(6)],
        }

    cfb = [p for p in graded if p["lg"] == "cfb"]
    out = {
        "break_even": playrules.BREAK_EVEN,
        "graded": len(graded), "pending": len(pending),
        "overall": _rec(graded),
        "clv": _clv(graded),
        "cfb": split(cfb),
        "nfl": split([p for p in graded if p["lg"] == "nfl"]),
        "recommended": _rec([p for p in graded if p["lg"] == "nfl"
                             or p.get("tier") == "P4"]),
        "p4": _rec([p for p in cfb if p.get("tier") == "P4"]),
        "g5": _rec([p for p in cfb if p.get("tier") == "G5"]),
        # per league: college weeks run ahead of the NFL's, so one shared
        # cut filled every slot with college and left the NFL list empty
        "recent": [p for lg in ("cfb", "nfl")
                   for p in sorted((p for p in graded if p["lg"] == lg),
                                   key=lambda p: p["week"], reverse=True)[:recent]],
        "upcoming": sorted(pending, key=lambda p: -p["stars"])[:recent],
    }
    return out


def _line(lab, r):
    if not r["n"]:
        return f"  {lab:<26} —"
    edge = "" if r["pct"] is None else (
        "  <-- clears break-even" if r["pct"] >= playrules.BREAK_EVEN else "")
    push = f" ({r['push']} push)" if r["push"] else ""
    return f"  {lab:<26} {r['w']}-{r['l']}{push}  {r['pct']:5.1f}%{edge}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=PATH)
    args = ap.parse_args()
    s = summary(load(args.path))
    print(f"Ledger: {s['graded']} graded, {s['pending']} pending"
          f"   (break-even at -110 is {s['break_even']}%)\n")
    print(_line("All graded plays", s["overall"]))
    print(_line("Recommended (P4 + NFL)", s["recommended"]))
    print(_line("CFB Power-4", s["p4"]))
    print(_line("CFB G5 (no-bet)", s["g5"]))
    c = s["clv"]
    if c["n"]:
        print(f"\n  Beat the closing line      {c['beat']} of {c['beat'] + c['worse']}"
              f" ({c['pct']}%), {c['same']} unmoved, average {c['avg']:+.2f} pts"
              f"   (50% = no edge)")
    for lg in ("cfb", "nfl"):
        if not s[lg]["all"]["n"]:
            continue
        print(f"\n  {lg.upper()} by size of edge:")
        for b in s[lg]["by_bucket"]:
            if b["n"]:
                print(_line("    " + b["bucket"], b))


if __name__ == "__main__":
    main()
