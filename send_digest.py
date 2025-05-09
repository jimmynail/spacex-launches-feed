#!/usr/bin/env python3
"""
send_digest.py – Emails upcoming SpaceX launches from Vandenberg within a specified time window.

Secrets required (repo > Settings > Secrets > Actions):
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, DEST_EMAIL
"""
import datetime as _dt
import os as _os
import re as _re
import smtplib as _smtp
import ssl as _ssl
from email.message import EmailMessage as _Email
import requests as _rq
import zoneinfo as _zi
import logging as _logging

# ───── Logging Setup ─────
_logging.basicConfig(level=_logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = _logging.getLogger(__name__)

# ───── Constants & Configuration ─────
URL_PADS = "https://api.spacexdata.com/v4/launchpads"
URL_LAUNCHES = "https://api.spacexdata.com/v4/launches/query"
URL_ROCKETS = "https://api.spacexdata.com/v4/rockets/"
URL_LL = "https://ll.thespacedevs.com/2.2.0/launch/upcoming/"
TZ_PT = _zi.ZoneInfo("America/Los_Angeles")
TZ_UTC = _zi.ZoneInfo("UTC")
NOW_UTC = _dt.datetime.now(tz=TZ_UTC)
WEEKS_AHEAD = 3  # Configurable time window for upcoming launches
LIMIT_UTC = NOW_UTC + _dt.timedelta(weeks=WEEKS_AHEAD)
_ROCKETS = {}  # Cache rocket ID to name

# ───── Helper Functions ─────
def _slug(s: str) -> str:
    """Generate a URL-safe slug from a string."""
    s = _re.sub(r"[’'`]", "", s.lower())
    s = _re.sub(r"[^a-z0-9]+", "-", s)
    return _re.sub(r"-{2,}", "-", s.strip("-"))

def _to_dt(iso: str) -> _dt.datetime:
    """Convert ISO date string to UTC datetime."""
    return _dt.datetime.fromisoformat(iso.rstrip("Z")).replace(tzinfo=TZ_UTC)

def _fmt_local(dt: _dt.datetime) -> str:
    """Format datetime in Pacific Time."""
    loc = dt.astimezone(TZ_PT)
    return f"{loc.strftime('%b')} {loc.day} {loc.strftime('%A')} {loc.strftime('%-I:%M%p').lower()} Pacific"

def _pad_ids() -> list:
    """Get IDs of Vandenberg launchpads."""
    try:
        pads = _rq.get(URL_PADS, timeout=10).json()
        logger.info(f"Fetched {len(pads)} launchpads")
        return [p["id"] for p in pads if "vandenberg" in p.get("locality", "").lower()]
    except Exception as e:
        logger.error(f"Failed to fetch launchpads: {str(e)}")
        return []

def _rocket_name(rid: str) -> str:
    """Get rocket name by ID, caching results."""
    if rid in _ROCKETS:
        return _ROCKETS[rid]
    try:
        name = _rq.get(f"{URL_ROCKETS}{rid}", timeout=10).json()["name"]
        _ROCKETS[rid] = name
        logger.info(f"Cached rocket ID {rid}: {name}")
        return name
    except Exception as e:
        logger.error(f"Failed to fetch rocket {rid}: {str(e)}")
        return "Unknown Rocket"

def _rocket_slug(rocket: str) -> str:
    """Generate slug for rocket name."""
    rocket_map = {
        "Falcon 9": "falcon-9",
        "Falcon Heavy": "falcon-heavy",
        "Starship": "starship"
    }
    slug = rocket_map.get(rocket, _slug(rocket))
    logger.info(f"Generated rocket slug for '{rocket}': {slug}")
    return slug

def _validate_url(url: str) -> bool:
    """Check if a URL returns a 200 status code."""
    try:
        response = _rq.head(url, timeout=5, allow_redirects=True)
        is_valid = response.status_code == 200
        logger.info(f"Validated URL {url}: {'Valid' if is_valid else 'Invalid'}")
        return is_valid
    except _rq.RequestException as e:
        logger.error(f"URL validation failed for {url}: {str(e)}")
        return False

def _links(mission: str, rocket: str, slug: str | None) -> tuple[str, str]:
    """Generate SpaceX and RocketLaunch.org URLs."""
    # SpaceX URL: Handle Starlink missions separately
    if "starlink" in mission.lower():
        match = _re.search(r"(\d+-\d+)", mission)
        sx_slug = f"sl-{match.group(1)}" if match else (slug or _slug(mission))
    else:
        sx_slug = slug if slug else _slug(mission)
    sx = f"https://www.spacex.com/launches/mission/?missionId={sx_slug}"
    logger.info(f"Generated SpaceX URL for '{mission}': {sx}")

    # RocketLaunch.org URL
    rl_rocket_slug = _rocket_slug(rocket)
    rl_mission_slug = _slug(mission)
    rl = f"https://rocketlaunch.org/mission-{rl_rocket_slug}-{rl_mission_slug}"
    if not _validate_url(rl):
        rl = "https://rocketlaunch.org/launch-schedule/spacex"
        logger.info(f"Fell back to RocketLaunch.org schedule URL: {rl}")

    return sx, rl

# ───── Data Fetchers ─────
def _spacex() -> list:
    """Fetch upcoming Vandenberg SpaceX launches from SpaceX API."""
    try:
        docs = _rq.post(URL_LAUNCHES, json={
            "query": {"upcoming": True, "launchpad": {"$in": _pad_ids()}},
            "options": {"sort": {"date_utc": "asc"},
                        "select": ["name", "date_utc", "rocket", "slug"]}
        }, timeout=10).json()["docs"]
        upcoming = [d for d in docs if NOW_UTC <= _to_dt(d["date_utc"]) <= LIMIT_UTC]
        logger.info(f"Fetched {len(upcoming)} upcoming SpaceX launches")
        return upcoming
    except Exception as e:
        logger.error(f"SpaceX API fetch failed: {str(e)}")
        return []

def _launch_library() -> list:
    """Fetch upcoming Vandenberg SpaceX launches from TheSpaceDevs API."""
    try:
        raw = _rq.get(URL_LL, params={
            "lsp__name": "SpaceX",
            "location__name__icontains": "Vandenberg",
            "status": 1,  # Confirmed launches
            "limit": 100,
            "ordering": "window_start"
        }, timeout=10).json()["results"]
        cleaned = []
        for l in raw:
            dt = _to_dt(l["window_start"])
            if not NOW_UTC <= dt <= LIMIT_UTC:
                continue
            name_raw = l["name"]
            rocket_part, mission_part = name_raw.split("|", 1) if "|" in name_raw else ("Falcon 9", name_raw)
            rocket_part, mission_part = rocket_part.strip(), mission_part.strip()
            cleaned.append({
                "name": mission_part,
                "rocket_name": rocket_part,
                "date_utc": l["window_start"],
                "slug": None
            })
        logger.info(f"Fetched {len(cleaned)} upcoming TheSpaceDevs launches")
        return cleaned
    except Exception as e:
        logger.error(f"TheSpaceDevs API fetch failed: {str(e)}")
        return []

# ───── Email Rendering ─────
def _render(items: list) -> tuple[str, str]:
    """Render text and HTML email bodies."""
    if not items:
        msg = "No Vandenberg launches currently scheduled in the next three weeks."
        logger.info("No launches found, using fallback message")
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
    html_lines.append("</ul>")
    logger.info(f"Rendered email content for {len(items)} launches")
    return "\n".join(txt_lines), "\n".join(html_lines)

# ───── Email Sending ─────
def _send(txt: str, html: str) -> None:
    """Send email with launch details."""
    m = _Email()
    m["From"] = _os.environ["SMTP_USER"]
    m["To"] = _os.environ["DEST_EMAIL"]
    m["Subject"] = f"Upcoming Vandenberg SpaceX launches (next {WEEKS_AHEAD} weeks)"
    m.set_content(txt)
    m.add_alternative(html, subtype="html")

    smtp_host = _os.environ["SMTP_HOST"]
    smtp_port = int(_os.environ.get("SMTP_PORT", "465"))
    smtp_user = _os.environ["SMTP_USER"]
    logger.info(f"Sending email via SMTP: {smtp_host}:{smtp_port}, To: {m['To']}")

    try:
        with _smtp.SMTP_SSL(smtp_host, smtp_port, context=_ssl.create_default_context()) as s:
            s.login(smtp_user, _os.environ["SMTP_PASS"])
            s.send_message(m)
        logger.info("Email sent successfully")
    except _smtp.SMTPException as e:
        logger.error(f"SMTP error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error sending email: {str(e)}")
        raise

# ───── Main ─────
def main():
    """Fetch launches and send email."""
    upcoming = _spacex()
    if not upcoming:
        logger.info("No SpaceX launches, trying TheSpaceDevs")
        upcoming = _launch_library()
    txt, html = _render(upcoming)
    _send(txt, html)

if __name__ == "__main__":
    main()
