#!/usr/bin/env python3
"""
send_digest.py – e-mails upcoming SpaceX launches from Vandenberg (≤21 days)

Required secrets (Settings ▸ Secrets ▸ Actions):
  SMTP_HOST   e.g. "smtp.gmail.com"
  SMTP_PORT   e.g. "465"
  SMTP_USER   login / from address
  SMTP_PASS   password or app-password
  DEST_EMAIL  recipient

The message is multi-part: a plaintext fallback and an HTML part
(with the hyperlinks).
"""

import datetime as _dt
import os as _os
import re as _re
import smtplib as _smtp
import ssl as _ssl
from email.message import EmailMessage as _Email

import requests as _rq
import zoneinfo as _zi

# ────────────────────────── CONSTANTS ──────────────────────────
URL_PADS      = "https://api.spacexdata.com/v4/launchpads"
URL_LAUNCHES  = "https://api.spacexdata.com/v4/launches/query"
TZ_PT         = _zi.ZoneInfo("America/Los_Angeles")
TZ_UTC        = _zi.ZoneInfo("UTC")
NOW_UTC       = _dt.datetime.now(tz=TZ_UTC)
LIMIT_UTC     = NOW_UTC + _dt.timedelta(weeks=3)          # 21-day horizon
ROCKETS_CACHE = {}                                        # id ➜ full name

# ────────────────────────── HELPERS ────────────────────────────
def _slugify(text: str) -> str:
    """simple url-slug: lower-case, alnum → keep, other → hyphen"""
    text = _re.sub(r"[’'`]", "", text.lower())            # drop apostrophes
    text = _re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return _re.sub(r"-{2,}", "-", text)

def _fmt_local(dt_utc: _dt.datetime) -> str:
    loc = dt_utc.astimezone(TZ_PT)
    # Example: "May 9 Friday 5:00pm Pacific"
    return f"{loc.strftime('%b')} {loc.day} {loc.strftime('%A')} " \
           f"{loc.strftime('%-I:%M%p').lower()} Pacific"

def _date_from_iso(iso: str) -> _dt.datetime:
    if iso.endswith("Z"):
        iso = iso[:-1]
    return _dt.datetime.fromisoformat(iso).replace(tzinfo=TZ_UTC)

def _get_rocket_name(rid: str) -> str:
    if rid in ROCKETS_CACHE:
        return ROCKETS_CACHE[rid]
    r = _rq.get(f"https://api.spacexdata.com/v4/rockets/{rid}", timeout=10).json()
    ROCKETS_CACHE[rid] = r["name"]
    return r["name"]

def _vafb_pad_ids() -> list[str]:
    pads = _rq.get(URL_PADS, timeout=10).json()
    return [p["id"] for p in pads if p.get("locality", "").startswith("Vandenberg")]

# ───────────────────────── DATA GATHERING ──────────────────────
def _fetch_spacex() -> list[dict]:
    payload = {
        "query": {"upcoming": True, "launchpad": {"$in": _vafb_pad_ids()}},
        "options": {
            "sort": {"date_utc": "asc"},
            "select": ["name", "date_utc", "rocket", "slug"],
        },
    }
    docs = _rq.post(URL_LAUNCHES, json=payload, timeout=10).json()["docs"]
    return [
        d for d in docs
        if _date_from_iso(d["date_utc"]) <= LIMIT_UTC
    ]

# ────────────────────────── BODY BUILDERS ──────────────────────
def _make_links(name: str, rocket_name: str, slug: str | None) -> tuple[str, str]:
    # SpaceX URL
    spacex_url = f"https://www.spacex.com/launches/mission/?missionId={slug or _slugify(name)}"
    # Rocketlaunch URL: mission-<rocket-slug>-<mission-slug>
    rl_slug = f"mission-{_slugify(rocket_name)}-{_slugify(name)}"
    rocketlaunch_url = f"https://rocketlaunch.org/{rl_slug}"
    return spacex_url, rocketlaunch_url

def _build_items(docs: list[dict]) -> tuple[str, str]:
    """return (plain_text, html) body"""
    if not docs:
        msg = "No Vandenberg launches currently scheduled in the next three weeks."
        return (msg, f"<p>{msg}</p>")

    plain_lines, html_lines = [], ["<ul style='padding-left:0'>"]
    for d in docs:
        dt_utc = _date_from_iso(d["date_utc"])
        when_pt = _fmt_local(dt_utc)
        rocket_name = _get_rocket_name(d["rocket"])
        spacex_url, rl_url = _make_links(d["name"], rocket_name, d.get("slug"))

        # single-line summary
        summary = f"{d['name']}, {rocket_name}, Vandenberg"

        # plain text
        plain_lines.append(f"🚀 {when_pt}\n{summary}\nSpaceX: {spacex_url}\n"
                           f"Rocketlaunch: {rl_url}\n")

        # html
        html_lines.append(
            "<li style='margin-bottom:12px;list-style:none'>"
            f"🚀 <strong>{when_pt}</strong><br>"
            f"{summary}<br>"
            f"<a href='{spacex_url}'>SpaceX</a> "
            f"<a href='{rl_url}'>Rocketlaunch</a>"
            "</li>"
        )

    html_lines.append("</ul>")
    return ("\n".join(plain_lines), "\n".join(html_lines))

# ────────────────────────── EMAIL SEND ─────────────────────────
def _send_email(plain: str, html: str) -> None:
    msg = _Email()
    msg["From"] = _os.environ["SMTP_USER"]
    msg["To"] = _os.environ["DEST_EMAIL"]
    msg["Subject"] = "Upcoming Vandenberg SpaceX launches (next 3 weeks)"
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    ctx = _ssl.create_default_context()
    with _smtp.SMTP_SSL(_os.environ["SMTP_HOST"],
                        int(_os.environ.get("SMTP_PORT", "465")),
                        context=ctx) as s:
        s.login(_os.environ["SMTP_USER"], _os.environ["SMTP_PASS"])
        s.send_message(msg)

# ──────────────────────────── MAIN ─────────────────────────────
def main() -> None:
    try:
        launches = _fetch_spacex()
    except Exception as e:                  # network / JSON errors
        print(f"SpaceX API error: {e}")
        launches = []

    plain, html = _build_items(launches)
    _send_email(plain, html)


if __name__ == "__main__":
    main()
