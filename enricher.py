"""
Email Enricher — ClearJet Pressure Washing LLC

Takes a CSV of leads (from scraper.py output) that have a 'website' field,
visits each site's homepage and /contact page, and extracts email addresses.

Usage:
    python enricher.py --input output/all_leads.csv --output output/enriched_leads.csv
    python enricher.py --input output/all_leads.csv   # overwrites input in place
"""

import argparse
import csv
import logging
import re
import time
import random
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

CONTACT_PATHS = ["/contact", "/contact-us", "/about", "/about-us"]

# Domains that show up in page text but are never real contact emails
SPAM_DOMAINS = {
    "example.com", "sentry.io", "domain.com", "yoursite.com",
    "email.com", "wixpress.com", "squarespace.com",
}


def _extract_emails(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)


def _is_valid(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    if domain in SPAM_DOMAINS:
        return False
    # Filter out image/asset false positives (e.g. bg@2x.png)
    if any(email.lower().endswith(ext) for ext in (".png", ".jpg", ".gif", ".svg", ".webp")):
        return False
    return True


def _fetch(session: requests.Session, url: str) -> str | None:
    try:
        resp = session.get(url, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except requests.RequestException:
        pass
    return None


def _scrape_emails_from_html(html: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: set[str] = set()

    # Prioritise mailto: links — highest signal
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            email = href[7:].split("?")[0].strip().lower()
            if email and _is_valid(email):
                found.add(email)

    # Fall back to regex over visible text
    for email in _extract_emails(soup.get_text()):
        if _is_valid(email):
            found.add(email.lower())

    return found


def find_email_for_site(website: str) -> str:
    """Visit a business website and return the best contact email found, or ''."""
    if not website or not website.startswith("http"):
        return ""

    parsed = urlparse(website)
    base = f"{parsed.scheme}://{parsed.netloc}"

    session = requests.Session()
    session.headers.update(HEADERS)

    # Homepage first
    html = _fetch(session, website)
    if html:
        emails = _scrape_emails_from_html(html)
        if emails:
            return sorted(emails)[0]

    # Try common contact/about pages
    for path in CONTACT_PATHS:
        time.sleep(random.uniform(0.5, 1.5))
        html = _fetch(session, urljoin(base, path))
        if not html:
            continue
        emails = _scrape_emails_from_html(html)
        if emails:
            return sorted(emails)[0]

    return ""


def enrich_csv(input_path: str, output_path: str) -> None:
    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "email" not in fieldnames:
        fieldnames.append("email")

    total = len(rows)
    enriched = 0

    for i, row in enumerate(rows, 1):
        if row.get("email"):
            log.info("[%d/%d] Skip (already has email): %s", i, total, row.get("business_name"))
            continue

        website = row.get("website", "").strip()
        if not website:
            log.info("[%d/%d] Skip (no website): %s", i, total, row.get("business_name"))
            continue

        log.info("[%d/%d] Checking: %s  →  %s", i, total, row.get("business_name"), website)
        email = find_email_for_site(website)
        row["email"] = email

        if email:
            enriched += 1
            log.info("    ✓ %s", email)
        else:
            log.info("    – no email found")

        time.sleep(random.uniform(1.0, 2.5))

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    log.info(
        "Done. %d/%d leads enriched with email. Output: %s",
        enriched, total, output_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Email Enricher — ClearJet Lead Pipeline")
    parser.add_argument("--input", required=True, help="Input CSV (from scraper.py)")
    parser.add_argument("--output", help="Output path (default: overwrite input)")
    args = parser.parse_args()
    enrich_csv(args.input, args.output or args.input)


if __name__ == "__main__":
    main()
