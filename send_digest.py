#!/usr/bin/env python3
"""
send_digest.py  –  E-mail upcoming SpaceX launches from Vandenberg (next 3 weeks)

Environment variables expected (set in GitHub Actions → Secrets):
  SMTP_HOST    e.g. "smtp.gmail.com"
  SMTP_PORT    e.g. "465"
  SMTP_USER    login / from-address
  SMTP_PASS    password or app-password
  DEST_EMAIL   where to send the digest
"""

import datetime as _dt
import os as _os
import ssl as _ssl
import smtplib as _smtp
from email.message import EmailMessage as _Email
import requests as _rq
import zoneinfo as _zi


# ─────────────────────────  CONSTANTS  ──────────────────────────
URL_PADS      = "https://api.spacexdata.com/v4/launchpads"
URL_LAUNCHES  = "https://api.spacexdata.com/v4/launches/query"
URL_LL        = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/"
TZ_PT         = _zi.ZoneInfo("America/Los_Angeles")
TZ_UTC        = _zi.ZoneInfo("UTC")
NOW_UTC       = _dt.datetime.now(tz=TZ_UTC)
LIMIT_UTC     = NOW_UTC + _dt.timedelta(weeks=3)      # 21-day horizon


# ──────────────────────  FETCH  ────────────────────────────────
def _vandenberg_pad_ids() -> list[str]:
    """Return list of launchpad IDs whose locality starts with 'Vandenberg'."""
    pads = _rq.get(URL_PADS, timeout=10).json()
    return [p["id"] for p in pads if p.get("locality", "").startswith("Vandenberg")]


def _fetch_spacex() -> list[dict]:
    """Query the SpaceX v4 API for upcoming launches from any VSFB pad."""
    pad_ids = _vandenberg_pad_ids()
    payload = {
        "query": {"upcoming": True, "launchpad": {"$in": pad_ids}},
        "options": {
            "sort": {"date_utc": "asc"},
            "select": ["name", "date_utc", "date_precision", "links.patch.small"],
        },
    }
    docs = _rq.post(URL_LAUNCHES, json=payload, timeout=10).json()["docs"]
    # client-side ≤ 21-day cutoff
    return [
        d
        for d in docs
        if _date_from_iso(d["date_utc"]) <= LIMIT_UTC
    ]


def _fetch_launchlibrary() -> list[dict]:
    """Fallback: query Launch Library 2 for SpaceX launches at Vandenberg."""
    resp = _rq.get(
        URL_LL,
        params={
            "lsp__name": "SpaceX",
            "location__name__icontains": "Vandenberg",
            "window_start__lte": LIMIT_UTC.isoformat(),
        },
        timeout=10,
    ).json()
    return [
        {
            "name": l["name"],
            "date_utc": l["window_start"],
            "date_precision": "hour",
            "links": {},
        }
        for l in resp.get("results", [])
    ]


# ─────────────────────  HELPERS  ───────────────────────────────
def _date_from_iso(iso: str) -> _dt.datetime:
    """Parse ISO-8601 date string and attach UTC tzinfo."""
    # SpaceX API ends with 'Z'; Launch Library already has tz marker
    if iso.endswith("Z"):
        iso = iso[:-1]
    return _dt.datetime.fromisoformat(iso).replace(tzinfo=TZ_UTC)


def _format_local(dt_utc: _dt.datetime) -> str:
    """Return local (PT) formatted string."""
    return dt_utc.astimezone(TZ_PT).strftime("%a %b %d %I:%M %p")


def _format_body(docs: list[dict]) -> str:
    if not docs:
        return "No Vandenberg launches currently scheduled in the next three weeks."

    lines = []
    for d in docs:
        dt_local = _format_local(_date_from_iso(d["date_utc"]))
        lines.append(f"• {dt_local} — {d['name']}")
    return "\n".join(lines)


def _send_email(body: str) -> None:
    msg = _Email()
    msg["From"] = _os.environ["SMTP_USER"]
    msg["To"] = _os.environ["DEST_EMAIL"]
    msg["Subject"] = "Upcoming Vandenberg SpaceX launches (next 3 weeks)"
    msg.set_content(body)

    ctx = _ssl.create_default_context()
    with _smtp.SMTP_SSL(
        _os.environ["SMTP_HOST"], int(_os.environ.get("SMTP_PORT", "465")), context=ctx
    ) as s:
        s.login(_os.environ["SMTP_USER"], _os.environ["SMTP_PASS"])
        s.send_message(msg)


# ──────────────────────  MAIN  ─────────────────────────────────
def main() -> None:
    try:
        docs = _fetch_spacex()
    except Exception as e:           # network / JSON / other
        docs = []
        print(f"SpaceX API error: {e}")

    if not docs:
        try:
            docs = _fetch_launchlibrary()
        except Exception as e:
            print(f"Launch Library fallback failed: {e}")

    _send_email(_format_body(docs))


if __name__ == "__main__":
    main()
