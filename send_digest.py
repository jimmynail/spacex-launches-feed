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

def _slug(s: str) -> str:
    """Generate a URL-safe slug from a string."""
    s = _re.sub(r"[’'`]", "", s.lower())  # Remove quotes/apostrophes
    s = _re.sub(r"[^a-z0-9]+", "-", s)    # Replace non-alphanumeric with hyphen
    return _re.sub(r"-{2,}", "-", s.strip("-"))  # Remove multiple hyphens, trim

def _to_dt(iso: str) -> _dt.datetime:
    """Convert ISO date string to UTC datetime."""
    return _dt.datetime.fromisoformat(iso.rstrip("Z")).replace(tzinfo=TZ_UTC)

def _fmt_local(dt: _dt.datetime) -> str:
    """Format datetime in Pacific Time."""
    loc = dt.astimezone(TZ_PT)
    return f"{loc.strftime('%b')} {loc.day} {loc.strftime('%A')} {loc.strftime('%-I:%M%p').lower()} Pacific"

def _pad_ids() -> list:
    """Get IDs of Vandenberg launchpads."""
    return [p["id"] for p in _rq.get(URL_PADS, timeout=10).json()
            if "vandenberg" in p.get("locality", "").lower()]

def _rocket_name(rid: str) -> str:
    """Get rocket name by ID, caching results."""
    if rid in _ROCKETS:
        return _ROCKETS[rid]
    name = _rq.get(f"https://api.spacexdata.com/v4/rockets/{rid}", timeout=10).json()["name"]
    _ROCKETS[rid] = name
    return name

def _rocket_slug(rocket: str) -> str:
    """Generate slug for rocket name."""
    rocket_map = {
        "Falcon 9": "falcon-9",
        "Falcon Heavy": "falcon-heavy",
        "Starship": "starship"
    }
    return rocket_map.get(rocket, _slug(rocket))

def _validate_url(url: str) -> bool:
    """Check if a URL returns a 200 status code."""
    try:
        response = _rq.head(url, timeout=5, allow_redirects=True)
        return response.status_code == 200
    except _rq.RequestException:
        return False

def _links(mission: str, rocket: str, slug: str | None) -> tuple[str, str]:
    """Generate SpaceX and RocketLaunch.org URLs."""
    # SpaceX URL: Handle Starlink missions separately
    if "starlink" in mission.lower():
        # Extract group and batch (e.g., "Group 15-3" → "15-3")
        match = _re.search(r"(\d+-\d+)", mission)
        if match:
            sx_slug = f"sl-{match.group(1)}"
        else:
            sx_slug = slug if slug else _slug(mission)  # Fallback
    else:
        sx_slug = slug if slug else _slug(mission)  # Use API slug or generate
    sx = f"https://www.spacex.com/launches/mission/?missionId={sx_slug}"

    # RocketLaunch.org URL: Generate rocket and mission slugs
    rl_rocket_slug = _rocket_slug(rocket)
    rl_mission_slug = _slug(mission)
    rl = f"https://rocketlaunch.org/mission-{rl_rocket_slug}-{rl_mission_slug}"
    
    # Validate RocketLaunch.org URL, fallback to schedule if invalid
    if not _validate_url(rl):
        rl = "https://rocketlaunch.org/launch-schedule/spacex"

    return sx, rl

# ───── fetchers ─────
def _spacex() -> list:
    """Fetch upcoming Vandenberg launches from SpaceX API."""
    docs = _rq.post(URL_LAUNCHES, json={
        "query": {"upcoming": True, "launchpad": {"$in": _pad_ids()}},
        "options": {"sort": {"date_utc": "asc"},
                    "select": ["name", "date_utc", "rocket", "slug"]}
    }, timeout=10).json()["docs"]
    return [d for d in docs if NOW_UTC <= _to_dt(d["date_utc"]) <= LIMIT_UTC]

def _launch_library() -> list:
    """Fetch upcoming Vandenberg launches from TheSpaceDevs API."""
    raw = _rq.get(URL_LL, params={
        "lsp__name": "SpaceX",
        "location__name__icontains": "Vandenberg",
        "status": 1,  # “Go”
        "limit": 100,
        "ordering": "window_start"
    }, timeout=10).json()["results"]

    cleaned = []
    for l in raw:
        dt = _to_dt(l["window_start"])
        if not NOW_UTC <= dt <= LIMIT_UTC:
            continue
        name_raw = l["name"]
        if "|" in name_raw:
            rocket_part, mission_part = [p.strip() for p in name_raw.split("|", 1)]
        else:
            rocket_part, mission_part = "Falcon 9", name_raw
        cleaned.append
