"""Fallback snapshots — used only when a live source (or its cache) is unavailable.

Slow-moving inputs (talent composite, portal, returning production, QB status,
HFA, 2025 NFL luck stats, win totals) live here permanently: they change once a
year or are frozen history. Fast-moving inputs (FPI, schedules) are fetched
live and only fall back to these numbers if the network and cache both fail.

Team names use the project's canonical names; ESPN names are normalized via
ALIASES in data_sources.py.
"""

# ---------------- CFB ----------------

# (team, conference) in ESPN FPI rank order — also the conference/tier map.
CFB_TEAMS = [
    ("Ohio State","Big Ten"),("Texas","SEC"),("Notre Dame","FBS Indep."),("Oregon","Big Ten"),
    ("Georgia","SEC"),("Indiana","Big Ten"),("Miami","ACC"),("Alabama","SEC"),("LSU","SEC"),
    ("Texas Tech","Big 12"),("Texas A&M","SEC"),("Oklahoma","SEC"),("USC","Big Ten"),("Ole Miss","SEC"),
    ("Michigan","Big Ten"),("Tennessee","SEC"),("Penn State","Big Ten"),("Florida","SEC"),("Clemson","ACC"),
    ("BYU","Big 12"),("Missouri","SEC"),("Auburn","SEC"),("South Carolina","SEC"),("SMU","ACC"),
    ("Iowa","Big Ten"),("Washington","Big Ten"),("Louisville","ACC"),("Florida State","ACC"),
    ("Vanderbilt","SEC"),("Nebraska","Big Ten"),("Utah","Big 12"),("Virginia","ACC"),("Virginia Tech","ACC"),
    ("Arizona","Big 12"),("Houston","Big 12"),("Pittsburgh","ACC"),("Baylor","Big 12"),("TCU","Big 12"),
    ("Illinois","Big Ten"),("Kentucky","SEC"),("Kansas State","Big 12"),("North Carolina","ACC"),
    ("Wisconsin","Big Ten"),("Arizona State","Big 12"),("Colorado","Big 12"),("Cincinnati","Big 12"),
    ("Arkansas","SEC"),("Georgia Tech","ACC"),("Mississippi State","SEC"),("Boise State","Pac-12"),
    ("NC State","ACC"),("Duke","ACC"),("Wake Forest","ACC"),("Oklahoma State","Big 12"),("Kansas","Big 12"),
    ("Tulane","American"),("UCF","Big 12"),("UNLV","Mountain West"),("San Diego State","Pac-12"),
    ("Northwestern","Big Ten"),("Maryland","Big Ten"),("California","ACC"),("Minnesota","Big Ten"),
    ("UCLA","Big Ten"),("Michigan State","Big Ten"),("West Virginia","Big 12"),("Rutgers","Big Ten"),
    ("East Carolina","American"),("Navy","American"),("Syracuse","ACC"),("Purdue","Big Ten"),
    ("Iowa State","Big 12"),("South Florida","American"),("Memphis","American"),("James Madison","Sun Belt"),
    ("Hawai'i","Mountain West"),("Fresno State","Pac-12"),("Boston College","ACC"),("Toledo","MAC"),
    ("Stanford","ACC"),("New Mexico","Mountain West"),("Western Michigan","MAC"),("Washington State","Pac-12"),
    ("Texas State","Pac-12"),("Old Dominion","Sun Belt"),("Western Kentucky","CUSA"),("Southern Miss","Sun Belt"),
    ("UTSA","American"),("Army","American"),("North Texas","American"),("Delaware","CUSA"),
    ("Utah State","Pac-12"),("Air Force","Mountain West"),("Miami (OH)","MAC"),("Troy","Sun Belt"),
    ("Liberty","CUSA"),("Ohio","MAC"),("Oregon State","Pac-12"),("Louisiana","Sun Belt"),
    ("North Dakota State","Mountain West"),("Jacksonville State","CUSA"),("Temple","American"),
    ("Georgia Southern","Sun Belt"),("Marshall","Sun Belt"),("Tulsa","American"),("Kennesaw State","CUSA"),
    ("Arkansas State","Sun Belt"),("Louisiana Tech","Sun Belt"),("App State","Sun Belt"),
    ("Missouri State","CUSA"),("Sacramento State","MAC"),("South Alabama","Sun Belt"),("Buffalo","MAC"),
    ("UConn","FBS Indep."),("Florida Atlantic","American"),("Nevada","Mountain West"),
    ("Coastal Carolina","Sun Belt"),("Colorado State","Pac-12"),("Florida International","CUSA"),
    ("Central Michigan","MAC"),("Wyoming","Mountain West"),("Rice","American"),("Bowling Green","MAC"),
    ("San José State","Mountain West"),("Northern Illinois","Mountain West"),("Charlotte","American"),
    ("Georgia State","Sun Belt"),("UAB","American"),("New Mexico State","CUSA"),
    ("Middle Tennessee","CUSA"),("Eastern Michigan","MAC"),("UTEP","Mountain West"),("Akron","MAC"),
    ("Ball State","MAC"),("Kent State","MAC"),("Sam Houston","CUSA"),("Massachusetts","MAC"),
    ("UL Monroe","Sun Belt"),
]
CFB_CONF = {t: c for t, c in CFB_TEAMS}

# Snapshot FPI (Aug 2026) — used only if ESPN fetch + cache both fail.
_CFB_FPI_VALS = [
    28.7,26.9,25.9,25.3,24.8,23.1,21.8,20.1,20.0,20.0,20.0,17.8,17.0,16.0,15.9,15.1,13.7,13.6,13.4,13.1,
    12.2,12.0,11.7,11.1,10.6,9.9,9.5,9.3,9.0,8.8,8.5,7.9,7.4,7.2,7.1,6.6,6.5,6.4,6.3,5.4,
    5.1,4.9,4.8,4.8,4.5,4.4,4.4,4.2,4.1,4.0,3.7,3.5,3.4,3.3,2.8,2.3,2.1,1.8,1.4,1.4,
    1.0,0.9,0.6,0.5,0.3,0.2,-0.2,-0.6,-0.7,-0.8,-0.9,-0.9,-0.9,-1.9,-2.0,-2.4,-2.5,-2.7,-3.0,-3.3,
    -3.5,-4.0,-4.1,-4.3,-4.4,-4.9,-5.1,-5.3,-5.6,-6.4,-6.6,-6.7,-6.8,-7.0,-7.4,-7.7,-8.0,-8.1,-8.3,-8.3,
    -8.5,-8.6,-8.7,-8.8,-9.0,-9.0,-9.2,-9.6,-9.8,-10.3,-10.4,-10.5,-10.8,-11.2,-11.3,-11.9,-12.1,-12.4,-12.6,-12.8,
    -13.1,-13.4,-13.7,-14.3,-14.5,-14.6,-15.2,-15.5,-15.7,-16.1,-16.3,-16.6,-16.9,-17.3,-17.9,-18.4,-18.8,-19.3]
CFB_FPI_SNAPSHOT = {t[0]: v for t, v in zip(CFB_TEAMS, _CFB_FPI_VALS)}

# 247Sports 2025 Team Talent Composite (annual)
CFB_TALENT = {
 "Georgia":1002.98,"Alabama":993.55,"Ohio State":973.69,"Texas":973.54,"Oregon":941.22,"LSU":920.05,
 "Clemson":918.43,"Texas A&M":917.29,"Notre Dame":912.11,"Penn State":910.42,"Michigan":907.22,
 "Florida":898.58,"Auburn":891.84,"Oklahoma":882.59,"Miami":874.57,"Tennessee":866.57,"USC":847.53,
 "South Carolina":833.26,"Florida State":828.45,"Nebraska":821.39,"Ole Miss":813.11,"Missouri":804.77,
 "Arkansas":772.64,"Mississippi State":770.48,"SMU":766.67,"UCLA":766.62,"Kentucky":763.18,
 "Wisconsin":763.17,"Texas Tech":757.49,"Colorado":755.20,"North Carolina":753.20,"TCU":745.01,
 "Arizona State":738.52,"Syracuse":727.83,"Baylor":726.30,"California":726.21,"Washington":720.56,
 "Michigan State":717.42,"Georgia Tech":715.56,"Virginia Tech":712.19,"Minnesota":711.04,"Iowa":710.00,
 "UCF":709.99,"Utah":707.86,"NC State":707.51,"Stanford":707.27,"Kansas State":705.85,"Kansas":705.32,
 "Oklahoma State":702.74,"Louisville":700.77,"UNLV":700.54,"Maryland":699.31,"Rutgers":689.22,
 "Purdue":687.74,"Vanderbilt":685.42,"Pittsburgh":681.21,"Northwestern":677.97,"Duke":669.18,
 "Memphis":668.63,"South Florida":666.89,"Virginia":666.74,"West Virginia":662.33,"Illinois":662.13,
 "UTSA":662.09,"Houston":656.74,"Arizona":651.62,"Tulane":650.87,"Boston College":650.12,"BYU":649.36,
 "Iowa State":648.93,"Indiana":645.34,"Cincinnati":644.10,"Wake Forest":627.16,
 "Toledo":620.13,"Texas State":612.16,"Georgia State":611.97,"East Carolina":611.55,"App State":611.31,
 "Coastal Carolina":610.88,"Boise State":610.65,"Florida Atlantic":605.22,"Southern Miss":602.53,
 "Tulsa":594.78,"San Diego State":594.27,"Miami (OH)":592.10,"Marshall":590.64,"Fresno State":586.84,
 "Louisiana":585.82,"Colorado State":583.71,"Eastern Michigan":572.90,"North Texas":569.15,
}

# On3 2026 net portal movement (offseason)
CFB_PORTAL = {
 "Indiana":57,"Texas Tech":51,"Texas A&M":46,"Louisville":42,"LSU":41,"Virginia Tech":35,"Houston":33,
 "Arkansas":32,"Notre Dame":31,"Texas":30,"UCLA":28,"Florida":26,"Mississippi State":25,"Ole Miss":24,
 "Wisconsin":22,"Miami":21,"BYU":21,"Vanderbilt":19,"Arizona":18,"West Virginia":17,"California":16,
 "Virginia":16,"Oklahoma":14,"Purdue":14,"SMU":13,"South Carolina":13,"Tennessee":13,"Oklahoma State":12,
 "Colorado":12,"Illinois":11,"Oregon":10,"Clemson":9,"Nebraska":7,"Arizona State":5,"Florida State":4,
 "Stanford":4,"Maryland":4,"TCU":1,"Ohio State":0,"Alabama":0,"Kentucky":0,"Northwestern":-1,"Syracuse":-1,
 "UCF":-2,"USC":-4,"Georgia":-4,"Iowa":-8,"Missouri":-8,"Wake Forest":-8,"Rutgers":-10,
}

# Returning production (overall snap %) + QB status: Y=returning, T=transfer, N=new
CFB_RETPROD = {
 "Virginia Tech":(62,"T"),"Stanford":(59,"T"),"Pittsburgh":(51,"Y"),"Miami":(46,"Y"),"SMU":(46,"Y"),
 "Clemson":(49,"N"),"Syracuse":(43,"N"),"NC State":(42,"Y"),"California":(41,"Y"),"Duke":(41,"N"),
 "Virginia":(39,"T"),"Boston College":(39,"T"),"Georgia Tech":(38,"T"),"Wake Forest":(41,"T"),
 "North Carolina":(29,"T"),"Louisville":(30,"T"),"Florida State":(32,"T"),
 "Maryland":(65,"Y"),"Ohio State":(56,"Y"),"Nebraska":(56,"T"),"Minnesota":(54,"Y"),"Oregon":(54,"Y"),
 "Washington":(52,"Y"),"Michigan":(51,"Y"),"Northwestern":(50,"T"),"USC":(56,"Y"),"UCLA":(40,"Y"),
 "Iowa":(39,"T"),"Rutgers":(36,"N"),"Purdue":(37,"Y"),"Illinois":(35,"T"),"Indiana":(41,"T"),
 "Wisconsin":(33,"T"),"Michigan State":(27,"Y"),"Penn State":(22,"T"),
 "Georgia":(61,"Y"),"Oklahoma":(55,"Y"),"Texas":(54,"Y"),"Tennessee":(53,"N"),"South Carolina":(49,"Y"),
 "Texas A&M":(46,"Y"),"Vanderbilt":(46,"N"),"Ole Miss":(44,"Y"),"Alabama":(41,"N"),"Missouri":(41,"T"),
 "Florida":(51,"N"),"Mississippi State":(37,"N"),"Kentucky":(29,"T"),"LSU":(34,"T"),"Arkansas":(30,"T"),
 "Auburn":(27,"T"),
 "BYU":(63,"Y"),"Oklahoma State":(10,"T"),"Texas Tech":(52,"T"),"Houston":(50,"Y"),"Arizona":(44,"Y"),
 "TCU":(45,"T"),"Kansas":(42,"N"),"UCF":(42,"T"),"Kansas State":(40,"Y"),"Utah":(39,"Y"),
 "Cincinnati":(33,"T"),"Baylor":(30,"T"),"Arizona State":(31,"T"),"Colorado":(21,"N"),
 "West Virginia":(19,"T"),"Iowa State":(10,"T"),
 "Notre Dame":(66,"Y"),"UConn":(7,"T"),
 "New Mexico":(58,None),"Air Force":(57,None),"Army":(54,None),"Navy":(53,None),"Boise State":(52,"Y"),
 "Florida Atlantic":(52,None),"Fresno State":(51,None),"Tulane":(35,None),"Memphis":(10,None),
 "James Madison":(15,None),"Toledo":(17,None),"North Texas":(8,None),"Southern Miss":(10,None),
}

# Venue-specific home-field advantage (anchor 2.5)
CFB_HFA = {
 "Wyoming":3.3,"Utah":3.0,"Colorado":2.9,"Boise State":2.9,"BYU":2.8,"Air Force":2.6,
 "Colorado State":2.2,"New Mexico":2.0,"Hawai'i":3.4,"SMU":3.2,"UTSA":3.2,"Cincinnati":3.0,
 "East Carolina":3.0,"Middle Tennessee":3.0,"Rice":2.8,"LSU":2.9,"Ohio State":2.9,"Penn State":2.9,
 "Oregon":2.9,"Tennessee":2.8,"Texas A&M":2.8,"Texas":2.8,"Georgia":2.8,"Washington":2.8,"Iowa":2.8,
 "Georgia Tech":1.8,"Arkansas":1.8,"Bowling Green":1.8,"Louisiana":1.8,"Illinois":2.0,
 "Western Kentucky":2.0,"Duke":2.0,"Purdue":2.0,"Stanford":2.0,"Tulsa":2.0,"Northwestern":2.0,
 "Vanderbilt":2.0,
}
CFB_HFA_DEFAULT = 2.5

# ---------------- NFL ----------------

# team: [FPI_snapshot, WinTotal, TO_margin, LuckWins(Act-Pyth), PtDiff, ActW(2025)]
# WinTotal = 2026 market win total (manual, editable). TO/Luck/PtDiff/ActW are
# frozen 2025 facts. FPI_snapshot used only if ESPN fetch + cache fail.
NFL_T = {
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

NFL_QB = {  # (points, note). Injury-return stars kept modest (FPI already knows some).
"Kansas City Chiefs":(1.3,"Mahomes back from injury"), "Cincinnati Bengals":(1.2,"Burrow back from injury"),
"Washington Commanders":(0.6,"Daniels back from injury"), "San Francisco 49ers":(0.6,"Purdy back from injury"),
"Miami Dolphins":(-1.5,"new QB (Willis) — downgrade"), "Arizona Cardinals":(-1.2,"new QB (Brissett) — downgrade"),
"Cleveland Browns":(-0.5,"rookie/unsettled"), "New York Jets":(-0.3,"new bridge QB (Geno)"),
"Las Vegas Raiders":(-0.3,"new bridge QB (Cousins)"), "Minnesota Vikings":(0.0,"unsettled: Murray/McCarthy"),
"Atlanta Falcons":(0.0,"unsettled: Penix/Tua"),
}
NFL_UNSETTLED = {"Minnesota Vikings","Atlanta Falcons","Cleveland Browns"}

NFL_ROST = {  # offseason roster direction (points)
"Los Angeles Rams":0.8,"New England Patriots":0.6,"New York Jets":0.6,"Cincinnati Bengals":0.6,
"Tennessee Titans":0.6,"Las Vegas Raiders":0.6,"New York Giants":0.6,"Washington Commanders":0.6,
"Carolina Panthers":0.6,"New Orleans Saints":0.6,"Los Angeles Chargers":0.5,"Dallas Cowboys":0.5,
"Houston Texans":0.2,"Buffalo Bills":0.2,
"Miami Dolphins":-0.8,"Indianapolis Colts":-0.6,"Jacksonville Jaguars":-0.6,"Green Bay Packers":-0.6,
"Tampa Bay Buccaneers":-0.6,"Arizona Cardinals":-0.6,"Seattle Seahawks":-0.6,
"Baltimore Ravens":-0.2,"Kansas City Chiefs":-0.2,"Philadelphia Eagles":-0.2,"Chicago Bears":-0.2,
"Detroit Lions":-0.2,
}

NFL_HFA = {
"Seattle Seahawks":2.5,"Denver Broncos":2.4,"Kansas City Chiefs":2.3,"Green Bay Packers":2.2,
"Buffalo Bills":2.2,"New Orleans Saints":2.2,"Baltimore Ravens":2.1,"Pittsburgh Steelers":2.1,
"Minnesota Vikings":2.1,"Philadelphia Eagles":2.0,
"Jacksonville Jaguars":1.3,"Los Angeles Rams":1.2,"Los Angeles Chargers":1.2,"Las Vegas Raiders":1.4,
"Arizona Cardinals":1.5,"Carolina Panthers":1.5,
}
NFL_HFA_DEFAULT = 1.8
