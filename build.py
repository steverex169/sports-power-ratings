"""One-command pipeline: fetch live data -> run models -> regenerate index.html.

Usage:
    .venv/bin/python build.py                    # full season, fetch + build
    .venv/bin/python build.py --open             # + open in browser
    .venv/bin/python build.py --offline          # skip network, use cache/fallbacks
    .venv/bin/python build.py --cfb-weeks 1-4    # only part of the slate
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import data_sources as ds
import fallback_data as fb
import injuries
import keynumbers
import ledger
import models
import playrules


def parse_weeks(spec, default):
    """Week selector: 'all' -> the league default, '1-15' -> a range,
    '1,2,5' -> a list (the forms combine: '0-2,14')."""
    spec = (spec or "").strip().lower()
    if not spec or spec == "all":
        return list(default)
    weeks = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            weeks.update(range(int(lo), int(hi) + 1))
        else:
            weeks.add(int(part))
    if not weeks:
        raise ValueError(f"no weeks parsed from {spec!r}")
    return sorted(weeks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open the dashboard when done")
    ap.add_argument("--offline", action="store_true", help="skip network, use cache/fallbacks")
    ap.add_argument("--cfb-weeks", default="all", metavar="SPEC",
                    help="CFB weeks to fetch: 'all' (default, 1-15), '1-4', or '1,2,5'")
    ap.add_argument("--nfl-weeks", default="all", metavar="SPEC",
                    help="NFL weeks to fetch: 'all' (default, 1-18), '1-4', or '1,2,5'")
    args = ap.parse_args()

    cfb_weeks = parse_weeks(args.cfb_weeks, ds.CFB_WEEKS)
    nfl_weeks = parse_weeks(args.nfl_weeks, ds.NFL_WEEKS)

    year = 2026
    if args.offline:
        os.environ["no_proxy"] = "*"
        # crude but effective: point fetchers at an unreachable proxy so they fall to cache
        os.environ["https_proxy"] = "http://127.0.0.1:9"

    print(f"Fetching data… (CFB {ds._span(cfb_weeks)}, NFL {ds._span(nfl_weeks)})")
    cfb_fpi = ds.get_cfb_fpi()
    nfl_fpi = ds.get_nfl_fpi()
    talent = ds.get_cfb_talent(year)
    retprod = ds.get_cfb_retprod(year)
    portal = ds.get_cfb_portal()
    cfb_games = ds.get_cfb_schedule(year, set(cfb_fpi), cfb_weeks)
    nfl_games = ds.get_nfl_schedule(year, nfl_weeks)
    # Absences move this week's margins only, never the season ratings.
    injuries.apply(nfl_games, injuries.get_nfl_injuries())

    # Flag any FPI teams with no conference mapping (e.g. realignment/new FBS teams)
    unknown = sorted(t for t in cfb_fpi if t not in fb.CFB_CONF)
    if unknown:
        print(f"  [NOTE    ] {len(unknown)} team(s) missing from conference map: {', '.join(unknown)}")

    print("Running models…")
    cfb_df = models.cfb_model(cfb_fpi, talent, portal, retprod)
    nfl_df = models.nfl_model(nfl_fpi)
    print(f"  CFB: {len(cfb_df)} teams | #1 {cfb_df.iloc[0].Team} {cfb_df.iloc[0].MyRating:.1f}")
    print(f"  NFL: {len(nfl_df)} teams | #1 {nfl_df.iloc[0].Team} {nfl_df.iloc[0].MyRating:.1f}"
          f" | win-total fit r={nfl_df.attrs['win_fit_r']:.3f}")

    cfb_df.to_csv(os.path.join(BASE, "cfb_ratings_v2.csv"), index=False)
    nfl_df.to_csv(os.path.join(BASE, "nfl_ratings.csv"), index=False)

    # 15 completed seasons of closing lines; the current one is excluded so the
    # tables describe finished games only.
    key_numbers = keynumbers.compute(set(range(year - 16, year)))
    if key_numbers:
        print(f"  Key numbers: {key_numbers['games']} NFL games, "
              f"{key_numbers['years'][0]}-{key_numbers['years'][1]}")

    updated = datetime.datetime.now().strftime("%b %d, %Y %H:%M")
    built_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    cfb_rows = models.cfb_rows_for_dashboard(cfb_df)
    nfl_rows = models.nfl_rows_for_dashboard(nfl_df)

    # The record. Plays are written down at first sighting and never recomputed,
    # so a later build can grade them but cannot quietly improve them.
    # How often games actually finish on each number, so a line moving through
    # 3 is scored above one moving through dead space.
    mass = playrules.key_mass((key_numbers or {}).get("margins"))

    print("Updating ledger…")
    led = ledger.load()
    added = 0
    for lg, games, rows in (("cfb", cfb_games, cfb_rows), ("nfl", nfl_games, nfl_rows)):
        # Only games kicking off within the week: a line seen eleven days out
        # is not a number anyone could have taken.
        plays = playrules.build_plays(lg, games, rows, horizon_days=7, mass=mass)
        added += ledger.record(led, lg, plays, built_iso)
    graded = ledger.grade(led, year, ds.get_results)
    ledger.save(led)
    tracker = ledger.summary(led)
    print(f"  {added} new play(s) recorded, {graded} newly graded | "
          f"{tracker['graded']} graded, {tracker['pending']} pending")
    if tracker["overall"]["n"]:
        o = tracker["overall"]
        print(f"  Standing record: {o['w']}-{o['l']} ({o['pct']}%) "
              f"vs {tracker['break_even']}% break-even")

    print("Building dashboard…")
    with open(os.path.join(BASE, "template.html"), encoding="utf-8") as f:
        html = f.read()

    j = lambda x: json.dumps(x, ensure_ascii=False)
    html = (html
            .replace("__CFB_DATA__", j(cfb_rows))
            .replace("__CFB_GAMES__", j(cfb_games))
            .replace("__NFL_DATA__", j(nfl_rows))
            .replace("__NFL_GAMES__", j(nfl_games))
            .replace("__SOURCES__", j(ds.SOURCES))
            .replace("__WEEK_ENDS_CFB__", j(ds.WEEK_ENDS.get("cfb", [])))
            .replace("__WEEK_ENDS_NFL__", j(ds.WEEK_ENDS.get("nfl", [])))
            .replace("__KEY_NUMBERS__", j(key_numbers))
            .replace("__PLAY_RULES__", j(playrules.rules_for_page()))
            .replace("__KEY_MASS__", j(mass))
            .replace("__TRACKER__", j(tracker))
            .replace("__UPDATED__", updated)
            .replace("__BUILT_ISO__", built_iso))

    out = os.path.join(BASE, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Done: {out} ({os.path.getsize(out)//1024} KB) | updated {updated}")
    def slate(label, games):
        wks = len({g["week"] for g in games})
        return f"{len(games)} {label} games across {wks} week{'s' if wks != 1 else ''}"
    print(f"Slate: {slate('CFB', cfb_games)}, {slate('NFL', nfl_games)}")
    print(f"Current week: CFB {ds.CURRENT_WEEK.get('cfb')}, NFL {ds.CURRENT_WEEK.get('nfl')}")
    print(f"Saved: cfb_ratings_v2.csv, nfl_ratings.csv, {os.path.relpath(ledger.PATH, BASE)}")

    if args.open:
        subprocess.run(["open", out], check=False)


if __name__ == "__main__":
    main()
