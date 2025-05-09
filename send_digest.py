#!/usr/bin/env python3
# send_digest.py  –  e-mails upcoming VSFB launches (21 days)

import datetime, os, requests, zoneinfo, smtplib, ssl
from email.message import EmailMessage

PAD_SLC4E = "5e9e4502f5090995de566f86"          # Falcon-9 / Heavy pad id

def fetch_launches():
    end = (datetime.datetime.utcnow()+datetime.timedelta(weeks=3)
           ).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "query": {"upcoming": True,
                  "launchpad": PAD_SLC4E,
                  "date_utc": {"$lte": end}},
        "options": {"sort": {"date_utc": "asc"},
                    "select": ["name", "date_utc", "window"]}
    }
    r = requests.post("https://api.spacexdata.com/v4/launches/query",
                      json=payload, timeout=10)
    r.raise_for_status()
    return r.json()["docs"]

def to_local(dt_utc):
    return (dt_utc.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
            .astimezone(zoneinfo.ZoneInfo("America/Los_Angeles")))

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
