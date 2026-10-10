"""Our own NFL team rating, built from nflverse play-by-play.

FPI is ESPN's number and the model starts from it. This is a second opinion
built from the plays themselves: expected points added per snap on offense and
allowed on defense, adjusted for who each team played, so a team that piled up
EPA against bad defenses is not credited as if it did it against good ones.

    rating = points per play * (offense EPA/play - defense EPA allowed/play)

Each team-game is one observation, weighted by its snaps and by how recent it
is. Opponent adjustment is a weighted least-squares fit, shrunk toward last
season's rating, so in week 1 the rating is last season's (regressed) and the
current season takes over as games come in.

Only competitive snaps count: passes and runs with the offense's win
probability between 5% and 95%, no kneels or spikes. Garbage time inflates
EPA without saying anything about next week.

    .venv/bin/python pbprating.py            # this season's ratings, as of now
    .venv/bin/python backtest.py --pbp       # does it beat FPI or the market?
"""
import csv
import gzip
import io
import os
import sys
import time
import urllib.request

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import data_sources as ds

_PBP = ("https://github.com/nflverse/nflverse-data/releases/download/"
        "pbp/play_by_play_{yr}.csv.gz")
# Franchises that moved keep their history under today's abbreviation.
RELOC = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
WP_LO, WP_HI = 0.05, 0.95
LIVE_MAX_AGE = 6 * 3600   # nflverse posts the current season's plays nightly

# Settings fitted by `backtest.py --pbp` on 2019-2025 (1,724 priced games).
# What it found: alone, this rating predicts results a little worse than FPI
# (MAE 10.34 vs 10.21); blended in at 0.3-0.4 it is 0.02 points better, and the
# blend's picks went 46.2% ATS against FPI's 47.2%. Where it disagrees with the
# closing line the line was right (-0.21 per point). So it is shown beside FPI
# as a second opinion and kept out of My Rating and the plays.
HALF_LIFE = 12       # games; an older game counts half as much as one 12 games newer
PRIOR_PLAYS = 1200   # last season is worth this many snaps of this season
PRIOR_KEEP = 0.5     # and is regressed halfway to average first
PTS_PER_PLAY = 53.6  # EPA/play difference -> points per game


def _download(url, retries=4):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def _aggregate(raw):
    """Raw play-by-play (gzip CSV bytes) -> one row per offense per game."""
    keep = ("game_id", "season_type", "week", "posteam", "defteam", "home_team",
            "pass", "rush", "epa", "success", "wp", "qb_kneel", "qb_spike")
    agg = {}
    with gzip.open(io.BytesIO(raw), "rt", newline="") as f:
        for r in csv.DictReader(f):
            if r["season_type"] != "REG" or not r["epa"] or not r["wp"]:
                continue
            if r["pass"] != "1" and r["rush"] != "1":
                continue
            if r["qb_kneel"] == "1" or r["qb_spike"] == "1":
                continue
            if not WP_LO <= float(r["wp"]) <= WP_HI:
                continue
            k = (r["game_id"], int(r["week"]), r["posteam"], r["defteam"],
                 int(r["posteam"] == r["home_team"]))
            a = agg.setdefault(k, [0, 0.0, 0.0])
            a[0] += 1
            a[1] += float(r["epa"])
            a[2] += float(r["success"] or 0)
    return [{"game_id": g, "week": wk, "off": RELOC.get(o, o), "def": RELOC.get(d, d),
             "home": h, "plays": a[0], "epa": a[1], "success": a[2]}
            for (g, wk, o, d, h), a in agg.items()]


def game_rows(year, live=False):
    """[{week, off, def, home, plays, epa, success}] for a regular season.

    A finished season is cached for good. The live season is refetched when
    the cache is older than LIVE_MAX_AGE, and the old copy is used if the
    download fails, so a GitHub hiccup never blanks the column.
    """
    path = os.path.join(ds.CACHE_DIR, f"pbp_games_{year}.csv")
    fresh = os.path.exists(path) and (
        not live or time.time() - os.path.getmtime(path) < LIVE_MAX_AGE)
    if not fresh:
        try:
            rows = _aggregate(_download(_PBP.format(yr=year)))
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        except Exception as e:  # noqa: BLE001
            if not os.path.exists(path):
                ds._report(f"NFL play-by-play {year} (nflverse)", "fallback", f"unavailable — {e}")
                return []
            ds._report(f"NFL play-by-play {year} (nflverse)", "fallback",
                       f"using cached copy — {e}")
    out = []
    for r in csv.DictReader(open(path)):
        out.append({"game_id": r["game_id"], "week": int(r["week"]), "off": r["off"],
                    "def": r["def"], "home": int(r["home"]), "plays": int(r["plays"]),
                    "epa": float(r["epa"]), "success": float(r["success"])})
    return out


def fit(rows, prior=None, half_life=HALF_LIFE, prior_plays=PRIOR_PLAYS, upto=None):
    """{team: (offense, defense)} in EPA per play relative to average.

    Offense is EPA gained above average, defense is EPA allowed above average
    (so lower is better). `rows` are the games to learn from, `upto` the week
    being predicted (recency is measured back from it), `prior` last season's
    {team: (o, d)} already regressed, worth `prior_plays` snaps per side.
    """
    teams = sorted({r["off"] for r in rows} | {r["def"] for r in rows} | set(prior or {}))
    if not teams:
        return {}
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    # Unknowns: league mean, home edge, n offenses, n defenses.
    k = 2 + 2 * n
    upto = upto if upto is not None else max((r["week"] for r in rows), default=0) + 1
    A, b, w = [], [], []
    for r in rows:
        if r["plays"] < 10:
            continue
        x = np.zeros(k)
        x[0] = 1.0
        x[1] = r["home"] - 0.5
        x[2 + idx[r["off"]]] = 1.0
        x[2 + n + idx[r["def"]]] = 1.0
        A.append(x)
        b.append(r["epa"] / r["plays"])
        age = upto - 1 - r["week"]
        w.append(r["plays"] * 0.5 ** (age / half_life if half_life else 0.0))
    # The prior, as pseudo-observations pulling each team toward last season.
    prior = prior or {}
    for t in teams:
        o, d = prior.get(t, (0.0, 0.0))
        for j, v in ((2 + idx[t], o), (2 + n + idx[t], d)):
            x = np.zeros(k)
            x[j] = 1.0
            A.append(x)
            b.append(v)
            w.append(prior_plays)
    # Pin the offense and defense averages to zero so the mean term carries it.
    for lo in (2, 2 + n):
        x = np.zeros(k)
        x[lo:lo + n] = 1.0
        A.append(x)
        b.append(0.0)
        w.append(1e4)
    A, b, sw = np.array(A), np.array(b), np.sqrt(np.array(w))
    sol = np.linalg.lstsq(A * sw[:, None], b * sw, rcond=None)[0]
    return {t: (float(sol[2 + idx[t]]), float(sol[2 + n + idx[t]])) for t in teams}


def season_prior(year, keep=PRIOR_KEEP, cache={}):
    """Last season's full-year rating, regressed toward average by `keep`."""
    if year not in cache:
        rows = game_rows(year - 1)
        full = fit(rows, half_life=0, prior_plays=1.0) if rows else {}
        cache[year] = full
    return {t: (keep * o, keep * d) for t, (o, d) in cache[year].items()}


def net(od):
    """{team: offense - defense} in EPA per play: positive = better than average."""
    return {t: o - d for t, (o, d) in od.items()}


def ratings_asof(year, week, live=False, **kw):
    """{team: (offense, defense)} knowable before `week` of `year` kicks off."""
    keep = kw.pop("keep", PRIOR_KEEP)
    rows = [r for r in game_rows(year, live=live) if r["week"] < week]
    return fit(rows, prior=season_prior(year, keep), upto=week, **kw)


def current(year):
    """This season's ratings for the dashboard, in points per game, plus the
    number of games played. Points are on FPI's scale: what a team would beat
    an average one by on a neutral field."""
    rows = game_rows(year, live=True)
    wk = max((r["week"] for r in rows), default=0) + 1
    od = ratings_asof(year, wk, live=True)
    played = {}
    for r in rows:
        played[r["off"]] = played.get(r["off"], 0) + 1
    if od:
        ds._report("NFL play-by-play rating (nflverse)", "live",
                   f"{len(rows) // 2} games through week {wk - 1}")
    return {t: {"pbp": round(PTS_PER_PLAY * (o - d), 1),
                "off": round(o, 3), "def": round(d, 3), "games": played.get(t, 0)}
            for t, (o, d) in od.items()}, wk - 1


if __name__ == "__main__":
    import datetime
    yr = datetime.date.today().year
    r, thru = current(yr)
    print(f"\nNFL play-by-play rating, {yr} through week {thru}  "
          f"(points vs an average team, neutral field)\n")
    print(f"  {'team':<6}{'rating':>8}{'off EPA/pl':>12}{'def EPA/pl':>12}{'games':>7}")
    for t, v in sorted(r.items(), key=lambda kv: -kv[1]["pbp"]):
        print(f"  {t:<6}{v['pbp']:>+8.1f}{v['off']:>+12.3f}{v['def']:>+12.3f}{v['games']:>7}")
