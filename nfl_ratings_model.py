"""NFL 2026 — Regression + QB/Roster power ratings (self-contained).
Run: python3 nfl_ratings_model.py  ->  writes nfl_ratings.csv and prints the board.
"""
import numpy as np, pandas as pd

# team: [FPI, WinTotal, TO_margin, LuckWins(Act-Pyth), PtDiff, ActW(2025)]
T = {
"Los Angeles Rams":[5.6,11.5,11,-0.3,172,12], "Buffalo Bills":[4.0,10.5,1,0.8,116,12],
"Baltimore Ravens":[3.7,11.5,-3,-1.1,26,8], "Seattle Seahawks":[3.6,10.5,-3,1.0,191,14],
"San Francisco 49ers":[3.3,9.5,-6,1.9,66,12], "Green Bay Packers":[2.8,9.5,1,0.2,31,9.5],
"Los Angeles Chargers":[2.6,9.5,2,1.7,28,11], "Detroit Lions":[2.5,10.5,4,-1.0,68,9],
"Kansas City Chiefs":[2.4,10.5,-1,-3.5,34,6], "Philadelphia Eagles":[2.0,10.5,6,1.0,54,11],
"Dallas Cowboys":[1.8,9.5,-9,-0.2,-40,7.5], "Cincinnati Bengals":[1.6,10.5,-3,-0.8,-78,6],
"Houston Texans":[1.4,9.5,17,0.5,109,12], "New England Patriots":[1.4,10.5,3,1.5,170,14],
"Denver Broncos":[1.3,9.5,-3,3.0,90,14], "Chicago Bears":[1.2,9.5,22,1.9,26,11],
"Jacksonville Jaguars":[1.0,8.5,13,1.2,138,13], "Tampa Bay Buccaneers":[0.1,8.5,7,0.3,-31,8],
"Minnesota Vikings":[-0.4,8.5,-9,0.2,11,9], "Indianapolis Colts":[-0.6,7.5,-2,-1.7,54,8],
"Washington Commanders":[-0.8,7.5,-13,-1.2,-95,5], "Pittsburgh Steelers":[-0.9,8.5,12,1.2,10,10],
"New York Giants":[-1.2,7.5,-2,-3.1,-58,4], "Atlanta Falcons":[-1.5,6.5,5,0.8,-48,8],
"New Orleans Saints":[-2.7,7.5,-4,-0.3,-77,6], "Carolina Panthers":[-3.0,7.5,-2,1.5,-69,8],
"Tennessee Titans":[-3.9,6.5,-5,-0.8,-194,3], "Las Vegas Raiders":[-4.6,5.5,-7,-0.4,-191,3],
"Arizona Cardinals":[-5.2,3.5,-2,-2.4,-133,3], "Cleveland Browns":[-5.5,5.5,-7,-0.5,-100,5],
"New York Jets":[-5.5,5.5,-19,-0.9,-203,3], "Miami Dolphins":[-5.5,4.5,-4,0.5,-77,7],
}
QB = {  # (points, note). Injury-return stars kept modest (FPI already knows some).
"Kansas City Chiefs":(1.3,"Mahomes back from injury"), "Cincinnati Bengals":(1.2,"Burrow back from injury"),
"Washington Commanders":(0.6,"Daniels back from injury"), "San Francisco 49ers":(0.6,"Purdy back from injury"),
"Miami Dolphins":(-1.5,"new QB (Willis) — downgrade"), "Arizona Cardinals":(-1.2,"new QB (Brissett) — downgrade"),
"Cleveland Browns":(-0.5,"rookie/unsettled"), "New York Jets":(-0.3,"new bridge QB (Geno)"),
"Las Vegas Raiders":(-0.3,"new bridge QB (Cousins)"), "Minnesota Vikings":(0.0,"unsettled: Murray/McCarthy"),
"Atlanta Falcons":(0.0,"unsettled: Penix/Tua"),
}
UNSETTLED = {"Minnesota Vikings","Atlanta Falcons","Cleveland Browns"}
ROST = {  # offseason roster direction (points)
"Los Angeles Rams":0.8,"New England Patriots":0.6,"New York Jets":0.6,"Cincinnati Bengals":0.6,
"Tennessee Titans":0.6,"Las Vegas Raiders":0.6,"New York Giants":0.6,"Washington Commanders":0.6,
"Carolina Panthers":0.6,"New Orleans Saints":0.6,"Los Angeles Chargers":0.5,"Dallas Cowboys":0.5,
"Houston Texans":0.2,"Buffalo Bills":0.2,
"Miami Dolphins":-0.8,"Indianapolis Colts":-0.6,"Jacksonville Jaguars":-0.6,"Green Bay Packers":-0.6,
"Tampa Bay Buccaneers":-0.6,"Arizona Cardinals":-0.6,"Seattle Seahawks":-0.6,
"Baltimore Ravens":-0.2,"Kansas City Chiefs":-0.2,"Philadelphia Eagles":-0.2,"Chicago Bears":-0.2,
"Detroit Lions":-0.2,
}
HFA = {
"Seattle Seahawks":2.5,"Denver Broncos":2.4,"Kansas City Chiefs":2.3,"Green Bay Packers":2.2,
"Buffalo Bills":2.2,"New Orleans Saints":2.2,"Baltimore Ravens":2.1,"Pittsburgh Steelers":2.1,
"Minnesota Vikings":2.1,"Philadelphia Eagles":2.0,
"Jacksonville Jaguars":1.3,"Los Angeles Rams":1.2,"Los Angeles Chargers":1.2,"Las Vegas Raiders":1.4,
"Arizona Cardinals":1.5,"Carolina Panthers":1.5,
}
HFA_DEF=1.8

rows=[]
for team,(fpi,wt,to,luck,pd_,aw) in T.items():
    rows.append(dict(Team=team,FPI=fpi,WinTotal=wt,TO=to,Luck=luck,PtDiff=pd_,ActW=aw,HFA=HFA.get(team,HFA_DEF)))
df=pd.DataFrame(rows)

# weights (modest: NFL FPI + market are sharp)
BETA_TO=0.06; BETA_LUCK=0.40; CAP_REG=2.5
REGIME={"Conservative":0.7,"Base":1.0,"Aggressive":1.4}

df["RegTilt"]=np.clip(-(BETA_TO*df["TO"]+BETA_LUCK*df["Luck"]),-CAP_REG,CAP_REG)
df["QBAdj"]=df["Team"].map(lambda t: QB.get(t,(0,""))[0])
df["QBnote"]=df["Team"].map(lambda t: QB.get(t,(0,""))[1])
df["RosterAdj"]=df["Team"].map(lambda t: ROST.get(t,0.0))
df["Tilt"]=df["RegTilt"]+df["QBAdj"]+df["RosterAdj"]
df["MyRating"]=df["FPI"]+df["Tilt"]
df["EdgeFPI"]=df["MyRating"]-df["FPI"]

# market-implied rating from win totals (regress FPI on WinTotal)
b1,b0=np.polyfit(df["WinTotal"],df["FPI"],1); r=np.corrcoef(df["WinTotal"],df["FPI"])[0,1]
df["MktRating"]=b0+b1*df["WinTotal"]
df["EdgeMkt"]=df["MyRating"]-df["MktRating"]     # + = model higher than market

for name,mult in REGIME.items():
    df[f"My_{name}"]=df["FPI"]+mult*df["Tilt"]

def band(x):
    conflict=min(sum(a for a in [x.RegTilt,x.QBAdj,x.RosterAdj] if a>0),
                 -sum(a for a in [x.RegTilt,x.QBAdj,x.RosterAdj] if a<0))
    return round(float(np.clip(2.5+(1.3 if x.Team in UNSETTLED else 0)+0.4*conflict+0.15*abs(x.EdgeMkt),2.5,6.0)),1)
df["Band"]=df.apply(band,axis=1)

df=df.sort_values("MyRating",ascending=False).reset_index(drop=True)
df["MyRank"]=np.arange(1,33)
print(f"WinTotal->FPI fit r={r:.3f}")
print(df[["MyRank","Team","FPI","MyRating","RegTilt","QBAdj","RosterAdj","EdgeMkt","WinTotal","Band"]].to_string(index=False))
df.to_csv("nfl_ratings.csv",index=False)   # <-- change path if you like
print("saved nfl_ratings.csv")
