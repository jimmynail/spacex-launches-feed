#!/usr/bin/env python3
# send_digest.py  –  e-mails upcoming VSFB launches (21 days)

import datetime, os, requests, zoneinfo, smtplib, ssl
from email.message import EmailMessage

URL_PADS     = "https://api.spacexdata.com/v4/launchpads"
URL_LAUNCHES = "https://api.spacexdata.com/v4/launches/query"
ZONE_PT      = zoneinfo.ZoneInfo("America/Los_Angeles")
NOW_UTC      = datetime.datetime.utcnow().replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
LIMIT        = NOW_UTC + datetime.timedelta(weeks=3)

def vandenberg_pad_ids():
    pads = requests.get(URL_PADS, timeout=10).json()
    return [p["id"] for p in pads
            if p.get("locality","").startswith("Vandenberg")]

def fetch_launches():
    pad_ids = vandenberg_pad_ids()
    payload = {
        "query": {
            "upcoming": True,
            "launchpad": {"$in": pad_ids}
        },
        "options": {
            "sort": {"date_utc": "asc"},
            "select": ["name", "date_utc", "date_precision"]
        }
    }
    docs = requests.post(URL_LAUNCHES, json=payload, timeout=10).json()["docs"]
    # keep only launches ≤ 21 days out
    return [d for d in docs
            if datetime.datetime.fromisoformat(d["date_utc"][:-1]).replace(
                   tzinfo=zoneinfo.ZoneInfo("UTC")) <= LIMIT]
    
def format_body(docs):
    if not docs:
        return ("No Vandenberg launches currently scheduled in the next "
                "three weeks.")
    lines = []
    for d in docs:
        t_local = to_local(
            datetime.datetime.fromisoformat(d["date_utc"][:-1])
        ).strftime("%a %b %d %I:%M %p")
        lines.append(f"• {t_local} — {d['name']}")
    return "\n".join(lines)

def send_email(body):
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"]   = os.environ["DEST_EMAIL"]
    msg["Subject"] = "Upcoming Vandenberg SpaceX launches (next 3 weeks)"
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(os.environ["SMTP_HOST"],
                          int(os.environ.get("SMTP_PORT", "465")),
                          context=ctx) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)

if __name__ == "__main__":
    body = format_body(fetch_launches())
    send_email(body)
