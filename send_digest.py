#!/usr/bin/env python3
"""
send_digest.py – e-mails upcoming SpaceX launches from Vandenberg (next 21 days)
Requires SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, DEST_EMAIL secrets.
"""

import datetime as _dt, os as _os, re as _re, smtplib as _smtp, ssl as _ssl
from email.message import EmailMessage as _Email
import requests as _rq, zoneinfo as _zi

# ───────────── CONSTANTS ─────────────
URL_PADS     = "https://api.spacexdata.com/v4/launchpads"
URL_LAUNCHES = "https://api.spacexdata.com/v4/launches/query"
URL_LL       = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/"
TZ_PT, TZ_UTC = _zi.ZoneInfo("America/Los_Angeles"), _zi.ZoneInfo("UTC")
NOW_UTC      = _dt.datetime.now(tz=TZ_UTC)
LIMIT_UTC    = NOW_UTC + _dt.timedelta(weeks=3)        # 21 days
ROCKETS_CACHE = {}                                     # id → name

# ───────────── helpers ─────────────
def _slug(t:str)->str: return _re.sub(r"-{2,}","-",_re.sub(r"[^a-z0-9]+","-",_re.sub(r"[’'`]","",t.lower())).strip("-"))
def _iso_to_dt(s:str)->_dt.datetime: return _dt.datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=TZ_UTC)
def _pad_ids()->list[str]: return [p["id"] for p in _rq.get(URL_PADS,timeout=10).json() if "vandenberg" in p.get("locality","").lower()]
def _fmt_local(dt:_dt.datetime)->str:
    loc=dt.astimezone(TZ_PT); return f"{loc.strftime('%b')} {loc.day} {loc.strftime('%A')} {loc.strftime('%-I:%M%p').lower()} Pacific"
def _rocket(rid:str)->str:
    if rid in ROCKETS_CACHE: return ROCKETS_CACHE[rid]
    name=_rq.get(f"https://api.spacexdata.com/v4/rockets/{rid}",timeout=10).json()["name"]
    ROCKETS_CACHE[rid]=name; return name
def _links(name,rocket,slug): 
    sx=f"https://www.spacex.com/launches/mission/?missionId={slug or _slug(name)}"
    rl=f"https://rocketlaunch.org/mission-{_slug(rocket)}-{_slug(name)}"
    return sx,rl

# ─── fetchers ───
def _spacex_upcoming()->list[dict]:
    resp=_rq.post(URL_LAUNCHES,json={
        "query":{"upcoming":True,"launchpad":{"$in":_pad_ids()}},
        "options":{"sort":{"date_utc":"asc"},"select":["name","date_utc","rocket","slug"]}},
        timeout=10).json()["docs"]
    return [d for d in resp if NOW_UTC<=_iso_to_dt(d["date_utc"])<=LIMIT_UTC]

def _ll_upcoming()->list[dict]:
    # ask Launch Library for *all future* VAFB SpaceX launches, then trim locally
    resp=_rq.get(URL_LL,params={
        "lsp__name":"SpaceX",
        "location__name__icontains":"Vandenberg",
        "status":1,                     # “Go”
        "limit":100,                    # plenty of head-room
        "ordering":"window_start"},timeout=10).json()["results"]
    out=[]
    for l in resp:
        dt=_iso_to_dt(l["window_start"])
        if NOW_UTC<=dt<=LIMIT_UTC:
            out.append({"name":l["name"],"date_utc":l["window_start"],
                        "rocket":"LL2","slug":None})
    return out

# ─── body builders ───
def _bodies(docs):
    if not docs: return ("No Vandenberg launches within 3 weeks.","<p>No launches…</p>")
    plain,html=[],["<ul style='padding-left:0'>"]
    for d in docs:
        dt=_iso_to_dt(d["date_utc"]); when=_fmt_local(dt)
        rocket=_rocket(d["rocket"]) if d["rocket"]!="LL2" else "Falcon 9"
        sx,rl=_links(d["name"],rocket,d.get("slug"))
        line=f"{d['name']}, {rocket}, Vandenberg"
        plain.append(f"🚀 {when}\n{line}\nSpaceX: {sx}\nRocketlaunch: {rl}\n")
        html.append(f"<li style='margin-bottom:12px;list-style:none'>🚀 <strong>{when}</strong><br>{line}<br><a href='{sx}'>SpaceX</a> <a href='{rl}'>Rocketlaunch</a></li>")
    html.append("</ul"); return "\n".join(plain),"\n".join(html)

# ─── mail ───
def _send(txt,html):
    m=_Email(); m["From"]=_os.environ["SMTP_USER"]; m["To"]=_os.environ["DEST_EMAIL"]
    m["Subject"]="Upcoming Vandenberg SpaceX launches (next 3 weeks)"; m.set_content(txt); m.add_alternative(html,subtype="html")
    with _smtp.SMTP_SSL(_os.environ["SMTP_HOST"],int(_os.environ.get("SMTP_PORT","465")),context=_ssl.create_default_context()) as s:
        s.login(_os.environ["SMTP_USER"],_os.environ["SMTP_PASS"]); s.send_message(m)

# ─── main ───
def main():
    sx=_spacex_upcoming()
    ll=_ll_upcoming() if not sx else []
    launches=sx or ll
    if not launches:
        print(f"[diagnostic] SpaceX items: {len(sx)}  LL items: {len(ll)}")
    _send(*_bodies(launches))

if __name__=="__main__":
    main()
