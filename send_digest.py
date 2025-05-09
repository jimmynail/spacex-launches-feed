#!/usr/bin/env python3
"""
send_digest.py – e-mails upcoming SpaceX launches from Vandenberg (≤21 days)

Secrets required (repo › Settings › Secrets › Actions):
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, DEST_EMAIL
"""
import datetime as _dt, os as _os, re as _re, smtplib as _smtp, ssl as _ssl
from email.message import EmailMessage as _Email
import requests as _rq, zoneinfo as _zi

# ───── constants & helpers ─────
URL_PADS      = "https://api.spacexdata.com/v4/launchpads"
URL_LAUNCHES  = "https://api.spacexdata.com/v4/launches/query"
URL_LL        = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/"
TZ_PT, TZ_UTC = _zi.ZoneInfo("America/Los_Angeles"), _zi.ZoneInfo("UTC")
NOW_UTC       = _dt.datetime.now(tz=TZ_UTC)
LIMIT_UTC     = NOW_UTC + _dt.timedelta(weeks=3)        # 21 days
_ROCKETS      = {}                                      # cache id→name

_slug = lambda s: _re.sub(r"-{2,}", "-", _re.sub(r"[^a-z0-9]+", "-", _re.sub(r"[’'`]", "", s.lower())).strip("-"))
_to_dt = lambda iso: _dt.datetime.fromisoformat(iso.rstrip("Z")).replace(tzinfo=TZ_UTC)

def _fmt_local(dt:_dt.datetime) -> str:
    loc = dt.astimezone(TZ_PT)
    return f"{loc.strftime('%b')} {loc.day} {loc.strftime('%A')} {loc.strftime('%-I:%M%p').lower()} Pacific"

def _pad_ids():
    return [p["id"] for p in _rq.get(URL_PADS, timeout=10).json()
            if "vandenberg" in p.get("locality","").lower()]

def _rocket_name(rid:str) -> str:
    if rid in _ROCKETS: return _ROCKETS[rid]
    name = _rq.get(f"https://api.spacexdata.com/v4/rockets/{rid}", timeout=10).json()["name"]
    _ROCKETS[rid]=name; return name

def _links(mission:str, rocket:str, slug:str|None):
    sx = f"https://www.spacex.com/launches/mission/?missionId={slug or _slug(mission)}"
    rl = f"https://rocketlaunch.org/mission-{_slug(rocket)}-{_slug(mission)}"
    return sx, rl

# ───── fetchers ─────
def _spacex():
    docs = _rq.post(URL_LAUNCHES, json={
        "query":{"upcoming":True,"launchpad":{"$in":_pad_ids()}},
        "options":{"sort":{"date_utc":"asc"},
                   "select":["name","date_utc","rocket","slug"]}},
        timeout=10).json()["docs"]
    return [d for d in docs if NOW_UTC <= _to_dt(d["date_utc"]) <= LIMIT_UTC]

def _launch_library():
    raw = _rq.get(URL_LL, params={
        "lsp__name":"SpaceX",
        "location__name__icontains":"Vandenberg",
        "status":1,              # “Go”
        "limit":100,
        "ordering":"window_start"}, timeout=10).json()["results"]

    cleaned = []
    for l in raw:
        dt = _to_dt(l["window_start"])
        if not NOW_UTC <= dt <= LIMIT_UTC:               # local trim
            continue
        name_raw = l["name"]
        if "|" in name_raw:                # "Falcon 9 Block 5 | Starlink 15-3"
            rocket_part, mission_part = [p.strip() for p in name_raw.split("|", 1)]
        else:
            rocket_part, mission_part = "Falcon 9", name_raw
        cleaned.append({
            "name": mission_part,
            "rocket_name": rocket_part,
            "date_utc": l["window_start"],
            "slug": None})
    return cleaned

# ───── body builder ─────
def _render(items):
    if not items:
        msg = "No Vandenberg launches currently scheduled in the next three weeks."
        return msg, f"<p>{msg}</p>"

    txt_lines, html_lines = [], ["<ul style='padding-left:0'>"]
    for d in items:
        dt, mission = _to_dt(d["date_utc"]), d["name"]
        rocket = d.get("rocket_name") or _rocket_name(d["rocket"])
        when = _fmt_local(dt)
        sx, rl = _links(mission, rocket, d.get("slug"))

        summary = f"{mission}, {rocket}, Vandenberg"
        txt_lines.append(f"🚀 {when}\n{summary}\nSpaceX: {sx}\nRocketlaunch: {rl}\n")
        html_lines.append(
            f"<li style='margin-bottom:12px;list-style:none'>"
            f"🚀 <strong>{when}</strong><br>{summary}<br>"
            f"<a href='{sx}'>SpaceX</a> "
            f"<a href='{rl}'>Rocketlaunch</a></li>"
        )
    html_lines.append("</ul>")                       # ← fixed tag
    return "\n".join(txt_lines), "\n".join(html_lines)

# ───── mail ─────
def _send(txt, html):
    m = _Email()
    m["From"], m["To"] = _os.environ["SMTP_USER"], _os.environ["DEST_EMAIL"]
    m["Subject"] = "Upcoming Vandenberg SpaceX launches (next 3 weeks)"
    m.set_content(txt)
    m.add_alternative(html, subtype="html")
    with _smtp.SMTP_SSL(_os.environ["SMTP_HOST"], int(_os.environ.get("SMTP_PORT","465")),
                        context=_ssl.create_default_context()) as s:
        s.login(_os.environ["SMTP_USER"], _os.environ["SMTP_PASS"])
        s.send_message(m)

# ───── main ─────
def main():
    upcoming = _spacex()
    if not upcoming: upcoming = _launch_library()
    _send(*_render(upcoming))

if __name__ == "__main__":
    main()
