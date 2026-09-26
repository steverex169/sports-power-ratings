"""Who is not playing this week, and what that costs the line.

The team ratings are season numbers; an injury is a this-week number. So an
absence never touches a rating. It moves only the projected margin of games
kicking off inside the coming week, which is the only window the status report
describes — a player out today says nothing about a game three weeks away.

A player counts when ESPN lists him Out or Doubtful and he has a value:

  data/injury_values.csv   the hand-kept list, league,team,player,position,
                           points_if_out,note. A player on it is worth
                           exactly what it says.
  QB1_DEFAULT              otherwise, only the starting quarterback (first on
                           ESPN's depth chart) counts, at a flat value. The
                           feed alone cannot tell a starter from a backup, and
                           outside quarterback no single absence reliably
                           moves an NFL line.

Injured Reserve is left out on purpose. A player gone for weeks is already in
the games FPI has rated since, so charging for him again would count him twice.

College is not covered: ESPN's college injury feed carries a handful of players
for the whole sport, so there is nothing to automate against.

The adjustment is written into each ledger play, so whether it earns its place
can be checked the same way the tilts were rather than believed.
"""
import csv
import datetime
import os

import data_sources as ds

BASE = os.path.dirname(os.path.abspath(__file__))
VALUES_PATH = os.path.join(BASE, "data", "injury_values.csv")

FEED = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
DEPTH = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{id}/depthcharts"
MISSING = {"Out", "Doubtful"}
# Conservative on purpose: FPI may already carry part of a known absence, and
# the market prices a starter-to-backup drop at several points more than this.
QB1_DEFAULT = 3.0


def load_values(path=VALUES_PATH):
    """{(league, team, player): (points, note)} from the hand-kept list."""
    vals = {}
    if not os.path.exists(path):
        return vals
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                pts = abs(float(r["points_if_out"]))
            except (KeyError, TypeError, ValueError):
                continue
            key = ((r.get("league") or "").strip().upper(), (r.get("team") or "").strip(),
                   (r.get("player") or "").strip())
            vals[key] = (pts, (r.get("note") or "").strip())
    return vals


def _qb1(team_id):
    """The first quarterback on the team's depth chart, or None."""
    try:
        d, _ = ds.http_get_json(DEPTH.format(id=team_id), f"nfl_depth_{team_id}")
    except Exception:  # noqa: BLE001 — no depth chart means no default charge
        return None
    for dc in d.get("depthchart") or []:
        qb = (dc.get("positions") or {}).get("qb")
        if qb and qb.get("athletes"):
            return qb["athletes"][0].get("displayName")
    return None


def get_nfl_injuries():
    """{team: [(player, points, why), ...]} for absences that move the line."""
    try:
        feed, mode = ds.http_get_json(FEED, "nfl_injuries")
    except Exception as e:  # noqa: BLE001 — an injury outage should not stop a build
        ds._report("NFL injuries (ESPN)", "fallback", f"none applied — {e}")
        return {}
    vals = load_values()
    lost = {}
    for t in feed.get("injuries") or []:
        team = t.get("displayName")
        missing = [(i["athlete"].get("displayName"),
                    (i["athlete"].get("position") or {}).get("abbreviation"))
                   for i in t.get("injuries") or []
                   if i.get("status") in MISSING and i.get("athlete")]
        if not missing:
            continue
        qb1 = _qb1(t.get("id")) if any(p == "QB" for _, p in missing) else None
        for name, pos in missing:
            v = vals.get(("NFL", team, name))
            if v:
                pts, why = v[0], v[1] or "on the list"
            elif pos == "QB" and name == qb1:
                pts, why = QB1_DEFAULT, "starting QB"
            else:
                continue
            lost.setdefault(team, []).append((name, pts, why))
    n = sum(len(v) for v in lost.values())
    ds._report("NFL injuries (ESPN)", "live" if mode == "live" else mode,
               f"{n} absence(s) priced across {len(lost)} team(s)"
               + (f", {len(vals)} valued by hand" if vals else ", starting QBs only"))
    return lost


def apply(games, lost, horizon_days=7, now=None):
    """Write each affected game's adjustment onto it, as a home margin:
    `inj` is positive when the away side is the one missing players."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    touched = 0
    for g in games:
        g.pop("inj", None)
        g.pop("inj_note", None)
        if not (lost.get(g["home"]) or lost.get(g["away"])) or not g.get("iso"):
            continue
        ko = datetime.datetime.fromisoformat(g["iso"].replace("Z", "+00:00"))
        if ko <= now or (ko - now).total_seconds() > horizon_days * 86400:
            continue
        h = lost.get(g["home"], [])
        a = lost.get(g["away"], [])
        g["inj"] = round(sum(p for _, p, _ in a) - sum(p for _, p, _ in h), 1)
        g["inj_note"] = ", ".join(f"{n} out −{p:.1f}" for n, p, _ in h + a)
        touched += 1
    return touched
