"""
Step 3: Cold Email Sender
5-touch automated email sequence via Gmail SMTP.

Setup required:
  1. Enable 2FA on your Gmail account
  2. Create an App Password: Google Account > Security > App passwords
  3. Set environment variables:
       set CLEARJET_EMAIL=jakobtillman33@gmail.com
       set CLEARJET_EMAIL_PASSWORD=your-app-password-here
"""

import smtplib
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import config
import database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email templates — 5-touch sequence
# ---------------------------------------------------------------------------

def get_email_sequence(business_name: str) -> list[dict]:
    """
    Returns 5 email templates customized with the business name.
    Each dict has 'subject' and 'body' keys.
    """
    return [
        # Touch 1: Introduction
        {
            "subject": f"Commercial Pressure Washing for {business_name}",
            "body": f"""Hi there,

I'm Jakob with ClearJet Pressure Washing — we handle commercial pressure washing and landscaping for businesses across Memphis and West Tennessee.

I came across {business_name} and wanted to reach out. We work with property managers, commercial plazas, dealerships, and similar businesses to keep exteriors looking sharp.

Our services include:
- Building & sidewalk pressure washing
- Parking lot cleaning
- Dumpster pad cleaning
- Commercial landscaping & maintenance

Would you be open to a quick call this week to see if we can help?

Best,
Jakob Tillman
ClearJet Pressure Washing LLC
""",
        },
        # Touch 2: Value add
        {
            "subject": f"Quick follow-up — {business_name}",
            "body": f"""Hi,

Just following up on my last email. I know you're busy so I'll keep it short.

A lot of the commercial properties we service in the Memphis area see a noticeable difference in curb appeal after just one wash — and several have told us it directly impacted tenant satisfaction and foot traffic.

Would it make sense to set up a free walkthrough of your property? No commitment — just a chance for me to show you what we can do.

Best,
Jakob Tillman
ClearJet Pressure Washing LLC
""",
        },
        # Touch 3: Social proof
        {
            "subject": f"How we helped a property like {business_name}",
            "body": f"""Hi,

Wanted to share a quick example. We recently completed a full exterior wash for a commercial plaza in East Memphis — parking lot, sidewalks, building facade, the works.

The property manager said it was the best their property had looked in years, and they signed on for monthly maintenance.

I'd love to do the same for {business_name}. Can I send over a quick quote?

Best,
Jakob Tillman
ClearJet Pressure Washing LLC
""",
        },
        # Touch 4: Direct ask
        {
            "subject": f"Still interested in a quote, {business_name}?",
            "body": f"""Hi,

I've reached out a couple times — totally understand if the timing hasn't been right.

If exterior maintenance is something you handle, I'd love just 5 minutes to chat. We can usually turn around a free estimate within 24 hours.

What does your schedule look like this week?

Best,
Jakob Tillman
ClearJet Pressure Washing LLC
""",
        },
        # Touch 5: Breakup
        {
            "subject": f"Last note from ClearJet — {business_name}",
            "body": f"""Hi,

This will be my last email — I don't want to clutter your inbox.

If you ever need commercial pressure washing or landscaping in the Memphis area, we're here. Just reply to this email anytime and we'll get you taken care of.

Wishing you and {business_name} all the best.

Jakob Tillman
ClearJet Pressure Washing LLC
""",
        },
    ]


# ---------------------------------------------------------------------------
# SMTP sender
# ---------------------------------------------------------------------------

def send_email(to_email: str, subject: str, body: str) -> bool:
    """Send a single email via Gmail SMTP. Returns True on success."""
    if not config.SENDER_PASSWORD:
        log.error("No email password set. Set CLEARJET_EMAIL_PASSWORD environment variable.")
        return False

    msg = MIMEMultipart()
    msg["From"] = f"{config.SENDER_NAME} <{config.SENDER_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SENDER_EMAIL, config.SENDER_PASSWORD)
            server.send_message(msg)
        return True
    except smtplib.SMTPAuthenticationError:
        log.error("Gmail auth failed. Make sure you're using an App Password, not your regular password.")
        return False
    except smtplib.SMTPException as exc:
        log.error("SMTP error sending to %s: %s", to_email, exc)
        return False


# ---------------------------------------------------------------------------
# Sequence runner
# ---------------------------------------------------------------------------

def run_email_sequence(db_path: str = None) -> int:
    """
    Send the next touch to all leads that are due.
    Respects daily send limit and delay between sends.
    Returns number of emails sent.
    """
    conn = database.get_db(db_path)
    leads = database.get_leads_needing_email(conn)
    log.info("Found %d leads ready for next touch", len(leads))

    sent_count = 0

    for lead in leads:
        if sent_count >= config.DAILY_SEND_LIMIT:
            log.info("Hit daily send limit (%d), stopping", config.DAILY_SEND_LIMIT)
            break

        touch_num = lead["current_touch"]  # 0-indexed, next touch to send
        sequence = get_email_sequence(lead["business_name"])

        if touch_num >= len(sequence):
            continue

        template = sequence[touch_num]
        to_email = lead["email"]
        subject = template["subject"]
        body = template["body"]

        log.info(
            "  Sending touch %d to %s (%s)",
            touch_num + 1, lead["business_name"], to_email,
        )

        success = send_email(to_email, subject, body)
        if success:
            database.mark_email_sent(conn, lead["id"], touch_num + 1, subject, body)
            sent_count += 1
            log.info("    -> Sent successfully")

            # Don't blast — wait between sends
            if sent_count < len(leads):
                time.sleep(config.DELAY_BETWEEN_EMAILS_SEC)
        else:
            log.warning("    -> Failed to send")

    conn.close()
    log.info("Sent %d emails this run", sent_count)
    return sent_count


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Send cold email sequences")
    parser.add_argument("--run", action="store_true", help="Send next batch of emails")
    parser.add_argument("--test", type=str, help="Send test email to this address")
    args = parser.parse_args()

    if args.test:
        ok = send_email(args.test, "Test from ClearJet", "This is a test email from your outreach system.")
        print("Sent!" if ok else "Failed — check your CLEARJET_EMAIL_PASSWORD env var")
    elif args.run:
        run_email_sequence()
    else:
        parser.print_help()
