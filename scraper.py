"""
Google Maps Lead Scraper - Step 1
ClearJet Pressure Washing LLC

Scrapes business listings from Google Maps by category + zip code.
Extracts: business name, address, phone, website, email.

Two modes:
  1. Default (HTTP) - Uses requests + regex parsing. No browser needed.
  2. --browser mode  - Uses Playwright for JS-rendered pages (requires: playwright install chromium)

Usage:
    python scraper.py --category "car dealerships" --zipcode 38117
    python scraper.py --batch
    python scraper.py --category "car dealerships" --zipcode 38117 --browser --headed
"""

import argparse
import csv
import json
import os
import re
import time
import random
import logging
from dataclasses import dataclass, fields, asdict
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Lead:
    """Single business lead scraped from Google Maps."""
    business_name: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    email: str = ""
    category: str = ""
    zipcode: str = ""


CSV_FIELDS = [f.name for f in fields(Lead)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_emails(text: str) -> list[str]:
    """Find all email addresses in text."""
    return re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)


def clean_phone(raw: str) -> str:
    """Extract a clean phone number."""
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return raw.strip() if raw.strip() else ""


def random_delay(lo: float = 1.0, hi: float = 3.0) -> None:
    """Sleep a random amount to be polite."""
    time.sleep(random.uniform(lo, hi))


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# HTTP-based scraper (no browser needed)
# ---------------------------------------------------------------------------

def search_google_maps_http(category: str, zipcode: str) -> list[Lead]:
    """
    Search Google Maps via HTTP and parse the embedded JSON/text data.
    Google Maps search results page embeds business data in the HTML even
    without JavaScript rendering — we extract it with regex patterns.
    """
    leads: list[Lead] = []
    query = f"{category} near {zipcode}"
    url = f"https://www.google.com/maps/search/{quote_plus(query)}"

    log.info("HTTP scraping: '%s' in %s", category, zipcode)

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        html = resp.text

        # Google Maps embeds data in JS arrays. Look for the pattern that
        # contains business listing data. The key marker is arrays starting
        # with [null,null,... that contain business info.

        # Strategy 1: Extract from window.APP_INITIALIZATION_STATE or
        # the embedded JS data blobs
        leads = parse_maps_html(html, category, zipcode)

        if not leads:
            # Strategy 2: Try the search page with a different endpoint
            search_url = "https://www.google.com/search"
            params = {
                "q": f"{category} near {zipcode}",
                "tbm": "lcl",  # local results
            }
            resp2 = session.get(search_url, params=params, timeout=30)
            resp2.raise_for_status()
            leads = parse_local_search_html(resp2.text, category, zipcode)

    except requests.RequestException as exc:
        log.error("HTTP request failed: %s", exc)

    return leads


def parse_maps_html(html: str, category: str, zipcode: str) -> list[Lead]:
    """
    Parse business data embedded in Google Maps HTML.
    The data lives in large JS arrays within script tags.
    """
    leads: list[Lead] = []

    # Google embeds business data in arrays that look like:
    # [null,"Business Name",null,[null,null,lat,lng],"address",...,"phone",...,"website"]
    # We look for patterns with phone numbers + addresses near business names.

    # Find all quoted strings that look like business names near addresses
    # Pattern: blocks containing TN zip codes (our target area)
    blocks = re.findall(
        r'\["([^"]{3,80})"\s*,\s*"([^"]*(?:38\d{3}|TN)[^"]*)"',
        html,
    )

    # Also try to find structured data blocks
    # Google Maps puts data in arrays like: ["name","address",null,null,"phone"]
    json_blocks = re.findall(r'(\[(?:"[^"]*",?\s*){3,}\])', html)

    seen: set[str] = set()

    for block in json_blocks:
        try:
            data = json.loads(block)
            if not isinstance(data, list) or len(data) < 3:
                continue

            # Look for arrays where items look like name, address, phone
            strings = [s for s in data if isinstance(s, str) and len(s) > 2]
            if len(strings) < 2:
                continue

            name = ""
            address = ""
            phone = ""
            website = ""

            for s in strings:
                # Detect address (has zip code or state)
                if re.search(r"\b\d{5}\b", s) and re.search(r"\b[A-Z]{2}\b", s):
                    address = s
                # Detect phone
                elif re.search(r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}", s):
                    phone = clean_phone(s)
                # Detect URL
                elif re.match(r"https?://", s):
                    website = s
                # Otherwise could be business name
                elif not name and len(s) > 2 and not s.startswith("http"):
                    name = s

            if name and name not in seen:
                seen.add(name)
                leads.append(Lead(
                    business_name=name,
                    address=address,
                    phone=phone,
                    website=website,
                    email="",
                    category=category,
                    zipcode=zipcode,
                ))
        except (json.JSONDecodeError, TypeError):
            continue

    # Also extract from the simpler pattern matches
    for name, addr in blocks:
        if name not in seen and len(name) > 2:
            seen.add(name)
            leads.append(Lead(
                business_name=name,
                address=addr,
                phone="",
                website="",
                email="",
                category=category,
                zipcode=zipcode,
            ))

    return leads


def parse_local_search_html(html: str, category: str, zipcode: str) -> list[Lead]:
    """
    Parse Google local search results (tbm=lcl).
    These have a simpler structure than Maps.
    """
    leads: list[Lead] = []
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()

    # Local results are in div blocks with business info
    # Look for common patterns in local pack results
    for div in soup.find_all("div"):
        text = div.get_text(separator="|", strip=True)

        # Must contain a phone-like pattern or address-like pattern to be a listing
        has_phone = re.search(r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}", text)
        has_address = re.search(r"\b\d{5}\b", text)

        if not (has_phone or has_address):
            continue

        parts = [p.strip() for p in text.split("|") if p.strip()]
        if len(parts) < 2:
            continue

        name = ""
        address = ""
        phone = ""
        website = ""

        for p in parts:
            if re.search(r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}", p) and not phone:
                phone = clean_phone(p)
            elif re.search(r"\b\d{5}\b", p) and re.search(r"\b[A-Z]{2}\b", p) and not address:
                address = p
            elif not name and len(p) > 2 and len(p) < 80:
                name = p

        if name and name not in seen:
            seen.add(name)

            # Try to find website links
            for a_tag in div.find_all("a", href=True):
                href = a_tag["href"]
                if "google" not in href and href.startswith("http"):
                    website = href
                    break

            leads.append(Lead(
                business_name=name,
                address=address,
                phone=phone,
                website=website,
                email="",
                category=category,
                zipcode=zipcode,
            ))

    return leads


# ---------------------------------------------------------------------------
# Playwright-based scraper (optional, for when HTTP isn't enough)
# ---------------------------------------------------------------------------

def scrape_with_browser(category: str, zipcode: str, headless: bool = True) -> list[Lead]:
    """
    Full browser scrape using Playwright. More reliable but requires:
        pip install playwright
        playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    leads: list[Lead] = []
    query = f"{category} near {zipcode}"
    url = f"https://www.google.com/maps/search/{quote_plus(query)}"
    log.info("Browser scraping: '%s' in %s", category, zipcode)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            user_agent=HEADERS["User-Agent"],
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()
        page.set_default_timeout(config.PAGE_LOAD_TIMEOUT_MS)

        try:
            page.goto(url, wait_until="networkidle")

            # Handle consent banner
            try:
                btn = page.query_selector('button:has-text("Accept all")')
                if btn:
                    btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # Wait for results
            try:
                page.wait_for_selector('a[href*="/maps/place/"]', timeout=15000)
            except PwTimeout:
                log.warning("No results for '%s' in %s", category, zipcode)
                browser.close()
                return leads

            # Scroll results
            prev_count = 0
            stale = 0
            for _ in range(20):
                page.evaluate("""
                    () => {
                        let f = document.querySelector('[role="feed"]');
                        if (f) { f.scrollTop = f.scrollHeight; return; }
                        let m = document.querySelector('[role="main"]');
                        if (m) for (let d of m.querySelectorAll('div'))
                            if (d.scrollHeight > d.clientHeight + 100) {
                                d.scrollTop = d.scrollHeight; return;
                            }
                    }
                """)
                time.sleep(config.SCROLL_PAUSE_SECONDS)
                count = len(page.query_selector_all('a[href*="/maps/place/"]'))
                if count == prev_count:
                    stale += 1
                    if stale >= 3:
                        break
                else:
                    stale = 0
                    prev_count = count

            # Collect listing URLs first (avoids stale elements)
            els = page.query_selector_all('a[href*="/maps/place/"]')
            seen: set[str] = set()
            listings: list[dict] = []
            for el in els:
                name = (el.get_attribute("aria-label") or "").strip()
                href = (el.get_attribute("href") or "").strip()
                if name and name not in seen:
                    seen.add(name)
                    listings.append({"name": name, "href": href})

            log.info("Found %d unique listings", len(listings))

            # Visit each listing
            for listing in listings[:config.SEARCH_RESULTS_LIMIT]:
                try:
                    page.goto(listing["href"], wait_until="domcontentloaded")
                    time.sleep(2)

                    info = {"address": "", "phone": "", "website": "", "email": ""}

                    # Address
                    el = page.query_selector('[data-item-id="address"]')
                    if el:
                        info["address"] = el.inner_text().strip().split("\n")[0]

                    # Phone
                    el = page.query_selector('[data-item-id^="phone"]')
                    if el:
                        info["phone"] = clean_phone(el.inner_text().strip().split("\n")[0])

                    # Website
                    el = page.query_selector('a[data-item-id="authority"]')
                    if el:
                        info["website"] = el.get_attribute("href") or ""

                    # Email from page text
                    main = page.query_selector('[role="main"]')
                    if main:
                        emails = extract_emails(main.inner_text())
                        info["email"] = emails[0] if emails else ""

                    leads.append(Lead(
                        business_name=listing["name"],
                        address=info["address"],
                        phone=info["phone"],
                        website=info["website"],
                        email=info["email"],
                        category=category,
                        zipcode=zipcode,
                    ))
                    log.info("  [%d] %s", len(leads), listing["name"])

                except Exception as exc:
                    log.warning("  Skip '%s': %s", listing["name"], exc)

        except Exception as exc:
            log.error("Browser error: %s", exc)
        finally:
            browser.close()

    return leads


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def save_leads_csv(leads: list[Lead], filepath: str) -> None:
    """Write leads to CSV. Appends if file exists."""
    file_exists = os.path.isfile(filepath)
    with open(filepath, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists or os.path.getsize(filepath) == 0:
            writer.writeheader()
        for lead in leads:
            writer.writerow(asdict(lead))
    log.info("Saved %d leads -> %s", len(leads), filepath)


def deduplicate_leads(leads: list[Lead]) -> list[Lead]:
    """Remove dupes by business name + address."""
    seen: set[str] = set()
    unique: list[Lead] = []
    for lead in leads:
        key = f"{lead.business_name.lower().strip()}|{lead.address.lower().strip()}"
        if key not in seen:
            seen.add(key)
            unique.append(lead)
    return unique


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Google Maps Lead Scraper - ClearJet Pressure Washing LLC"
    )
    parser.add_argument("--category", type=str, help="Business category to search")
    parser.add_argument("--zipcode", type=str, help="Zip code to search in")
    parser.add_argument("--batch", action="store_true", help="Run all combos from config")
    parser.add_argument("--browser", action="store_true", help="Use Playwright browser mode")
    parser.add_argument("--headed", action="store_true", help="Show browser (only with --browser)")
    args = parser.parse_args()

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    # Pick scrape function
    if args.browser:
        scrape_fn = lambda cat, zc: scrape_with_browser(cat, zc, headless=not args.headed)
    else:
        scrape_fn = search_google_maps_http

    all_leads: list[Lead] = []

    if args.batch:
        total = len(config.BUSINESS_CATEGORIES) * len(config.TARGET_ZIP_CODES)
        done = 0
        for cat in config.BUSINESS_CATEGORIES:
            for zc in config.TARGET_ZIP_CODES:
                done += 1
                log.info("--- Job %d/%d ---", done, total)
                leads = scrape_fn(cat, zc)
                all_leads.extend(leads)
                fname = config.CSV_FILENAME_TEMPLATE.format(
                    category=cat.replace(" ", "_"), zipcode=zc,
                )
                if leads:
                    save_leads_csv(leads, os.path.join(config.OUTPUT_DIR, fname))
                random_delay(2, 5)  # Don't hammer Google

    elif args.category and args.zipcode:
        leads = scrape_fn(args.category, args.zipcode)
        all_leads.extend(leads)
        fname = config.CSV_FILENAME_TEMPLATE.format(
            category=args.category.replace(" ", "_"), zipcode=args.zipcode,
        )
        if leads:
            save_leads_csv(leads, os.path.join(config.OUTPUT_DIR, fname))

    else:
        parser.error("Provide --category and --zipcode, or use --batch")

    # Combined deduplicated output
    all_leads = deduplicate_leads(all_leads)
    combined = os.path.join(config.OUTPUT_DIR, config.COMBINED_CSV_FILENAME)
    if os.path.exists(combined):
        os.remove(combined)
    if all_leads:
        save_leads_csv(all_leads, combined)

    log.info("Done. Total unique leads: %d", len(all_leads))


if __name__ == "__main__":
    main()
