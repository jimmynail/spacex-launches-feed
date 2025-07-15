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
WEEKS_AHEAD = 16  # Covers "Next 4 Weeks" and "After That"
START_UTC = NOW_UTC - _dt.timedelta(days=2)  # Include recent launches
FOUR_WEEKS_UTC = NOW_UTC + _dt.timedelta(weeks=4)
LIMIT_UTC = NOW_UTC + _dt.timedelta(weeks=WEEKS_AHEAD)
_ROCKETS = {}  # Cache rocket ID to name
_PADS = {}  # Cache pad ID to name
VANDENBERG_PAD_IDS = ["5e9e4502f509092b78566f87"]  # SLC-4E (SpaceX API)
REPO_URL = "https://github.com/jimmynail/spacex-launches-feed"
SCRIPT_URL = f"{REPO_URL}/blob/main/send_digest.py"
WORKFLOW_URL = f"{REPO_URL}/actions/workflows/send_digest.yml"

# ───── Helper Functions ─────
def _slug(s: str) -> str:
    """Generate a URL-safe slug from a string."""
    s = _re.sub(r"[’'`]", "", s.lower())
    s = _re.sub(r"[^a-z0-9]+", "-", s)
    return _re.sub(r"-{2,}", "-", s.strip("-"))

def _to_dt(iso: str) -> _dt.datetime:
    """Convert ISO date string to UTC datetime."""
    return _dt.datetime.fromisoformat(iso.rstrip("Z")).replace(tzinfo=TZ_UTC)

def _fmt_local(dt: _dt.datetime, tz: _zi.ZoneInfo) -> tuple[str, str]:
    """Format datetime in local time zone, return time string and tz name."""
    loc = dt.astimezone(tz)
    time_str = f"{loc.strftime('%A')} {loc.strftime('%b')} {loc.day} {loc.strftime('%-I:%M%p').lower()}"
    tz_name = tz.tzname(loc) or "Pacific"
    return time_str, tz_name

def _pad_ids() -> list:
    """Get IDs of Vandenberg launchpads."""
    try:
        pads = _rq.get(URL_PADS, timeout=10).json()
        logger.info(f"Fetched {len(pads)} launchpads")
        vandenberg_ids = [p["id"] for p in pads if "vandenberg" in p.get("locality", "").lower()]
        valid_ids = [pid for pid in vandenberg_ids if pid in VANDENBERG_PAD_IDS]
        logger.info(f"Found {len(valid_ids)} Vandenberg launchpads: {valid_ids}")
        return valid_ids
    except Exception as e:
        logger.error(f"Failed to fetch launchpads: {str(e)}")
        return VANDENBERG_PAD_IDS

def _get_pad_info(pad_id: str) -> tuple[str, str]:
    """Get launchpad name and locality."""
    if pad_id in _PADS:
        return _PADS[pad_id]
    try:
        pad = _rq.get(f"{URL_PADS}/{pad_id}", timeout=5).json()
        name = pad.get("name", "Unknown")
        locality = pad.get("locality", "Unknown")
        _PADS[pad_id] = (name, locality)
        logger.info(f"Cached pad {pad_id}: {name}, {locality}")
        return name, locality
    except Exception as e:
        logger.error(f"Failed to fetch pad {pad_id}: {str(e)}")
        return "Unknown", "Unknown"

def _rocket_name(rid: str) -> str:
    """Get rocket name by ID, caching results."""
    if rid in _ROCKETS:
        return _ROCKETS[rid]
    try:
        name = _rq.get(f"{URL_ROCKETS}/{rid}", timeout=10).json()["name"]
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
    if "starlink" in mission.lower():
        match = _re.search(r"(\d+-\d+)", mission)
        sx_slug = f"sl-{match.group(1)}" if match else (slug or _slug(mission))
    else:
        sx_slug = slug if slug else _slug(mission)
    sx = f"https://www.spacex.com/launches/mission/?missionId={sx_slug}"
    logger.info(f"Generated SpaceX URL for '{mission}': {sx}")

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
        # Primary query with Vandenberg filter
        docs = _rq.post(URL_LAUNCHES, json={
            "query": {
                "launchpad": {"$in": _pad_ids()},
                "date_utc": {"$gte": START_UTC.isoformat(), "$lte": LIMIT_UTC.isoformat()}
            },
            "options": {
                "sort": {"date_utc": "asc"},
                "select": ["name", "date_utc", "rocket", "slug", "launchpad"]
            }
        }, timeout=10).json()["docs"]
        logger.info(f"Raw SpaceX API response (Vandenberg filter): {len(docs)} launches: {[d['name'] for d in docs]}")
        
        # Fallback query if no launches
        if not docs:
            logger.info("No launches with Vandenberg filter, trying broader query")
            docs = _rq.post(URL_LAUNCHES, json={
                "query": {
                    "date_utc": {"$gte": START_UTC.isoformat(), "$lte": LIMIT_UTC.isoformat()}
                },
                "options": {
                    "sort": {"date_utc": "asc"},
                    "select": ["name", "date_utc", "rocket", "slug", "launchpad"]
                }
            }, timeout=10).json()["docs"]
            logger.info(f"Raw SpaceX API response (broad query): {len(docs)} launches: {[d['name'] for d in docs]}")

        upcoming = []
        for d in docs:
            dt = _to_dt(d["date_utc"])
            if not (START_UTC <= dt <= LIMIT_UTC):
                logger.info(f"Excluded launch outside time window: {d['name']} ({dt})")
                continue
            if d["launchpad"] not in VANDENBERG_PAD_IDS:
                logger.warning(f"Excluded non-Vandenberg launch: {d['name']} (Launchpad: {d['launchpad']})")
                continue
            pad_name, locality = _get_pad_info(d["launchpad"])
            d["pad_name"] = pad_name
            d["location"] = locality.split(",")[0].strip()
            if dt.date() == NOW_UTC.date():
                logger.info(f"Included same-day launch: {d['name']} ({dt})")
            upcoming.append(d)
        logger.info(f"Fetched {len(upcoming)} upcoming SpaceX Vandenberg launches")
        return upcoming
    except Exception as e:
        logger.error(f"SpaceX API fetch failed: {str(e)}")
        return []

def _launch_library() -> list:
    """Fetch upcoming Vandenberg SpaceX launches from TheSpaceDevs API."""
    try:
        raw = _rq.get(URL_LL, params={
            "lsp__name": "SpaceX",
            "pad__name__icontains": "SLC-4",
            "limit": 100,
            "ordering": "window_start"
        }, timeout=10).json()["results"]
        logger.info(f"Raw TheSpaceDevs API response: {len(raw)} launches: {[l['name'] for l in raw]}")
        cleaned = []
        for l in raw:
            dt = _to_dt(l["window_start"])
            if not (START_UTC <= dt <= LIMIT_UTC):
                logger.info(f"Excluded launch outside time window: {l['name']} ({dt})")
                continue
            pad_name = l.get("pad", {}).get("name", "").lower()
            logger.info(f"Processing launch: {l['name']} (Raw pad name: {pad_name})")
            pad_match = _re.search(r"slc-?4[eE]", pad_name, _re.IGNORECASE) or "4e" in pad_name or "4w" in pad_name
            logger.info(f"Pad match result: {pad_match} for pad_name: {pad_name}")
            if not pad_match:
                logger.warning(f"Excluded non-Vandenberg launch: {l['name']} (Pad: {pad_name})")
                continue
            name_raw = l["name"]
            rocket_part, mission_part = name_raw.split("|", 1) if "|" in name_raw else ("Falcon 9", name_raw)
            rocket_part, mission_part = rocket_part.strip(), mission_part.strip()
            location = l.get("pad", {}).get("location", {}).get("name", "Vandenberg")
            cleaned.append({
                "name": mission_part,
                "rocket_name": rocket_part,
                "date_utc": l["window_start"],
                "slug": None,
                "pad_name": l.get("pad", {}).get("name", "SLC-4E"),
                "location": location.split(",")[0].strip()
            })
            if dt.date() == NOW_UTC.date():
                logger.info(f"Included same-day launch: {l['name']} ({dt})")
        logger.info(f"Fetched {len(cleaned)} upcoming TheSpaceDevs Vandenberg launches")
        return cleaned
    except Exception as e:
        logger.error(f"TheSpaceDevs API fetch failed: {str(e)}")
        return []

# ───── Email Rendering ─────
def _render(items: list) -> tuple[str, str]:
    """Render text and HTML email bodies with sections and footer."""
    if not items:
        msg = f"No Vandenberg launches currently scheduled in the next {WEEKS_AHEAD} weeks."
        logger.info("No launches found, using fallback message")
        footer_txt = (
            f"\n---\n"
            f"This email lists upcoming SpaceX launches from Vandenberg SFB within a {WEEKS_AHEAD}-week window.\n"
            f"Edit the look-forward window: {SCRIPT_URL}. Disable these emails: {WORKFLOW_URL}"
        )
        footer_html = (
            f"<p style='font-size: 10px; color: #999;'>"
            f"This email lists upcoming SpaceX launches from Vandenberg SFB within a {WEEKS_AHEAD}-week window.<br>"
            f"<a href='{SCRIPT_URL}'>Edit</a> the look-forward window or <a href='{WORKFLOW_URL}'>disable</a> these emails."
            f"</p>"
        )
        logger.info(f"Rendered footer: This email lists upcoming SpaceX launches...")
        return msg + footer_txt, f"<p>{msg}</p>{footer_html}"

    next_4_weeks = [d for d in items if _to_dt(d["date_utc"]) <= FOUR_WEEKS_UTC]
    after_that = [d for d in items if _to_dt(d["date_utc"]) > FOUR_WEEKS_UTC]

    txt_lines, html_lines = [], ["<ul style='padding-left:0'>"]
    
    # Next 4 Weeks Section
    if next_4_weeks:
        txt_lines.append("**Next 4 Weeks**")
        html_lines.append("<h3>Next 4 Weeks</h3>")
        for d in next_4_weeks:
            dt, mission = _to_dt(d["date_utc"]), d["name"]
            rocket = d.get("rocket_name") or _rocket_name(d["rocket"])
            location = d.get("location", "Vandenberg")
            time_str, tz_name = _fmt_local(dt, TZ_PT)
            sx, rl = _links(mission, rocket, d.get("slug"))

            loc_dt = dt.astimezone(TZ_PT)
            wd, hr = loc_dt.weekday(), loc_dt.hour      # 0=Mon … 6=Sun
            is_highlight = (
                (wd == 4 and hr >= 13)      # Friday 1 pm or later
                or (wd == 5)                # all of Saturday
                or (wd == 6 and hr < 18)    # Sunday before 6 pm
            )
            time_line = f"**🚀 {time_str} {tz_name}**" if is_highlight else f"🚀 {time_str} {tz_name}"
            html_time = (
                f"<span style='color: red;'><strong>{time_str} {tz_name}</strong></span>"
                if is_highlight else f"<strong>{time_str} {tz_name}</strong>"
            )

            summary = f"{mission}, {rocket}, {location}"
            logger.info(f"Rendered summary: {summary} (Highlight: {is_highlight})")
            txt_lines.append(f"{time_line}\n{summary}\nSpaceX: {sx}\nRocketlaunch: {rl}\n")
            html_lines.append(
                f"<li style='margin-bottom:12px;list-style:none'>"
                f"{html_time}<br>{summary}<br>"
                f"<a href='{sx}'>SpaceX</a> "
                f"<a href='{rl}'>Rocketlaunch</a></li>"
            )

    # After That Section
    if after_that:
        txt_lines.append("**After That**")
        html_lines.append("<h3>After That</h3>")
        for d in after_that:
            dt, mission = _to_dt(d["date_utc"]), d["name"]
            rocket = d.get("rocket_name") or _rocket_name(d["rocket"])
            location = d.get("location", "Vandenberg")
            time_str, tz_name = _fmt_local(dt, TZ_PT)
            sx, rl = _links(mission, rocket, d.get("slug"))

            loc_dt = dt.astimezone(TZ_PT)
            is_highlight = (
                (wd == 4 and hr >= 13)      # Friday 1 pm or later
                or (wd == 5)                # all of Saturday
                or (wd == 6 and hr < 18)    # Sunday before 6 pm
            )
            time_line = f"**🚀 {time_str} {tz_name}**" if is_highlight else f"🚀 {time_str} {tz_name}"
            html_time = (
                f"<span style='color: red;'><strong>{time_str} {tz_name}</strong></span>"
                if is_highlight else f"<strong>{time_str} {tz_name}</strong>"
            )

            summary = f"{mission}, {rocket}, {location}"
            logger.info(f"Rendered summary: {summary} (Highlight: {is_highlight})")
            txt_lines.append(f"{time_line}\n{summary}\nSpaceX: {sx}\nRocketlaunch: {rl}\n")
            html_lines.append(
                f"<li style='margin-bottom:12px;list-style:none'>"
                f"{html_time}<br>{summary}<br>"
                f"<a href='{sx}'>SpaceX</a> "
                f"<a href='{rl}'>Rocketlaunch</a></li>"
            )

    # Footer
    footer_txt = (
        f"\n---\n"
        f"This email lists upcoming SpaceX launches from Vandenberg SFB within a {WEEKS_AHEAD}-week window.\n"
        f"Edit the look-forward window: {SCRIPT_URL}\n"
        f"Disable these emails: {WORKFLOW_URL}\n"
    )
    footer_html = (
        f"<p style='font-size: 10px; color: #999;'>"
        f"This email lists upcoming SpaceX launches from Vandenberg SFB within a {WEEKS_AHEAD}-week window.<br>"
        f"<a href='{SCRIPT_URL}'>Edit</a> the look-forward window or <a href='{WORKFLOW_URL}'>disable</a> these emails."
        f"</p>"
    )
    logger.info(f"Rendered footer: This email lists upcoming SpaceX launches...")

    txt_lines.append(footer_txt)
    html_lines.append(footer_html)
    logger.info(f"Rendered email content for {len(items)} launches ({len(next_4_weeks)} in Next 4 Weeks, {len(after_that)} in After That)")
    return "\n".join(txt_lines), "\n".join(html_lines)

# ───── Email Sending ─────
def _send(txt: str, html: str) -> None:
    """Send email with launch details."""
    m = _Email()
    m["From"] = _os.environ["SMTP_USER"]
    m["To"] = _os.environ["DEST_EMAIL"]
    m["Subject"] = f"Ad Astra! Upcoming Vandenberg SpaceX launches (next {WEEKS_AHEAD} weeks)"
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
