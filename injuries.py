"""Who is not playing this week, and what that costs the line.

The team ratings are season numbers; an injury is a this-week number. So an
absence never touches a rating. It moves only the projected margin of games
kicking off inside the coming week, which is the only window the status report
describes — a player out today says nothing about a game three weeks away.

A player counts when he is listed Out or Doubtful and he has a value:

  the hand-kept list       league,team,player,position,points_if_out,status,
                           note — the shared Google Sheet named in
                           injury_sheet.txt, plus data/injury_values.csv. A
                           player on it is worth exactly what it says.
  QB1_DEFAULT              otherwise, only the starting quarterback (first on
                           ESPN's depth chart) counts, at a flat value. The
                           feed alone cannot tell a starter from a backup, and
                           outside quarterback no single absence reliably
                           moves an NFL line.

Injured Reserve is left out on purpose. A player gone for weeks is already in
the games FPI has rated since, so charging for him again would count him twice.

NFL status comes from ESPN's injury report; the list's status column is ignored
there. College has no such report — ESPN's college feed carries a handful of
players for the whole sport — so a college row counts only while the list
itself says Out or Doubtful, and someone has to set it back when he returns.

The adjustment is written into each ledger play, so whether it earns its place
can be checked the same way the tilts were rather than believed.
"""
import csv
import datetime
import io
import json
import os
import urllib.request

import data_sources as ds

BASE = os.path.dirname(os.path.abspath(__file__))
VALUES_PATH = os.path.join(BASE, "data", "injury_values.csv")
# The sheet's id or URL. Gitignored and shipped by deploy.sh; the sheet has to
# be viewable by link for the server to read it.
SHEET_FILE = os.path.join(BASE, "injury_sheet.txt")
SHEET_CACHE = os.path.join(BASE, "data", "cache", "injury_sheet.csv")
# When each priced absence was first seen, so the page can flag what is new.
# Server-side state like the ledger: deploy.sh must not sync over it.
SEEN_PATH = os.environ.get("INJURY_SEEN_PATH") or os.path.join(BASE, "data", "injury_seen.json")

# site.web.api, not site.api: the latter returns 403 to AWS ranges.
FEED = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
DEPTH = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/teams/{id}/depthcharts"
MISSING = {"Out", "Doubtful"}
# Conservative on purpose: FPI may already carry part of a known absence, and
# the market prices a starter-to-backup drop at several points more than this.
QB1_DEFAULT = 3.0


def _sheet_csv():
    """The shared sheet as CSV text, or None. A sheet nobody can read comes
    back as Google's sign-in page, which is refused rather than parsed; the
    last good copy is used instead so one bad fetch cannot erase the list."""
    ref = os.environ.get("INJURY_SHEET")
    if not ref and os.path.exists(SHEET_FILE):
        ref = open(SHEET_FILE).read().strip()
    if not ref:
        return None, None
    sid = ref.split("/d/")[1].split("/")[0] if "/d/" in ref else ref
    url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8-sig", errors="replace")
        if not text.lower().lstrip().startswith("league,"):
            raise ValueError("not the list — is the sheet shared by link?")
        os.makedirs(os.path.dirname(SHEET_CACHE), exist_ok=True)
        with open(SHEET_CACHE, "w", encoding="utf-8") as f:
            f.write(text)
        return text, "live"
    except Exception as e:  # noqa: BLE001 — fall back to the last good copy
        if os.path.exists(SHEET_CACHE):
            return open(SHEET_CACHE, encoding="utf-8").read(), f"cache ({e})"
        return None, f"unreadable ({e})"


def load_rows():
    """Every usable row of the hand-kept list: the sheet, then the local file."""
    texts = []
    sheet, mode = _sheet_csv()
    if sheet:
        texts.append(sheet)
    if os.path.exists(VALUES_PATH):
        texts.append(open(VALUES_PATH, newline="", encoding="utf-8").read())
    rows = []
    for text in texts:
        for r in csv.DictReader(io.StringIO(text)):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            try:
                pts = abs(float(r.get("points_if_out")))
            except (TypeError, ValueError):
                continue
            if not (r.get("team") and r.get("player")):
                continue
            rows.append({"league": r.get("league", "").upper(), "team": r["team"],
                         "player": r["player"], "pos": r.get("position") or None,
                         "pts": pts, "status": r.get("status", "").title(),
                         "note": r.get("note", "")})
    return rows, mode


def load_values():
    """{(league, team, player): (points, note)} from the hand-kept list."""
    return {(r["league"], r["team"], r["player"]): (r["pts"], r["note"])
            for r in load_rows()[0]}


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
    """{team: [{player, pos, status, pts, why}, ...]} for absences that move the line."""
    try:
        feed, mode = ds.http_get_json(FEED, "nfl_injuries")
    except Exception as e:  # noqa: BLE001 — an injury outage should not stop a build
        ds._report("NFL injuries (ESPN)", "fallback", f"none applied — {e}")
        return {}
    vals = {(r["league"], r["team"], r["player"]): (r["pts"], r["note"])
            for r in load_rows()[0] if r["league"] == "NFL"}
    lost = {}
    for t in feed.get("injuries") or []:
        team = t.get("displayName")
        missing = [(i["athlete"].get("displayName"),
                    (i["athlete"].get("position") or {}).get("abbreviation"), i["status"])
                   for i in t.get("injuries") or []
                   if i.get("status") in MISSING and i.get("athlete")]
        if not missing:
            continue
        qb1 = _qb1(t.get("id")) if any(p == "QB" for _, p, _ in missing) else None
        for name, pos, status in missing:
            v = vals.get(("NFL", team, name))
            if v:
                pts, why = v[0], v[1] or "on the list"
            elif pos == "QB" and name == qb1:
                pts, why = QB1_DEFAULT, "starting QB"
            else:
                continue
            lost.setdefault(team, []).append(
                {"player": name, "pos": pos, "status": status, "pts": pts, "why": why})
    n = sum(len(v) for v in lost.values())
    ds._report("NFL injuries (ESPN)", "live" if mode == "live" else mode,
               f"{n} absence(s) priced across {len(lost)} team(s)"
               + (f", {len(vals)} valued by hand" if vals else ", starting QBs only"))
    return lost


def _cfb_team(name, teams):
    """The model's name for a team written either way: "Texas" or ESPN's
    "Texas Longhorns" (mascot words dropped from the end until one matches)."""
    if name in teams:
        return name
    if ds.canon(name) in teams:
        return ds.canon(name)
    words = name.split()
    for n in range(len(words) - 1, 0, -1):
        cand = " ".join(words[:n])
        if cand in teams:
            return cand
        if ds.canon(cand) in teams:
            return ds.canon(cand)
    return None


def get_cfb_injuries(teams):
    """Same shape as get_nfl_injuries(), from the hand-kept list alone."""
    rows, mode = load_rows()
    lost, unknown = {}, set()
    for r in rows:
        if r["league"] != "CFB" or r["status"] not in MISSING:
            continue
        team = _cfb_team(r["team"], teams)
        if not team:
            unknown.add(r["team"])
            continue
        lost.setdefault(team, []).append(
            {"player": r["player"], "pos": r["pos"], "status": r["status"],
             "pts": r["pts"], "why": r["note"] or "on the list"})
    n = sum(len(v) for v in lost.values())
    detail = f"{n} absence(s) marked out across {len(lost)} team(s)"
    if unknown:
        detail += f"; team name not recognised: {', '.join(sorted(unknown))}"
    if mode and mode != "live":
        detail += f"; sheet {mode}"
    ds._report("CFB injuries (hand list)",
               "live" if mode in (None, "live")
               else "fallback" if mode.startswith("unreadable") else "cache", detail)
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
        g["inj"] = round(sum(x["pts"] for x in a) - sum(x["pts"] for x in h), 1)
        g["inj_note"] = ", ".join(f"{x['player']} {x['status'].lower()} −{x['pts']:.1f}"
                                  for x in h + a)
        touched += 1
    return touched


def track(lost, games, lg, now=None, path=SEEN_PATH):
    """The panel's rows: every priced absence, the game it moves and when it
    was first seen. An absence that drops off the list is forgotten, so a
    player hurt again later shows as new again."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(path, encoding="utf-8") as f:
            seen = json.load(f)
    except (OSError, ValueError):
        seen = {}
    # this league's entries are rebuilt below; the other league's stay put
    keep = {k: v for k, v in seen.items() if not k.startswith(lg + "|")
            and not (lg == "nfl" and k.count("|") == 1)}
    rows = []
    for team, xs in lost.items():
        g = next((g for g in games if g.get("inj") is not None
                  and team in (g["home"], g["away"])), None)
        for x in xs:
            k = f"{lg}|{team}|{x['player']}"
            # keys written before college existed carried no league prefix
            keep[k] = (seen.get(k) or (seen.get(f"{team}|{x['player']}") if lg == "nfl" else None)
                       or now.isoformat(timespec="seconds"))
            rows.append(dict(x, team=team, first_seen=keep[k],
                             game=(f"{g['away']} @ {g['home']}" if g else None),
                             week=g["week"] if g else None,
                             date=g["date"] if g else None))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(keep, f, indent=1, sort_keys=True)
    rows.sort(key=lambda r: r["first_seen"], reverse=True)
    return rows
