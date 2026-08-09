"""Rating models as pure functions of their input data.

cfb_model(...)  -> DataFrame with one row per FBS team
nfl_model(...)  -> DataFrame with one row per NFL team

Both include regime variants (Conservative 0.7x / Base 1.0x / Aggressive 1.4x
applied to the total tilt), an uncertainty band, and the fields the dashboard
template consumes.
"""
import numpy as np
import pandas as pd

import fallback_data as fb

REGIMES = {"C": 0.7, "B": 1.0, "A": 1.4}


# ---------------- CFB ----------------

def cfb_model(fpi, talent, portal, retprod, conf_map=None, hfa=None):
    conf_map = conf_map or fb.CFB_CONF
    hfa = hfa or fb.CFB_HFA

    rows = []
    for team, f in fpi.items():
        rows.append({"Team": team, "Conf": conf_map.get(team, "—"), "FPI": f})
    base = pd.DataFrame(rows).sort_values("FPI", ascending=False).reset_index(drop=True)
    base["FPI_Rank"] = np.arange(1, len(base) + 1)

    base["Talent"] = base["Team"].map(talent)
    base["PortalNet"] = base["Team"].map(portal)
    base["RetProd"] = base["Team"].map(lambda t: retprod[t][0] if t in retprod else np.nan)
    base["QB"] = base["Team"].map(lambda t: retprod[t][1] if t in retprod else None)
    base["HFA"] = base["Team"].map(lambda t: hfa.get(t, fb.CFB_HFA_DEFAULT))

    P4 = {"SEC", "Big Ten", "Big 12", "ACC"}
    is_p4 = base["Conf"].isin(P4) | (base["Team"] == "Notre Dame")
    base["TalentTier"] = np.where(is_p4, "P4", "G5")

    def fit(mask):  # tier-relative talent -> implied rating (separate OLS per tier)
        m = mask & base["Talent"].notna() & base["FPI"].notna()
        x, y = base.loc[m, "Talent"].values, base.loc[m, "FPI"].values
        b1, b0 = np.polyfit(x, y, 1)
        return b0, b1

    b0P, b1P = fit(is_p4)
    b0G, b1G = fit(~is_p4)

    def implied(row):
        if pd.isna(row["Talent"]):
            return row["FPI"]
        if row["TalentTier"] == "P4":
            return b0P + b1P * row["Talent"]
        return b0G + b1G * row["Talent"]

    base["TalentImplied"] = base.apply(implied, axis=1)

    BETA_T = 0.22; CAP_P4 = 4.0; CAP_G5 = 2.5
    BETA_P = 0.10; BETA_RP = 0.07; CAP_RP = 3.0
    QB_ADJ = {"Y": 0.7, "T": -0.2, "N": -0.8}
    rp_mean = base["RetProd"].mean()

    def talent_tilt(row):
        raw = BETA_T * (row["TalentImplied"] - row["FPI"])
        cap = CAP_P4 if row["TalentTier"] == "P4" else CAP_G5
        return float(np.clip(raw, -cap, cap))

    base["TalentTilt"] = base.apply(talent_tilt, axis=1)
    base["PortalAdj"] = base["PortalNet"].fillna(0) * BETA_P
    base["RetTilt"] = np.clip((base["RetProd"] - rp_mean).fillna(0) * BETA_RP, -CAP_RP, CAP_RP)
    base["QBAdj"] = base["QB"].map(lambda q: QB_ADJ.get(q, 0.0))
    base["RetProdAdj"] = base["RetTilt"] + base["QBAdj"]
    base["Tilt"] = base["TalentTilt"] + base["PortalAdj"] + base["RetProdAdj"]
    for k, mult in REGIMES.items():
        base[f"My_{k}"] = base["FPI"] + mult * base["Tilt"]
    base["MyRating"] = base["My_B"]
    base["Edge"] = base["MyRating"] - base["FPI"]

    def band(row):
        present = sum(pd.notna(row[c]) for c in ["Talent", "PortalNet", "RetProd"])
        adj = [row["TalentTilt"], row["PortalAdj"], row["RetProdAdj"]]
        pos = sum(a for a in adj if a > 0)
        neg = -sum(a for a in adj if a < 0)
        return round(float(np.clip(2.5 + 0.5 * (3 - present) + 0.8 * min(pos, neg), 2.5, 6.5)), 1)

    base["Band"] = base.apply(band, axis=1)

    def conf(row):
        present = sum(pd.notna(row[c]) for c in ["Talent", "PortalNet", "RetProd"])
        if row["TalentTier"] == "P4" and present == 3:
            return "High"
        if row["TalentTier"] == "P4" and present >= 2:
            return "Med"
        return "Low+" if present >= 1 else "Low"

    base["Confidence"] = base.apply(conf, axis=1)

    base = base.sort_values("MyRating", ascending=False).reset_index(drop=True)
    base["MyRank"] = np.arange(1, len(base) + 1)
    return base


def cfb_rows_for_dashboard(df):
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "rank": int(r.MyRank), "team": r.Team, "conf": r.Conf, "tier": r.TalentTier,
            "fpi": round(r.FPI, 1),
            "talent": None if pd.isna(r.Talent) else int(round(r.Talent)),
            "portal": None if pd.isna(r.PortalNet) else int(r.PortalNet),
            "retprod": None if pd.isna(r.RetProd) else int(r.RetProd),
            "qb": r.QB if isinstance(r.QB, str) else None,
            "hfa": round(r.HFA, 1),
            "tt": round(r.TalentTilt, 1), "padj": round(r.PortalAdj, 1),
            "rpadj": round(r.RetProdAdj, 1),
            "my": round(r.MyRating, 1), "edge": round(r.Edge, 1), "band": r.Band,
            "conf_lvl": r.Confidence,
            "myC": round(r.My_C, 1), "myB": round(r.My_B, 1), "myA": round(r.My_A, 1),
        })
    return rows


# ---------------- NFL ----------------

def nfl_model(fpi, t_table=None, qb=None, rost=None, hfa=None, unsettled=None):
    t_table = t_table or fb.NFL_T
    qb = qb or fb.NFL_QB
    rost = rost or fb.NFL_ROST
    hfa = hfa or fb.NFL_HFA
    unsettled = unsettled or fb.NFL_UNSETTLED

    rows = []
    for team, (fpi_snap, wt, to, luck, pd_, aw) in t_table.items():
        rows.append(dict(Team=team, FPI=fpi.get(team, fpi_snap), WinTotal=wt, TO=to,
                         Luck=luck, PtDiff=pd_, ActW=aw,
                         HFA=hfa.get(team, fb.NFL_HFA_DEFAULT)))
    df = pd.DataFrame(rows)

    BETA_TO = 0.06; BETA_LUCK = 0.40; CAP_REG = 2.5

    df["RegTilt"] = np.clip(-(BETA_TO * df["TO"] + BETA_LUCK * df["Luck"]), -CAP_REG, CAP_REG)
    df["QBAdj"] = df["Team"].map(lambda t: qb.get(t, (0, ""))[0])
    df["QBnote"] = df["Team"].map(lambda t: qb.get(t, (0, ""))[1])
    df["RosterAdj"] = df["Team"].map(lambda t: rost.get(t, 0.0))
    df["Tilt"] = df["RegTilt"] + df["QBAdj"] + df["RosterAdj"]
    for k, mult in REGIMES.items():
        df[f"My_{k}"] = df["FPI"] + mult * df["Tilt"]
    df["MyRating"] = df["My_B"]
    df["EdgeFPI"] = df["MyRating"] - df["FPI"]

    # market-implied rating from win totals (regress FPI on WinTotal)
    b1, b0 = np.polyfit(df["WinTotal"], df["FPI"], 1)
    r = np.corrcoef(df["WinTotal"], df["FPI"])[0, 1]
    df["MktRating"] = b0 + b1 * df["WinTotal"]
    df["EdgeMkt"] = df["MyRating"] - df["MktRating"]  # + = model higher than market

    def band(x):
        conflict = min(sum(a for a in [x.RegTilt, x.QBAdj, x.RosterAdj] if a > 0),
                       -sum(a for a in [x.RegTilt, x.QBAdj, x.RosterAdj] if a < 0))
        return round(float(np.clip(2.5 + (1.3 if x.Team in unsettled else 0)
                                   + 0.4 * conflict + 0.15 * abs(x.EdgeMkt), 2.5, 6.0)), 1)

    df["Band"] = df.apply(band, axis=1)

    df = df.sort_values("MyRating", ascending=False).reset_index(drop=True)
    df["MyRank"] = np.arange(1, len(df) + 1)
    df.attrs["win_fit_r"] = r
    return df


def nfl_rows_for_dashboard(df):
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "rank": int(r.MyRank), "team": r.Team, "fpi": round(r.FPI, 1),
            "win": r.WinTotal, "reg": round(r.RegTilt, 1), "qb": round(r.QBAdj, 1),
            "ros": round(r.RosterAdj, 1), "my": round(r.MyRating, 1),
            "mkt": round(r.MktRating, 1), "edge": round(r.EdgeMkt, 1),
            "band": r.Band, "hfa": round(r.HFA, 1), "note": r.QBnote,
            "myC": round(r.My_C, 1), "myB": round(r.My_B, 1), "myA": round(r.My_A, 1),
        })
    return rows
