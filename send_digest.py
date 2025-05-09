#!/usr/bin/env python3
"""
send_digest.py – e-mails upcoming SpaceX launches from Vandenberg (next 21 days)

Secrets required in GitHub → Settings → Secrets → Actions
  SMTP_HOST • SMTP_PORT • SMTP_USER • SMTP_PASS • DEST_EMAIL
"""

import datetime as _dt
import os as _os
import re as _re
import smtplib as _smtp
import ssl as _ssl
from email.message import EmailMessage as _Email

import requests as _rq
import zoneinfo as _zi

# ─────────────────────────  CONSTANTS  ─────────────────────────
URL_PADS      = "https://api.spacexdata.com/v4/launchpads"
URL_LAUNCHES  = "https://api.spacexdata.com/v4/launches/query"
URL_LL        = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/"
TZ_PT         = _zi.ZoneInfo("America/Los_Angeles")
TZ_UTC        = _zi.ZoneInfo("UTC")
NOW_UTC       = _dt.datetime.now(tz=TZ_UTC)
LIMIT_UTC     = NOW_UTC + _dt.timedelta(weeks=3)          # 21 days
ROCKETS_CACHE = {}                                        # id ➜ name

# ─────────────────────────  HELPERS  ──────────────────────────
def _slug(text: str) -> str:
    t = _re.sub(r"[’'`]", "", text.lower())
    t = _re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return _re.sub(r"-{2,}", "-", t)

def _dt_from_iso(iso: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(iso.rstrip("Z")).replace(tzinfo=TZ_UTC)

def _fmt_local(dt_utc: _dt.datetime) -> str:
    loc = dt_utc.astimezone(TZ_PT)
    return f"{loc.strftime('%b')} {loc.day} {loc.strftime('%A')} " \
           f"{loc.strftime('%-I:%M%p').lower()} Pacific"

def _rocket_name(rid: str) -> str:
    if rid in ROCKETS_CACHE:
        return ROCKETS_CACHE[rid]
    r = _rq.get(f"https://api.spacexdata.com/v4/rockets/{rid}", timeout=10).json()
    ROCKETS_CACHE[rid] = r["name"]
    return r["name"]

def _pad_ids_vafb() -> list[str]:
    pads = _rq.get(URL_PADS, timeout=10).json()
    return [p["id"] for p in pads if "vandenberg" in p.get("locality", "").lower()]

# ───────────────────────  FETCHERS  ───────────────────────────
def _fetch_spacex() -> list[dict]:
    payload = {
        "query": {
            "upcoming": True,
            "launchpad": {"$in": _pad_ids_vafb()}
        },
        "options": {
            "sort": {"date_utc": "asc"},
            "select": ["name", "date_utc", "rocket", "slug"]
        }
    }
    docs = _rq.post(URL_LAUNCHES, json=payload, timeout=10).json()["docs"]
    return [d for d in docs
            if NOW_UTC <= _dt_from_iso(d["date_utc"]) <= LIMIT_UTC]

def _fetch_launchlibrary() -> list[dict]:
    resp = _rq.get(
        URL_LL,
        params={
            "lsp__name": "SpaceX",
            "location__name__icontains": "Vandenberg",
            "status": 1,                                      # “Go” only
            "window_start__gte": NOW_UTC.isoformat(),
            "window_start__lte": LIMIT_UTC.isoformat(),
            "ordering": "window_start"
        }, timeout=10
    ).json()
    return [{
        "name": l["name"],
        "date_utc": l["window_start"],
        "rocket": "LL2",          # placeholder, not used
        "slug": None
    } for l in resp.get("results", [])]

# ─────────────────────  BODY BUILDERS  ─────────────────────────
def _links(name: str, rocket: str, slug: str | None):
    sx_url = f"https://www.spacex.com/launches/mission/?missionId={slug or _slug(name)}"
    rl_url = f"https://rocketlaunch.org/mission-{_slug(rocket)}-{_slug(name)}"
    return sx_url, rl_url

def _build_bodies(docs: list[dict]) -> tuple[str, str]:
    if not docs:
        msg = "No Vandenberg launches currently scheduled in the next three weeks."
        return msg, f"<p>{msg}</p>"

    plain, html = [], ["<ul style='padding-left:0'>"]
    for d in docs:
        t = _dt_from_iso(d["date_utc"])
        when = _fmt_local(t)
        rocket = _rocket_name(d["rocket"]) if d["rocket"] != "LL2" else "Falcon 9"
        sx, rl = _links(d["name"], rocket, d.get("slug"))

        summary = f"{d['name']}, {rocket}, Vandenberg"

        plain.append(
            f"🚀 {when}\n{summary}\nSpaceX: {sx}\nRocketlaunch: {rl}\n"
        )
        html.append(
            "<li style='margin-bottom:12px;list-style:none'>"
            f"🚀 <strong>{when}</strong><br>{summary}<br>"
            f"<a href='{sx}'>SpaceX</a> "
            f"<a href='{rl}'>Rocketlaunch</a></li>"
        )
    html.append("</ul>")
    return "\n".join(plain), "\n".join(html)

# ─────────────────────  EMAIL SEND  ───────────────────────────
def _send_email(text: str, html: str) -> None:
    msg = _Email()
    msg["From"] = _os.environ["SMTP_USER"]
    msg["To"]   = _os.environ["DEST_EMAIL"]
    msg["Subject"] = "Upcoming Vandenberg SpaceX launches (next 3 weeks)"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    ctx = _ssl.create_default_context()
    with _smtp.SMTP_SSL(_os.environ["SMTP_HOST"],
                        int(_os.environ.get("SMTP_PORT", "465")), context=ctx) as s:
        s.login(_os.environ["SMTP_USER"], _os.environ["SMTP_PASS"])
        s.send_message(msg)

# ─────────────────────────  MAIN  ─────────────────────────────
def main() -> None:
    try:
        launches = _fetch_spacex()
    except Exception as e:
        print(f"SpaceX API error: {e}")
        launches = []

    if not launches:          # fallback only if SpaceX list empty
        try:
            launches = _fetch_launchlibrary()
        except Exception as e:
            print(f"Launch Library error: {e}")

    text, html = _build_bodies(launches)
    _send_email(text, html)

if __name__ == "__main__":
    main()
