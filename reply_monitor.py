"""
Step 5: IMAP Reply Monitor
Checks Gmail inbox for replies from leads and pauses their sequences.

Connects via IMAP, scans recent emails, matches sender addresses
against the leads database, and marks them as 'replied'.
"""

import imaplib
import email
import email.utils
import re
import logging
from datetime import datetime, timedelta

import config
import database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def check_for_replies(db_path: str = None, days_back: int = 7) -> int:
    """
    Connect to Gmail IMAP, check inbox for replies from known leads,
    and mark those leads as 'replied' to pause their sequence.
    Returns number of replies detected.
    """
    if not config.SENDER_PASSWORD:
        log.error("No email password set. Set CLEARJET_EMAIL_PASSWORD environment variable.")
        return 0

    conn = database.get_db(db_path)

    # Get all lead emails we're actively emailing
    active_leads = conn.execute(
        "SELECT id, email, business_name FROM leads WHERE status = 'emailing' AND email != ''"
    ).fetchall()

    if not active_leads:
        log.info("No active leads to check replies for")
        conn.close()
        return 0

    # Build a lookup: email -> lead
    email_to_lead = {row["email"].lower(): row for row in active_leads}
    log.info("Checking replies for %d active leads", len(email_to_lead))

    reply_count = 0

    try:
        imap = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT)
        imap.login(config.SENDER_EMAIL, config.SENDER_PASSWORD)
        imap.select("INBOX")

        # Search for recent emails
        since_date = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
        status, msg_ids = imap.search(None, f'(SINCE "{since_date}")')

        if status != "OK" or not msg_ids[0]:
            log.info("No recent emails found")
            imap.logout()
            conn.close()
            return 0

        ids = msg_ids[0].split()
        log.info("Checking %d recent emails", len(ids))

        for msg_id in ids:
            try:
                status, data = imap.fetch(msg_id, "(RFC822.HEADER)")
                if status != "OK":
                    continue

                header_data = data[0][1]
                msg = email.message_from_bytes(header_data)

                # Get sender email
                from_header = msg.get("From", "")
                _, sender_addr = email.utils.parseaddr(from_header)
                sender_addr = sender_addr.lower().strip()

                if sender_addr in email_to_lead:
                    lead = email_to_lead[sender_addr]
                    database.mark_replied(conn, lead["id"])
                    reply_count += 1
                    log.info(
                        "  REPLY detected from %s (%s) — sequence paused",
                        lead["business_name"], sender_addr,
                    )
                    # Remove from lookup so we don't double-count
                    del email_to_lead[sender_addr]

            except Exception as exc:
                log.warning("  Error processing message: %s", exc)
                continue

        imap.logout()

    except imaplib.IMAP4.error as exc:
        log.error("IMAP error: %s", exc)
    except Exception as exc:
        log.error("Error checking replies: %s", exc)

    conn.close()
    log.info("Detected %d replies", reply_count)
    return reply_count


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Check for replies from leads")
    parser.add_argument("--days", type=int, default=7, help="How many days back to check")
    parser.add_argument("--run", action="store_true", help="Run the reply check")
    args = parser.parse_args()

    if args.run:
        check_for_replies(days_back=args.days)
    else:
        parser.print_help()
