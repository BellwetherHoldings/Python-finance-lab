"""
Step 2: Email Finder
Scrapes business websites to find contact email addresses.

If Google Maps didn't have an email, this module visits the business website
and checks the homepage, /contact, /about pages for email addresses.
"""

import re
import logging
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config
import database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Junk emails to ignore
JUNK_PATTERNS = [
    r".*@example\.com",
    r".*@sentry\.io",
    r".*@.*\.png",
    r".*@.*\.jpg",
    r"noreply@",
    r"no-reply@",
    r".*wixpress\.com",
    r".*squarespace\.com",
    r".*wordpress\.com",
]


def extract_emails(text: str) -> list[str]:
    """Find all email addresses in text, filtering out junk."""
    raw = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    cleaned = []
    for email in raw:
        email = email.lower().strip(".")
        if any(re.match(p, email) for p in JUNK_PATTERNS):
            continue
        cleaned.append(email)
    return list(dict.fromkeys(cleaned))  # dedupe, preserve order


def find_email_on_website(website_url: str) -> str:
    """
    Visit a business website and try to find a contact email.
    Checks: homepage, /contact, /contact-us, /about, /about-us
    Returns the best email found, or empty string.
    """
    if not website_url:
        return ""

    # Normalize URL
    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    session = requests.Session()
    session.headers.update(HEADERS)

    # Pages to check
    base = website_url.rstrip("/")
    pages_to_check = [base] + [base + path for path in config.CONTACT_PAGE_PATHS]

    all_emails: list[str] = []

    for url in pages_to_check[:config.MAX_PAGES_PER_SITE + 1]:
        try:
            resp = session.get(url, timeout=config.WEBSITE_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200:
                continue

            # Check both raw HTML (for mailto: links) and visible text
            html = resp.text

            # mailto: links are highest quality
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                if a["href"].startswith("mailto:"):
                    email = a["href"].replace("mailto:", "").split("?")[0].strip()
                    emails = extract_emails(email)
                    all_emails.extend(emails)

            # Also check page text
            page_text = soup.get_text(separator=" ")
            all_emails.extend(extract_emails(page_text))

        except requests.RequestException:
            continue
        except Exception:
            continue

    if not all_emails:
        return ""

    # Prioritize: info@, contact@, sales@, admin@, then anything
    priority = ["info@", "contact@", "sales@", "admin@", "office@", "service@"]
    for prefix in priority:
        for email in all_emails:
            if email.startswith(prefix):
                return email

    return all_emails[0]


def find_emails_for_leads(db_path: str = None) -> int:
    """
    Look up emails for all leads that have a website but no email.
    Updates the database directly. Returns count of emails found.
    """
    conn = database.get_db(db_path)
    leads = database.get_leads_without_email(conn)
    log.info("Found %d leads without email (have website)", len(leads))

    found = 0
    for i, lead in enumerate(leads):
        website = lead["website"]
        name = lead["business_name"]
        log.info("  [%d/%d] Checking %s (%s)", i + 1, len(leads), name, website)

        email = find_email_on_website(website)
        if email:
            database.update_lead_email(conn, lead["id"], email)
            found += 1
            log.info("    -> Found: %s", email)
        else:
            log.info("    -> No email found")

    conn.close()
    log.info("Found %d emails out of %d websites checked", found, len(leads))
    return found


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Find emails from business websites")
    parser.add_argument("--url", type=str, help="Test a single website URL")
    parser.add_argument("--run", action="store_true", help="Process all leads in database")
    args = parser.parse_args()

    if args.url:
        email = find_email_on_website(args.url)
        print(f"Email found: {email}" if email else "No email found")
    elif args.run:
        find_emails_for_leads()
    else:
        parser.print_help()
