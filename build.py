"""One-command pipeline: fetch live data -> run models -> regenerate index.html.

Usage:
    .venv/bin/python build.py            # fetch + build
    .venv/bin/python build.py --open     # fetch + build + open in browser
    .venv/bin/python build.py --offline  # skip network, use cache/fallbacks
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
import models


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open the dashboard when done")
    ap.add_argument("--offline", action="store_true", help="skip network, use cache/fallbacks")
    args = ap.parse_args()

    year = 2026
    if args.offline:
        os.environ["no_proxy"] = "*"
        # crude but effective: point fetchers at an unreachable proxy so they fall to cache
        os.environ["https_proxy"] = "http://127.0.0.1:9"

    print("Fetching data…")
    cfb_fpi = ds.get_cfb_fpi()
    nfl_fpi = ds.get_nfl_fpi()
    talent = ds.get_cfb_talent(year)
    retprod = ds.get_cfb_retprod(year)
    portal = ds.get_cfb_portal()
    cfb_games = ds.get_cfb_schedule(year, set(cfb_fpi))
    nfl_games = ds.get_nfl_schedule(year)

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

    print("Building dashboard…")
    with open(os.path.join(BASE, "template.html"), encoding="utf-8") as f:
        html = f.read()

    updated = datetime.datetime.now().strftime("%b %d, %Y %H:%M")
    j = lambda x: json.dumps(x, ensure_ascii=False)
    html = (html
            .replace("__CFB_DATA__", j(models.cfb_rows_for_dashboard(cfb_df)))
            .replace("__CFB_GAMES__", j(cfb_games))
            .replace("__NFL_DATA__", j(models.nfl_rows_for_dashboard(nfl_df)))
            .replace("__NFL_GAMES__", j(nfl_games))
            .replace("__SOURCES__", j(ds.SOURCES))
            .replace("__UPDATED__", updated))

    out = os.path.join(BASE, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Done: {out} ({os.path.getsize(out)//1024} KB) | updated {updated}")
    print("Saved: cfb_ratings_v2.csv, nfl_ratings.csv")

    if args.open:
        subprocess.run(["open", out], check=False)


if __name__ == "__main__":
    main()
