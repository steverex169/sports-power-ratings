"""The play rules, in one place so the page and the record cannot drift apart.

build.py injects these constants into the dashboard, so the card a reader sees
and the ledger kept against it are scored by the same numbers rather than by
two copies that slowly disagree.

The Python side is what gets written down: a snapshot taken at Base weighting,
before kickoff. That is the only version that can honestly be graded later —
FPI refreshes weekly, so a pick recomputed after the fact is not the pick that
was made.

What the rules are, and why:

  bettable   the book's number is inside 17. Past that, lines are priced off a
             different distribution and the model has no business there.
  play       at least a point off the number.
  tier       a G5 team on either side makes the game a no-bet. It is still
             recorded and still graded, so the rule can be checked rather than
             believed.

The edge curve peaks at 3-5 points and is discounted past 7 deliberately.
That shape is measured: replaying 816 NFL games against closing lines
(backtest.py), disagreements of 4+ points went 42.9% while the whole set went
48.1%. Past a touchdown a disagreement is more often a stale injury than a soft
number, so size alone is treated as a warning rather than a reason.
"""
import datetime

REGW = {"C": 0.7, "B": 1.0, "A": 1.4}
DECAY = {"cfb": {"hold": 2, "fade": 8}, "nfl": {"hold": 9, "fade": 9}}
BETTABLE_MAX = 17.0
PLAY_MIN = 1.0
KEY_NUMS = [3, 7, 10, 14]
CONF_ORDER = ["Low", "Low+", "Med", "High"]

# Edge buckets the ledger reports against. Break-even at -110 is 52.4%.
BUCKETS = [("1 to 1.5", 1.0, 1.5), ("1.5 to 3", 1.5, 3.0), ("3 to 5", 3.0, 5.0),
           ("5 to 7", 5.0, 7.0), ("7 and up", 7.0, 999.0)]
BREAK_EVEN = 52.4


def rules_for_page():
    """The subset the dashboard needs, injected as JSON at build time."""
    return {"REGW": REGW, "DECAY": DECAY, "BETTABLE_MAX": BETTABLE_MAX,
            "PLAY_MIN": PLAY_MIN, "KEY_NUMS": KEY_NUMS, "CONF_ORDER": CONF_ORDER}


def decay(lg, wk):
    d = DECAY[lg]
    if wk is None or wk <= d["hold"]:
        return 1.0
    return max(0.25, 1 - (wk - d["hold"]) / d["fade"] * 0.75)


def tilt(row, lg):
    if lg == "cfb":
        return (row.get("tt") or 0) + (row.get("padj") or 0) + (row.get("rpadj") or 0)
    return (row.get("reg") or 0) + (row.get("qb") or 0) + (row.get("ros") or 0)


def rating(row, lg, wk, regime="B"):
    return round(row["fpi"] + REGW[regime] * decay(lg, wk) * tilt(row, lg), 1)


def keys_crossed(a, b):
    """Key numbers the line moves through, in signed home-margin space, so a
    move across zero and into a number on the other side still counts."""
    lo, hi = min(a, b), max(a, b)
    if lo == hi:
        return []
    return [k for k in KEY_NUMS if (lo <= k <= hi) or (lo <= -k <= hi)]


def play_rank(edge, unc, keys, conf):
    if edge < PLAY_MIN:
        return 0.0
    if edge < 1.5:
        s = 0.75
    elif edge < 3:
        s = 1 + (edge - 1.5) / 1.5
    elif edge <= 5:
        s = 3 + (edge - 3) / 2
    elif edge <= 7:
        s = 3.5 - (edge - 5) * 0.25
    else:
        s = max(0.5, 3 - (edge - 7) / 2)
    r = (edge / unc) if unc else 2
    if r >= 1:
        s += 0.5
    elif r < 0.5:
        s -= 1
    if keys:
        s += 0.5
    if conf == "High":
        s += 0.25
    elif conf in ("Low", "Low+"):
        s -= 0.5
    return round(max(0.0, min(5.0, s)), 2)


def _conf_of(h, a):
    def i(x):
        return CONF_ORDER.index(x) if x in CONF_ORDER else 1
    return CONF_ORDER[min(i(h.get("conf_lvl")), i(a.get("conf_lvl")))]


def _kickoff(g):
    iso = g.get("iso")
    if not iso:
        return None
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_plays(lg, games, rows, now=None, horizon_days=None):
    """Every game that clears the bar and has not kicked off yet.

    Filtering on kickoff rather than on week number matters more than it looks:
    the weekly rebuild runs Monday morning, when the week's Sunday games are
    already played but the week number has not turned over. A week-based filter
    would happily record a "play" on a game whose result was known.

    `horizon_days` bounds the other side. The ledger only commits to games
    inside the coming week, because a line seen eleven days out is not the
    number anyone could have bet.
    """
    by = {r["team"]: r for r in rows}
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = []
    for g in games:
        if g.get("mkt") is None or g.get("fbs") is False:
            continue
        ko = _kickoff(g)
        if ko is None or ko <= now:
            continue
        if horizon_days is not None and (ko - now).total_seconds() > horizon_days * 86400:
            continue
        mkt = float(g["mkt"])
        if abs(mkt) > BETTABLE_MAX:
            continue
        h, a = by.get(g["home"]), by.get(g["away"])
        if not h or not a:
            continue
        wk = g["week"]
        hfa = 0.0 if g.get("site") else h["hfa"]
        margin = round(rating(h, lg, wk) - rating(a, lg, wk) + hfa, 1)
        edge = round(margin - mkt, 1)
        if abs(edge) < PLAY_MIN:
            continue
        side = g["home"] if edge >= 0 else g["away"]
        mkt_fav = g["home"] if mkt >= 0 else g["away"]
        unc = round((h["band"] ** 2 + a["band"] ** 2) ** 0.5, 1)
        tier = ("G5" if (h.get("tier") == "G5" or a.get("tier") == "G5") else "P4") \
            if lg == "cfb" else None
        conf = _conf_of(h, a) if lg == "cfb" else None
        keys = keys_crossed(mkt, margin)
        stars = 0.0 if tier == "G5" else play_rank(abs(edge), unc, keys, conf)
        out.append({
            "week": wk, "date": g["date"], "away": g["away"], "home": g["home"],
            "neutral": bool(g.get("site")), "mkt": mkt, "model": margin,
            "edge": edge, "abs_edge": round(abs(edge), 1), "side": side,
            "getting": round(-abs(mkt) if side == mkt_fav else abs(mkt), 1),
            "tier": tier, "conf": conf, "band": unc, "keys": keys, "stars": stars,
        })
    out.sort(key=lambda p: (-p["stars"], -p["abs_edge"]))
    return out
