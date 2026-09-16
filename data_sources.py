"""Live data fetchers with three-level resilience: live API -> disk cache -> fallback snapshot.

Also works around routers whose DNS refuses lookups for ESPN's API domains by
resolving through public DNS (1.1.1.1 / 8.8.8.8) via `dig` when normal
resolution fails.

Optional: set CFBD_API_KEY (env var, or a `cfbd_key.txt` file next to this
script) to pull CFB talent + returning production live from
collegefootballdata.com instead of the bundled snapshots.
"""
import base64
import csv
import datetime
import io
import json
import os
import socket
import subprocess
import time
import urllib.request
import urllib.error

import fallback_data as fb

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Build report: list of dicts {source, mode, detail} — mode: live | cache | fallback
SOURCES = []

# When each league's weeks finish, derived from kickoff times while the schedule
# is fetched: {league: [[week, last_kickoff_iso], ...]}. The dashboard resolves
# the current week from this in the browser, so it stays right between weekly
# rebuilds — a build-time constant would go stale within a day, since a week is
# not over until its last game (Monday night in the NFL) has kicked off.
WEEK_ENDS = {}
CURRENT_WEEK = {}   # same answer at build time, for the build log


def _report(source, mode, detail=""):
    SOURCES.append({"source": source, "mode": mode, "detail": detail})
    print(f"  [{mode.upper():8s}] {source}" + (f" — {detail}" if detail else ""))


# ---------------- DNS workaround ----------------

_dns_cache = {}
_orig_getaddrinfo = socket.getaddrinfo


def _public_dns_resolve(host):
    if host in _dns_cache:
        return _dns_cache[host]
    for server in ("1.1.1.1", "8.8.8.8"):
        try:
            out = subprocess.run(
                ["dig", "+short", f"@{server}", host],
                capture_output=True, text=True, timeout=8,
            ).stdout.strip().splitlines()
            ips = [l.strip() for l in out if l.strip() and l.strip()[0].isdigit()]
            if ips:
                _dns_cache[host] = ips[-1]
                return ips[-1]
        except Exception:
            continue
    return None


def _patched_getaddrinfo(host, *args, **kwargs):
    try:
        return _orig_getaddrinfo(host, *args, **kwargs)
    except socket.gaierror:
        ip = _public_dns_resolve(host)
        if ip:
            return _orig_getaddrinfo(ip, *args, **kwargs)
        raise


socket.getaddrinfo = _patched_getaddrinfo


# ---------------- HTTP + cache ----------------

def _cache_path(name):
    return os.path.join(CACHE_DIR, name + ".json")


def http_get_json(url, cache_name, headers=None, retries=2):
    """GET a JSON URL. On success, cache to disk. On failure, serve the cache
    (any age). Returns (data, mode) where mode is 'live' or 'cache'.
    Raises if both fail."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.load(r)
            with open(_cache_path(cache_name), "w") as f:
                json.dump({"fetched_at": time.time(), "url": url, "data": data}, f)
            return data, "live"
        except Exception as e:  # noqa: BLE001 — any network/parse error falls through to cache
            last_err = e
            time.sleep(1 + attempt)
    cp = _cache_path(cache_name)
    if os.path.exists(cp):
        with open(cp) as f:
            blob = json.load(f)
        age_h = (time.time() - blob.get("fetched_at", 0)) / 3600
        return blob["data"], f"cache ({age_h:.0f}h old)"
    raise RuntimeError(f"{cache_name}: fetch failed and no cache — {last_err}")


def http_get_text(url, cache_name, headers=None, retries=2):
    """Text sibling of http_get_json — live, else the cached copy at any age."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    path = os.path.join(CACHE_DIR, cache_name)
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode(errors="replace")
            with open(path, "w") as f:
                f.write(text)
            return text, "live"
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1 + attempt)
    if os.path.exists(path):
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        return open(path).read(), f"cache ({age_h:.0f}h old)"
    raise RuntimeError(f"{cache_name}: fetch failed and no cache — {last_err}")


# ---------------- Name normalization ----------------

# ESPN name -> project canonical name (only where they differ)
ALIASES = {
    "UMass": "Massachusetts",
    "Florida Intl": "Florida International",
    "FIU": "Florida International",
    "App State": "App State",
    "Appalachian State": "App State",
    "UL Monroe": "UL Monroe",
    "Louisiana Monroe": "UL Monroe",
    "Hawaii": "Hawai'i",
    "San Jose State": "San José State",
    "Connecticut": "UConn",
    "Southern Mississippi": "Southern Miss",
    "Miami (OH)": "Miami (OH)",
    "Miami OH": "Miami (OH)",
    "Sam Houston State": "Sam Houston",
    # Pinnacle's spellings
    "Middle Tennessee State": "Middle Tennessee",
    "UL Lafayette": "Louisiana",
    # ESPN abbreviated shortDisplayNames
    "Arizona St": "Arizona State",
    "Arkansas St": "Arkansas State",
    "Boise St": "Boise State",
    "C Michigan": "Central Michigan",
    "Coastal": "Coastal Carolina",
    "Colorado St": "Colorado State",
    "E Michigan": "Eastern Michigan",
    "FAU": "Florida Atlantic",
    "FIU": "Florida International",
    "Florida St": "Florida State",
    "Fresno St": "Fresno State",
    "GA Southern": "Georgia Southern",
    "Georgia St": "Georgia State",
    "Jax State": "Jacksonville State",
    "Kansas St": "Kansas State",
    "Kennesaw St": "Kennesaw State",
    "MTSU": "Middle Tennessee",
    "Michigan St": "Michigan State",
    "Mississippi St": "Mississippi State",
    "Missouri St": "Missouri State",
    "N Dakota St": "North Dakota State",
    "N Illinois": "Northern Illinois",
    "New Mexico St": "New Mexico State",
    "Oklahoma St": "Oklahoma State",
    "Oregon St": "Oregon State",
    "Pitt": "Pittsburgh",
    "Sacramento St": "Sacramento State",
    "San Diego St": "San Diego State",
    "San José St": "San José State",
    "Texas St": "Texas State",
    "W Michigan": "Western Michigan",
    "Washington St": "Washington State",
    "Western KY": "Western Kentucky",
}


def canon(espn_name):
    return ALIASES.get(espn_name, espn_name)


# ---------------- ESPN fetchers ----------------

_PI_URL = ("https://site.web.api.espn.com/apis/fitt/v3/sports/football/{league}/"
           "powerindex?region=us&lang=en&limit=50&page={page}")


def fetch_fpi(league, cache_name):
    """Return dict {team_name: fpi} using ESPN power index (paginated)."""
    teams, mode = {}, "live"
    page, pages = 1, 1
    while page <= pages:
        data, m = http_get_json(_PI_URL.format(league=league, page=page),
                                f"{cache_name}_p{page}")
        if m != "live":
            mode = m
        pages = data.get("pagination", {}).get("pages", 1)
        for entry in data.get("teams", []):
            name = canon(entry["team"].get("shortDisplayName")
                         if league == "college-football"
                         else entry["team"].get("displayName"))
            cats = {c.get("name"): c.get("values") for c in entry.get("categories", [])}
            fpi_vals = cats.get("fpi")
            if fpi_vals:
                teams[name] = round(float(fpi_vals[0]), 1)
        page += 1
    return teams, mode


def get_cfb_fpi():
    try:
        teams, mode = fetch_fpi("college-football", "cfb_fpi")
        if len(teams) < 100:
            raise RuntimeError(f"only {len(teams)} CFB teams parsed")
        _report("CFB FPI (ESPN)", "live" if mode == "live" else "cache",
                f"{len(teams)} teams" + ("" if mode == "live" else f", {mode}"))
        return teams
    except Exception as e:
        _report("CFB FPI (ESPN)", "fallback", str(e))
        return dict(fb.CFB_FPI_SNAPSHOT)


def get_nfl_fpi():
    try:
        teams, mode = fetch_fpi("nfl", "nfl_fpi")
        if len(teams) < 32:
            raise RuntimeError(f"only {len(teams)} NFL teams parsed")
        _report("NFL FPI (ESPN)", "live" if mode == "live" else "cache",
                f"{len(teams)} teams" + ("" if mode == "live" else f", {mode}"))
        return teams
    except Exception as e:
        _report("NFL FPI (ESPN)", "fallback", str(e))
        return {t: v[0] for t, v in fb.NFL_T.items()}


# site.web.api works from datacenter IPs (GitHub Actions); site.api 403s there.
_SB_URL = ("https://site.web.api.espn.com/apis/site/v2/sports/football/{league}/"
           "scoreboard?dates={year}&seasontype=2&week={week}{extra}&limit=400")

_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _fmt_date(iso):
    """'2026-08-29T16:00Z' -> ('Sat Aug 29', (month, day)) in US-ish terms (date only)."""
    import datetime
    dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    dt = dt - datetime.timedelta(hours=5)  # shift to US eastern-ish for the display date
    return f"{_DOW[dt.weekday()]} {_MONTHS[dt.month]} {dt.day}", (dt.month, dt.day)


# Regular-season coverage. CFB: weeks 1-13, conference championships (14) and
# Army-Navy (15). NFL: the 18-week regular season.
CFB_WEEKS = tuple(range(1, 16))
# ESPN placeholder names for matchups the season hasn't decided yet
_TBD = {"TBD", "TBA"}
NFL_WEEKS = tuple(range(1, 19))


def _week_ends(kickoffs):
    """kickoffs: [(week_label, kickoff_utc)] -> [[week, last_kickoff_iso], ...]."""
    last = {}
    for w, k in kickoffs:
        if w not in last or k > last[w]:
            last[w] = k
    return [[w, last[w].astimezone(datetime.timezone.utc)
             .isoformat(timespec="seconds").replace("+00:00", "Z")]
            for w in sorted(last)]


def _current_week(week_ends):
    """The first week whose last game has not yet kicked off."""
    if not week_ends:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    for wk, iso in week_ends:
        if datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")) >= now:
            return wk
    return week_ends[-1][0] + 1


def _week_events(league, year, wk, extra, cache_name):
    data, mode = http_get_json(
        _SB_URL.format(league=league, year=year, week=wk, extra=extra), cache_name)
    return data.get("events", []), mode


def get_cfb_schedule(year, fbs_teams, weeks=None):
    """Full CFB regular season, one fetch per week.

    ESPN folds the late-August "Week 0" games into its week 1, so August games
    from that week are relabeled week 0; everything else keeps ESPN's week
    number. A week that fails to fetch is skipped rather than sinking the slate.
    """
    weeks = list(weeks if weeks is not None else CFB_WEEKS)
    games, modes, failed, tbd, malformed = [], [], [], 0, 0
    kickoffs = []
    market = get_pinnacle_lines("cfb")
    for wk in weeks:
        try:
            events, m = _week_events("college-football", year, wk, "&groups=80",
                                     f"cfb_sched_w{wk}")
        except Exception:  # noqa: BLE001 — one bad week shouldn't drop the rest
            failed.append(wk)
            continue
        modes.append(m)
        for ev in events:
            # ESPN occasionally emits an empty or partial event object; skip it
            # rather than sink an unattended build
            comp = next(iter(ev.get("competitions") or []), None)
            if not comp or not comp.get("competitors"):
                malformed += 1
                continue
            home = away = None
            for c in comp["competitors"]:
                nm = canon(c.get("team", {}).get("shortDisplayName"))
                if c.get("homeAway") == "home":
                    home = nm
                else:
                    away = nm
            if not home or not away:
                continue
            if home in _TBD or away in _TBD:
                # conference championship round: ESPN carries the slots with
                # placeholder teams until the season decides them
                tbd += 1
                continue
            if not ev.get("date"):
                malformed += 1
                continue
            date_str, (mo, _day) = _fmt_date(ev["date"])
            week_label = 0 if (wk == 1 and mo == 8) else wk
            kickoffs.append((week_label, datetime.datetime.fromisoformat(
                ev["date"].replace("Z", "+00:00"))))
            site = None
            if comp.get("neutralSite"):
                v = comp.get("venue", {})
                city = v.get("address", {}).get("city", "")
                site = ", ".join(x for x in [v.get("fullName", ""), city] if x) or "Neutral site"
            fbs = home in fbs_teams and away in fbs_teams
            games.append({"week": week_label, "date": date_str, "away": away,
                          "home": home, "site": site, "note": "",
                          "iso": ev["date"],
                          "mkt": market.get((home, away)),
                          "fbs": fbs, "fcs": (None if fbs else (away if home in fbs_teams else home))})

    # keep only games involving at least one FBS team, dedupe within each week
    games = [g for g in games if g["home"] in fbs_teams or g["away"] in fbs_teams]
    seen, out = set(), []
    for g in games:
        k = (g["week"], g["away"], g["home"])
        if k not in seen:
            seen.add(k)
            out.append(g)
    games = out

    WEEK_ENDS["cfb"] = _week_ends(kickoffs)
    CURRENT_WEEK["cfb"] = _current_week(WEEK_ENDS["cfb"])
    if not games:
        _report("CFB schedule (ESPN)", "fallback", f"unavailable — {_span(weeks)} all failed")
        return []
    _report("CFB schedule (ESPN)",
            "live" if all(m == "live" for m in modes) else "cache",
            _sched_detail(games, failed, tbd, malformed))
    return games


def get_nfl_schedule(year, weeks=None):
    """Full NFL regular season, one fetch per week."""
    weeks = list(weeks if weeks is not None else NFL_WEEKS)
    games, modes, failed, kickoffs, malformed = [], [], [], [], 0
    market = get_nfl_market_lines(year)
    for wk in weeks:
        try:
            events, m = _week_events("nfl", year, wk, "", f"nfl_sched_w{wk}")
        except Exception:  # noqa: BLE001 — one bad week shouldn't drop the rest
            failed.append(wk)
            continue
        modes.append(m)
        for ev in events:
            comp = next(iter(ev.get("competitions") or []), None)
            if not comp or not comp.get("competitors"):
                malformed += 1
                continue
            home = away = home_ab = away_ab = None
            for c in comp["competitors"]:
                t = c.get("team", {})
                nm, ab = t.get("displayName"), t.get("abbreviation")
                if c.get("homeAway") == "home":
                    home, home_ab = nm, ab
                else:
                    away, away_ab = nm, ab
            if not home or not away:
                continue
            if not ev.get("date"):
                malformed += 1
                continue
            date_str, _ = _fmt_date(ev["date"])
            site = None
            if comp.get("neutralSite"):
                v = comp.get("venue", {})
                addr = v.get("address", {})
                site = ", ".join(x for x in [addr.get("city", ""), addr.get("country", "")] if x) \
                    or v.get("fullName", "Neutral site")
            kickoffs.append((wk, datetime.datetime.fromisoformat(
                ev["date"].replace("Z", "+00:00"))))
            spread = market.get((wk, NV_ABBR.get(home_ab, home_ab),
                                 NV_ABBR.get(away_ab, away_ab)))
            games.append({"week": wk, "date": date_str, "away": away, "home": home,
                          "site": site, "iso": ev["date"], "mkt": spread})

    WEEK_ENDS["nfl"] = _week_ends(kickoffs)
    CURRENT_WEEK["nfl"] = _current_week(WEEK_ENDS["nfl"])
    if not games:
        _report("NFL schedule (ESPN)", "fallback", f"unavailable — {_span(weeks)} all failed")
        return []
    _report("NFL schedule (ESPN)",
            "live" if all(m == "live" for m in modes) else "cache",
            _sched_detail(games, failed, malformed=malformed))
    return games


def get_results(league, year, weeks):
    """{(week, away, home): (away_pts, home_pts)} for games that have finished.

    Team names are derived exactly as get_cfb_schedule/get_nfl_schedule derive
    them, so a result lines up with the play recorded against it without a
    second, lossy name-matching layer in between. Unfinished games are simply
    absent, which is what lets the ledger grade a week as it completes.
    """
    espn = "college-football" if league == "cfb" else "nfl"
    extra = "&groups=80" if league == "cfb" else ""
    out = {}
    for wk in weeks:
        try:
            events, _ = _week_events(espn, year, wk, extra,
                                     f"res_{league}_{year}_w{wk}")
        except Exception:  # noqa: BLE001 — one bad week shouldn't sink grading
            continue
        for ev in events:
            comp = next(iter(ev.get("competitions") or []), None)
            if not comp or not comp.get("competitors"):
                continue
            if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FINAL":
                continue
            home = away = h_pts = a_pts = None
            for c in comp["competitors"]:
                t = c.get("team", {})
                nm = canon(t.get("shortDisplayName")) if league == "cfb" \
                    else t.get("displayName")
                try:
                    sc = int(c.get("score"))
                except (TypeError, ValueError):
                    continue
                if c.get("homeAway") == "home":
                    home, h_pts = nm, sc
                else:
                    away, a_pts = nm, sc
            if not home or not away or h_pts is None or a_pts is None:
                continue
            # the same week-0 relabel the CFB schedule applies
            wk_label = wk
            if league == "cfb" and wk == 1 and ev.get("date"):
                try:
                    if int(ev["date"][5:7]) == 8:
                        wk_label = 0
                except ValueError:
                    pass
            out[(wk_label, away, home)] = (a_pts, h_pts)
    return out


def _span(weeks):
    """'week 7' / 'weeks 1-18' — contiguous runs collapse to a range."""
    if len(weeks) == 1:
        return f"week {weeks[0]}"
    if weeks == list(range(weeks[0], weeks[-1] + 1)):
        return f"weeks {weeks[0]}-{weeks[-1]}"
    return "weeks " + ", ".join(map(str, weeks))


def _sched_detail(games, failed, tbd=0, malformed=0):
    wks = sorted({g["week"] for g in games})
    detail = f"{len(games)} games, {_span(wks)}"
    notes = []
    if failed:
        notes.append(f"week{'s' if len(failed) > 1 else ''} {', '.join(map(str, failed))} unavailable")
    if tbd:
        notes.append(f"{tbd} matchup{'s' if tbd > 1 else ''} still TBD")
    if malformed:
        notes.append(f"{malformed} malformed event{'s' if malformed > 1 else ''} skipped")
    if notes:
        detail += " (" + "; ".join(notes) + ")"
    return detail


# ---------------- Pinnacle market lines (credentials required) ----------------
#
# Off unless credentials are present, which is deliberate: the public CI build
# holds no secrets, so it publishes no licensed odds. A local or private-host
# build with PS3838_* set gets the lines. Read from the environment or a
# gitignored pinnacle_env.txt — never hard-coded, never committed.

_PIN_SPORT_FOOTBALL = 15
PIN_LEAGUES = {"cfb": 880, "nfl": 889}


def _env_file():
    """key=value pairs from the gitignored secrets file, if there is one.

    One file holds every server-side secret. deploy.sh excludes it from the
    rsync, so a redeploy cannot delete what is only on the server.
    """
    out = {}
    path = os.path.join(BASE, "pinnacle_env.txt")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _pin_env():
    env = {k: os.environ.get(k) for k in
           ("PS3838_BASE_URL", "PS3838_USERNAME", "PS3838_PASSWORD", "PS3838_PROXY")}
    for k, v in _env_file().items():
        env[k] = env.get(k) or v
    if not (env.get("PS3838_USERNAME") and env.get("PS3838_PASSWORD")):
        return None
    env["PS3838_BASE_URL"] = env.get("PS3838_BASE_URL") or "https://api.probet42.com"
    return env


def _pin_get(env, path, retries=3, **params):
    """One API call, optionally through a residential proxy.

    The book answers a residential IP and returns 403 to a datacenter one, so a
    build running on a server needs an exit node that isn't AWS — set
    PS3838_PROXY. Residential exits are unreliable by nature: an individual
    call can come back as a 504 on the CONNECT tunnel and succeed on the
    retry, so transient failures are retried. 401/403 are not retried, because
    those are a refusal rather than a flake and hammering them helps nobody.
    """
    tok = base64.b64encode(
        f"{env['PS3838_USERNAME']}:{env['PS3838_PASSWORD']}".encode()).decode()
    q = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    req = urllib.request.Request(f"{env['PS3838_BASE_URL']}{path}?{q}",
                                 headers={"Authorization": f"Basic {tok}",
                                          "User-Agent": "Mozilla/5.0"})
    proxy = env.get("PS3838_PROXY")
    opener = (urllib.request.build_opener(
                  urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
              if proxy else urllib.request.build_opener())
    last = None
    for attempt in range(retries):
        try:
            with opener.open(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise
            last = e
        except Exception as e:  # noqa: BLE001 — tunnel drops, resets, timeouts
            last = e
        time.sleep(1 + attempt)
    raise last


def get_pinnacle_lines(league):
    """{(home, away): home_margin} in project-canonical names, home-positive.

    Pinnacle quotes `hdp` as the HOME handicap, so a home favourite is negative
    there; the sign is flipped to match the rest of this codebase. Full-game
    period only (0), open lines only (status 1), and of the offered spreads the
    one with the most balanced juice — the main line, not an alternate.
    """
    env = _pin_env()
    label = f"{league.upper()} market lines (Pinnacle)"
    if not env:
        _report(label, "fallback", "no credentials — set PS3838_USERNAME/PASSWORD to enable")
        return {}
    try:
        lid = PIN_LEAGUES[league]
        fx = _pin_get(env, "/v3/fixtures", sportId=_PIN_SPORT_FOOTBALL, leagueIds=lid)
        names = {e["id"]: (e.get("home"), e.get("away"))
                 for lg in fx.get("league", []) for e in lg.get("events", [])}
        od = _pin_get(env, "/v4/odds", sportId=_PIN_SPORT_FOOTBALL, leagueIds=lid,
                      oddsFormat="American")
        out = {}
        for lg in od.get("leagues", []):
            for e in lg.get("events", []):
                p0 = next((p for p in e.get("periods", [])
                           if p.get("number") == 0 and p.get("status") == 1), None)
                if not p0 or not p0.get("spreads") or e["id"] not in names:
                    continue
                main = sorted(p0["spreads"],
                              key=lambda s: abs((s.get("home") or 0) - (s.get("away") or 0)))[0]
                if main.get("hdp") is None:
                    continue
                home, away = names[e["id"]]
                if not home or not away:
                    continue
                out[(canon(home), canon(away))] = -float(main["hdp"])
        _report(label, "live", f"{len(out)} games priced"
                + (" via proxy" if env.get("PS3838_PROXY") else ""))
        return out
    except Exception as e:  # noqa: BLE001
        _report(label, "fallback", f"unavailable — {e}")
        return {}


# ---------------- NFL market lines (nflverse, free, no key) ----------------

_NFLVERSE_GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

# ESPN abbreviation -> nflverse abbreviation (the only two that differ)
NV_ABBR = {"LAR": "LA", "WSH": "WAS"}


def get_nfl_market_lines(year):
    """{(week, home_abbr, away_abbr): spread} for `year`, home-positive.

    nflverse carries the number for games that are already priced and leaves it
    blank for the rest, so later weeks fill in as books post them.
    """
    try:
        text, mode = http_get_text(_NFLVERSE_GAMES, "nflverse_games.csv")
    except Exception as e:  # noqa: BLE001
        _report("NFL market lines (nflverse)", "fallback", f"unavailable — {e}")
        return {}
    lines, scheduled = {}, 0
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("season") != str(year) or r.get("game_type") != "REG":
            continue
        scheduled += 1
        if not r.get("spread_line"):
            continue
        try:
            lines[(int(r["week"]), r["home_team"], r["away_team"])] = float(r["spread_line"])
        except ValueError:
            continue
    _report("NFL market lines (nflverse)", "live" if mode == "live" else "cache",
            f"{len(lines)} of {scheduled} games priced"
            + ("" if mode == "live" else f", {mode}"))
    return lines


# ---------------- CFBD (optional, key required) ----------------

def _cfbd_key():
    k = os.environ.get("CFBD_API_KEY")
    if k:
        return k.strip()
    kf = os.path.join(BASE, "cfbd_key.txt")
    if os.path.exists(kf):
        return open(kf).read().strip()
    return _env_file().get("CFBD_API_KEY")


def get_cfb_talent(year):
    key = _cfbd_key()
    if key:
        try:
            # The current cycle's composite is the right input for rating the
            # current season. It appears once that cycle closes, so the prior
            # year is the fallback while it is still missing rather than the
            # default it used to be.
            for yr in (year, year - 1):
                data, m = http_get_json(
                    f"https://api.collegefootballdata.com/talent?year={yr}",
                    f"cfbd_talent_{yr}", headers={"Authorization": f"Bearer {key}"})
                # CFBD renamed this field from `school` to `team`; accept either
                # so the build survives the rename in both directions.
                talent = {canon(d.get("team") or d.get("school")): float(d["talent"])
                          for d in data
                          if d.get("talent") is not None and (d.get("team") or d.get("school"))}
                if talent:
                    _report("Talent composite (CFBD)",
                            "live" if m == "live" else "cache",
                            f"{len(talent)} teams, {yr} composite")
                    return talent
        except Exception as e:
            _report("Talent composite (CFBD)", "fallback", str(e))
            return dict(fb.CFB_TALENT)
    _report("Talent composite", "fallback", "snapshot (247Sports 2025) — set CFBD_API_KEY to automate")
    return dict(fb.CFB_TALENT)


def get_cfb_retprod(year):
    key = _cfbd_key()
    if key:
        try:
            data, m = http_get_json(
                f"https://api.collegefootballdata.com/player/returning?year={year}",
                "cfbd_returning", headers={"Authorization": f"Bearer {key}"})
            rp = {}
            for d in data:
                pct = d.get("percentPPA")
                if pct is not None:
                    team = canon(d["team"])
                    qb = fb.CFB_RETPROD.get(team, (None, None))[1]  # QB status stays curated
                    rp[team] = (round(float(pct) * 100), qb)
            if rp:
                _report("Returning production (CFBD)", "live" if m == "live" else "cache",
                        f"{len(rp)} teams (QB status still curated)")
                return rp
        except Exception as e:
            _report("Returning production (CFBD)", "fallback", str(e))
            return dict(fb.CFB_RETPROD)
    _report("Returning production", "fallback", "snapshot — set CFBD_API_KEY to automate")
    return dict(fb.CFB_RETPROD)


def get_cfb_portal():
    # No free machine-readable On3 source; stays a curated snapshot.
    _report("Portal net movement", "fallback", "curated snapshot (On3) — no free API exists")
    return dict(fb.CFB_PORTAL)
