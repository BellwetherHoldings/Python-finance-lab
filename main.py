"""
ClearJet Outreach System - Main Controller
Runs the full pipeline or individual steps.

Usage:
    python main.py --scrape          Scrape Google Maps for leads (Step 1)
    python main.py --find-emails     Find emails from business websites (Step 2)
    python main.py --send            Send next batch of cold emails (Step 3)
    python main.py --check-replies   Check inbox for replies (Step 5)
    python main.py --status          Show pipeline stats
    python main.py --full            Run the full pipeline (scrape -> emails -> send)

Setup:
    1. pip install -r requirements.txt
    2. For browser mode: python -m playwright install chromium
    3. Set your Gmail App Password:
         Windows:  set CLEARJET_EMAIL_PASSWORD=your-app-password
         Mac/Linux: export CLEARJET_EMAIL_PASSWORD=your-app-password
"""

import argparse
import logging
import sys

import config
import database
import scraper
import email_finder
import emailer
import reply_monitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def show_status():
    """Print current pipeline status."""
    conn = database.get_db()
    stats = database.get_stats(conn)
    conn.close()

    print("\n" + "=" * 50)
    print(f"  ClearJet Outreach System - Status")
    print("=" * 50)
    print(f"  Total leads:      {stats['total']}")
    print(f"  With email:       {stats['with_email']}")
    print(f"  ---")
    print(f"  New (not sent):   {stats['new']}")
    print(f"  In sequence:      {stats['emailing']}")
    print(f"  Replied:          {stats['replied']}")
    print(f"  Dead/bounced:     {stats['dead']}")
    print(f"  ---")
    print(f"  Total emails sent: {stats['emails_sent']}")
    print("=" * 50 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="ClearJet Pressure Washing - Automated Outreach System"
    )
    parser.add_argument("--scrape", action="store_true", help="Step 1: Scrape leads from Google Maps")
    parser.add_argument("--find-emails", action="store_true", help="Step 2: Find emails from business websites")
    parser.add_argument("--send", action="store_true", help="Step 3: Send next batch of cold emails")
    parser.add_argument("--check-replies", action="store_true", help="Step 5: Check for replies and pause sequences")
    parser.add_argument("--status", action="store_true", help="Show pipeline stats")
    parser.add_argument("--full", action="store_true", help="Run full pipeline (scrape + find emails + send)")
    parser.add_argument("--browser", action="store_true", help="Use Playwright browser for scraping")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    args = parser.parse_args()

    if not any([args.scrape, args.find_emails, args.send, args.check_replies, args.status, args.full]):
        parser.print_help()
        return

    if args.status:
        show_status()
        return

    if args.scrape or args.full:
        log.info("=== STEP 1: Scraping Google Maps ===")
        # Build the argv for scraper
        scraper_args = ["--batch"]
        if args.browser:
            scraper_args.append("--browser")
        if args.headed:
            scraper_args.append("--headed")
        sys.argv = ["scraper.py"] + scraper_args
        scraper.main()

    if args.find_emails or args.full:
        log.info("=== STEP 2: Finding emails from websites ===")
        found = email_finder.find_emails_for_leads()
        log.info("Found %d new email addresses", found)

    if args.send or args.full:
        if not config.SENDER_PASSWORD:
            log.error("Cannot send emails — set CLEARJET_EMAIL_PASSWORD first")
            log.error("  Windows:   set CLEARJET_EMAIL_PASSWORD=your-app-password")
            log.error("  Mac/Linux: export CLEARJET_EMAIL_PASSWORD=your-app-password")
        else:
            log.info("=== STEP 3: Sending email sequences ===")
            sent = emailer.run_email_sequence()
            log.info("Sent %d emails", sent)

    if args.check_replies:
        if not config.SENDER_PASSWORD:
            log.error("Cannot check replies — set CLEARJET_EMAIL_PASSWORD first")
        else:
            log.info("=== STEP 5: Checking for replies ===")
            replies = reply_monitor.check_for_replies()
            log.info("Detected %d replies", replies)

    show_status()


if __name__ == "__main__":
    main()
