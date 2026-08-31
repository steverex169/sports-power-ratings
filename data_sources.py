"""Live data fetchers with three-level resilience: live API -> disk cache -> fallback snapshot.

Also works around routers whose DNS refuses lookups for ESPN's API domains by
resolving through public DNS (1.1.1.1 / 8.8.8.8) via `dig` when normal
resolution fails.

Optional: set CFBD_API_KEY (env var, or a `cfbd_key.txt` file next to this
script) to pull CFB talent + returning production live from
collegefootballdata.com instead of the bundled snapshots.
"""
import datetime
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

# Which week each league is currently in, derived from kickoff times while the
# schedule is fetched. Preseason -> the first week; season over -> last week + 1.
CURRENT_WEEK = {}


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


def _current_week(kickoffs):
    """kickoffs: [(week_label, kickoff_utc)] -> week now being played / next up."""
    if not kickoffs:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    upcoming = [w for w, k in kickoffs if k >= now]
    return min(upcoming) if upcoming else max(w for w, _ in kickoffs) + 1


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

    CURRENT_WEEK["cfb"] = _current_week(kickoffs)
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
            home = away = None
            for c in comp["competitors"]:
                nm = c.get("team", {}).get("displayName")
                if c.get("homeAway") == "home":
                    home = nm
                else:
                    away = nm
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
            games.append({"week": wk, "date": date_str, "away": away, "home": home, "site": site})

    CURRENT_WEEK["nfl"] = _current_week(kickoffs)
    if not games:
        _report("NFL schedule (ESPN)", "fallback", f"unavailable — {_span(weeks)} all failed")
        return []
    _report("NFL schedule (ESPN)",
            "live" if all(m == "live" for m in modes) else "cache",
            _sched_detail(games, failed, malformed=malformed))
    return games


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


# ---------------- CFBD (optional, key required) ----------------

def _cfbd_key():
    k = os.environ.get("CFBD_API_KEY")
    if k:
        return k.strip()
    kf = os.path.join(BASE, "cfbd_key.txt")
    if os.path.exists(kf):
        return open(kf).read().strip()
    return None


def get_cfb_talent(year):
    key = _cfbd_key()
    if key:
        try:
            data, m = http_get_json(
                f"https://api.collegefootballdata.com/talent?year={year - 1}",
                "cfbd_talent", headers={"Authorization": f"Bearer {key}"})
            talent = {canon(d["school"]): float(d["talent"]) for d in data}
            if talent:
                _report("Talent composite (CFBD)", "live" if m == "live" else "cache",
                        f"{len(talent)} teams")
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
