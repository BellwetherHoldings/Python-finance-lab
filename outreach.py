"""
Cold Email Outreach — ClearJet Pressure Washing LLC

Sends personalized cold emails to enriched leads and manages follow-ups.
Uses Gmail SMTP (or any SMTP provider) via environment variables.

Setup:
    cp .env.example .env
    # Fill in EMAIL_FROM and EMAIL_PASSWORD, then:
    source .env

    # Import leads from enricher.py output:
    python outreach.py --import-csv output/enriched_leads.csv

    # Dry-run to preview emails without sending:
    python outreach.py --send --dry-run
    python outreach.py --follow-ups --dry-run

    # Go live:
    python outreach.py --send
    python outreach.py --follow-ups

    # Check pipeline stats:
    python outreach.py --stats

CAN-SPAM compliance
-------------------
All emails include a physical mailing address and an unsubscribe link.
Honor unsubscribe requests immediately by running:
    python outreach.py --unsubscribe <lead_id>
"""

import argparse
import logging
import os
import smtplib
import string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import tracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — set via environment variables (see .env.example)
# ---------------------------------------------------------------------------
SMTP_HOST       = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))
EMAIL_FROM      = os.getenv("EMAIL_FROM", "")
EMAIL_PASSWORD  = os.getenv("EMAIL_PASSWORD", "")   # Gmail App Password
FROM_NAME       = os.getenv("FROM_NAME", "ClearJet Pressure Washing")
PHYSICAL_ADDRESS = os.getenv(
    "PHYSICAL_ADDRESS", "ClearJet Pressure Washing LLC, Memphis, TN"
)
UNSUBSCRIBE_URL = os.getenv(
    "UNSUBSCRIBE_URL", "mailto:unsubscribe@clearjetpw.com?subject=unsubscribe"
)
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "25"))

# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------
_TEMPLATES: dict[str, tuple[str, str]] = {
    "initial": (
        "Commercial Pressure Washing for {business_name}",
        """\
Hi {business_name} team,

My name is Jakob, and I run ClearJet Pressure Washing — we specialise in
commercial exterior cleaning for property managers, dealerships, apartment
complexes, and retail centres across the Memphis area.

We handle:

  • Parking lots and dumpster pads
  • Building exteriors and sidewalks
  • Drive-throughs and storefronts
  • Fleet vehicle washing

We offer flexible scheduling (nights/weekends) and work with property managers
on recurring maintenance contracts. Most clients get a quote within 24 hours.

If this sounds useful for your property at {address}, I'd love to send a quick
quote — no commitment needed. Would a 10-minute call this week work for you?

Best,
Jakob Tillman
ClearJet Pressure Washing LLC
Memphis, TN | (901) 555-0100
clearjetpw.com

---
{physical_address}
To stop receiving emails: {unsubscribe_url}
""",
    ),

    "follow_up_1": (
        "Re: Commercial Pressure Washing for {business_name}",
        """\
Hi {business_name} team,

Just a quick follow-up on my note from a few days ago about commercial
pressure washing services for {address}.

We're booking spring cleaning jobs in the Memphis area and have a few open
slots this month. If you'd like a free quote, just reply here or call
(901) 555-0100 — takes about 10 minutes.

Happy to work around your schedule.

Best,
Jakob Tillman
ClearJet Pressure Washing LLC

---
{physical_address}
To stop receiving emails: {unsubscribe_url}
""",
    ),

    "follow_up_2": (
        "Last note — ClearJet Pressure Washing",
        """\
Hi {business_name} team,

This is my last note about commercial pressure washing services. If the
timing isn't right, no worries at all — feel free to reach out if you ever
need exterior cleaning down the road.

Wishing you a great season.

Jakob Tillman
ClearJet Pressure Washing LLC
Memphis, TN | (901) 555-0100

---
{physical_address}
To stop receiving emails: {unsubscribe_url}
""",
    ),
}


def _render(template: str, lead: dict) -> str:
    return string.Template(template).safe_substitute(
        business_name=lead.get("business_name", ""),
        address=lead.get("address", "your property"),
        physical_address=PHYSICAL_ADDRESS,
        unsubscribe_url=UNSUBSCRIBE_URL,
    )


def _send(to_email: str, subject: str, body: str) -> bool:
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        log.error("Set EMAIL_FROM and EMAIL_PASSWORD environment variables before sending.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{EMAIL_FROM}>"
    msg["To"] = to_email
    msg["List-Unsubscribe"] = f"<{UNSUBSCRIBE_URL}>"
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(EMAIL_FROM, EMAIL_PASSWORD)
            srv.sendmail(EMAIL_FROM, to_email, msg.as_string())
        return True
    except smtplib.SMTPException as exc:
        log.error("SMTP error → %s: %s", to_email, exc)
        return False


# ---------------------------------------------------------------------------
# Send actions
# ---------------------------------------------------------------------------

def send_initial(dry_run: bool = False) -> None:
    leads = tracker.get_pending_leads(limit=DAILY_LIMIT)
    log.info("Initial sends: %d leads queued (daily limit %d)", len(leads), DAILY_LIMIT)
    subj_tpl, body_tpl = _TEMPLATES["initial"]
    sent = 0
    for lead in leads:
        d = dict(lead)
        subject = _render(subj_tpl, d)
        body    = _render(body_tpl, d)
        if dry_run:
            log.info("[DRY RUN] %s <%s>\n  Subject: %s", d["business_name"], d["email"], subject)
        else:
            log.info("Sending → %s <%s>", d["business_name"], d["email"])
            if _send(d["email"], subject, body):
                tracker.log_sent(d["id"], "initial")
                sent += 1
            else:
                log.warning("Failed: %s", d["email"])
    log.info("Done — sent %d/%d", sent, len(leads))


def send_follow_ups(step: str, dry_run: bool = False) -> None:
    leads = tracker.get_due_follow_ups(step)
    log.info("%s: %d leads due", step, len(leads))
    subj_tpl, body_tpl = _TEMPLATES[step]
    sent = 0
    for lead in leads:
        d = dict(lead)
        subject = _render(subj_tpl, d)
        body    = _render(body_tpl, d)
        if dry_run:
            log.info("[DRY RUN] %s → %s <%s>", step, d["business_name"], d["email"])
        else:
            log.info("Sending %s → %s <%s>", step, d["business_name"], d["email"])
            if _send(d["email"], subject, body):
                tracker.log_sent(d["id"], step)
                sent += 1
            else:
                log.warning("Failed: %s", d["email"])
    log.info("Done — sent %d/%d", sent, len(leads))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Cold Email Outreach — ClearJet")
    parser.add_argument("--import-csv",    metavar="FILE", help="Import enriched CSV into tracker DB")
    parser.add_argument("--send",          action="store_true", help="Send initial emails (up to DAILY_LIMIT)")
    parser.add_argument("--follow-ups",    action="store_true", help="Send all due follow-up emails")
    parser.add_argument("--stats",         action="store_true", help="Print pipeline stats")
    parser.add_argument("--unsubscribe",   metavar="LEAD_ID", type=int, help="Mark a lead as unsubscribed")
    parser.add_argument("--dry-run",       action="store_true", help="Preview emails without sending")
    args = parser.parse_args()

    tracker.init_db()

    if args.import_csv:
        n = tracker.import_leads(args.import_csv)
        log.info("Imported %d new leads from %s", n, args.import_csv)

    if args.unsubscribe:
        tracker.log_unsubscribe(args.unsubscribe)
        log.info("Lead %d marked as unsubscribed", args.unsubscribe)

    if args.send or args.follow_ups:
        n = tracker.geocode_leads()
        if n:
            log.info("Geocoded %d new leads", n)

    if args.send:
        send_initial(dry_run=args.dry_run)

    if args.follow_ups:
        send_follow_ups("follow_up_1", dry_run=args.dry_run)
        send_follow_ups("follow_up_2", dry_run=args.dry_run)

    if args.stats:
        stats = tracker.get_stats()
        print("\n--- ClearJet Outreach Pipeline ---")
        for key, val in stats.items():
            print(f"  {key:<20} {val}")
        print()


if __name__ == "__main__":
    main()
