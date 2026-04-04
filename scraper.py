"""
Google Maps Lead Scraper - Step 1
ClearJet Pressure Washing LLC

Scrapes business listings from Google Maps by category + zip code.
Extracts: business name, address, phone, website, email.

Strategy: Instead of relying on brittle CSS class selectors, this scraper
uses aria-labels, data attributes, and text content patterns that survive
Google's frequent UI reshuffles.

Usage:
    python scraper.py --category "property management companies" --zipcode 38117
    python scraper.py --batch
"""

import argparse
import csv
import json
import os
import re
import time
import logging
from dataclasses import dataclass, fields, asdict
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_email_from_text(text: str) -> str:
    """Find the first email address in a block of text."""
    match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    return match.group(0) if match else ""


def clean_phone(raw: str) -> str:
    """Normalize a phone string."""
    digits = re.sub(r"[^\d+]", "", raw)
    return digits if len(digits) >= 10 else ""


def build_search_url(category: str, zipcode: str) -> str:
    query = f"{category} near {zipcode}"
    return f"https://www.google.com/maps/search/{quote_plus(query)}"


# ---------------------------------------------------------------------------
# Scroll the results feed
# ---------------------------------------------------------------------------

def scroll_results(page, max_scrolls: int = 20) -> None:
    """
    Scroll the results panel. Uses JS to find the scrollable container
    by looking for role="feed" or the first scrollable child of role="main".
    """
    js_scroll = """
    () => {
        // Try role="feed" first (most common)
        let feed = document.querySelector('[role="feed"]');
        if (feed) { feed.scrollTop = feed.scrollHeight; return true; }
        // Fallback: find scrollable divs inside role="main"
        let main = document.querySelector('[role="main"]');
        if (!main) return false;
        let divs = main.querySelectorAll('div');
        for (let d of divs) {
            if (d.scrollHeight > d.clientHeight + 100 && d.clientHeight > 200) {
                d.scrollTop = d.scrollHeight;
                return true;
            }
        }
        return false;
    }
    """
    prev_count = 0
    stale_rounds = 0

    for i in range(max_scrolls):
        try:
            page.evaluate(js_scroll)
        except Exception:
            pass
        time.sleep(config.SCROLL_PAUSE_SECONDS)

        # Count current listings to detect when we stop getting new ones
        links = page.query_selector_all('a[href*="/maps/place/"]')
        if len(links) == prev_count:
            stale_rounds += 1
            if stale_rounds >= 3:
                log.info("No new results after %d scrolls, stopping", i + 1)
                return
        else:
            stale_rounds = 0
            prev_count = len(links)

    log.info("Finished scrolling (%d scrolls, %d listings)", max_scrolls, prev_count)


# ---------------------------------------------------------------------------
# Extract detail from a listing page
# ---------------------------------------------------------------------------

def extract_detail_from_page(page) -> dict:
    """
    Pull business details from the listing detail panel.
    Uses multiple selector strategies per field for resilience.
    """
    info = {"address": "", "phone": "", "website": "", "email": ""}

    # Give the detail panel a moment to render
    time.sleep(1.5)

    # --- ADDRESS ---
    # Strategy 1: button with data-item-id="address"
    addr = page.query_selector('[data-item-id="address"]')
    if addr:
        info["address"] = addr.inner_text().strip().split("\n")[0]
    else:
        # Strategy 2: aria-label containing "Address:"
        addr2 = page.query_selector('[aria-label*="Address"]')
        if addr2:
            label = addr2.get_attribute("aria-label") or ""
            info["address"] = label.replace("Address:", "").strip()

    # --- PHONE ---
    # Strategy 1: data-item-id starting with "phone"
    phone = page.query_selector('[data-item-id^="phone"]')
    if phone:
        raw = phone.inner_text().strip().split("\n")[0]
        info["phone"] = clean_phone(raw) or raw
    else:
        # Strategy 2: aria-label containing "Phone:"
        phone2 = page.query_selector('[aria-label*="Phone"]')
        if phone2:
            label = phone2.get_attribute("aria-label") or ""
            info["phone"] = clean_phone(label)

    # --- WEBSITE ---
    # Strategy 1: data-item-id="authority"
    web = page.query_selector('a[data-item-id="authority"]')
    if web:
        info["website"] = web.get_attribute("href") or ""
    else:
        # Strategy 2: aria-label containing "website" (case insensitive via XPath)
        try:
            web2 = page.query_selector('a[aria-label*="ebsite"]')
            if web2:
                info["website"] = web2.get_attribute("href") or ""
        except Exception:
            pass

    # --- EMAIL ---
    # Emails are rare on Maps but check the full page text
    try:
        main = page.query_selector('[role="main"]')
        if main:
            text = main.inner_text()
            info["email"] = extract_email_from_text(text)
    except Exception:
        pass

    return info


# ---------------------------------------------------------------------------
# Core scrape loop
# ---------------------------------------------------------------------------

def scrape_category_zip(
    category: str,
    zipcode: str,
    headless: bool = True,
) -> list[Lead]:
    """
    Scrape Google Maps for one category + zip code.
    Returns a list of Lead dataclass objects.
    """
    leads: list[Lead] = []
    url = build_search_url(category, zipcode)
    log.info("Scraping: '%s' in %s", category, zipcode)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        # Hide webdriver flag
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()
        page.set_default_timeout(config.PAGE_LOAD_TIMEOUT_MS)

        try:
            page.goto(url, wait_until="networkidle")

            # Handle consent / cookie banner if it appears
            try:
                accept_btn = page.query_selector(
                    'button:has-text("Accept all"), '
                    'form[action*="consent"] button'
                )
                if accept_btn:
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # Wait for results to appear
            try:
                page.wait_for_selector(
                    'a[href*="/maps/place/"]', timeout=15000
                )
            except PlaywrightTimeout:
                log.warning("No results found for '%s' in %s", category, zipcode)
                browser.close()
                return leads

            # Scroll to load all results
            scroll_results(page)

            # Gather all listing links
            listing_els = page.query_selector_all('a[href*="/maps/place/"]')
            log.info("Found %d raw listing links", len(listing_els))

            # Dedupe and collect (name, href) pairs first so DOM detach won't bite us
            seen: set[str] = set()
            listings: list[dict] = []
            for el in listing_els:
                name = (el.get_attribute("aria-label") or "").strip()
                href = (el.get_attribute("href") or "").strip()
                if name and name not in seen and "/maps/place/" in href:
                    seen.add(name)
                    listings.append({"name": name, "href": href})

            log.info("Unique listings to process: %d", len(listings))

            for idx, listing in enumerate(listings):
                if len(leads) >= config.SEARCH_RESULTS_LIMIT:
                    break

                name = listing["name"]
                href = listing["href"]

                try:
                    # Navigate directly to the listing URL instead of clicking
                    # This avoids stale element issues entirely
                    page.goto(href, wait_until="domcontentloaded")
                    time.sleep(config.DETAIL_LOAD_DELAY_MS / 1000)

                    detail = extract_detail_from_page(page)

                    lead = Lead(
                        business_name=name,
                        address=detail["address"],
                        phone=detail["phone"],
                        website=detail["website"],
                        email=detail["email"],
                        category=category,
                        zipcode=zipcode,
                    )
                    leads.append(lead)
                    log.info(
                        "  [%d/%d] %s | %s | %s",
                        len(leads), len(listings),
                        name,
                        detail["phone"] or "no phone",
                        detail["website"] or "no website",
                    )

                except (PlaywrightTimeout, Exception) as exc:
                    log.warning("  Skipping '%s': %s", name, exc)
                    continue

            # After processing all listings, go back for a clean state
            # (useful if this function is called in a loop)

        except PlaywrightTimeout:
            log.error("Page timed out for '%s' in %s", category, zipcode)
        except Exception as exc:
            log.error("Unexpected error: %s", exc)
        finally:
            browser.close()

    log.info("Collected %d leads for '%s' in %s", len(leads), category, zipcode)
    return leads


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

CSV_FIELDS = [f.name for f in fields(Lead)]


def save_leads_csv(leads: list[Lead], filepath: str) -> None:
    """Write leads to a CSV file. Appends if file exists."""
    file_exists = os.path.isfile(filepath)
    with open(filepath, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists or os.path.getsize(filepath) == 0:
            writer.writeheader()
        for lead in leads:
            writer.writerow(asdict(lead))
    log.info("Saved %d leads to %s", len(leads), filepath)


def deduplicate_leads(leads: list[Lead]) -> list[Lead]:
    """Remove duplicates by business name + address."""
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
    parser.add_argument(
        "--batch", action="store_true",
        help="Run all category x zip combos from config.py",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Show the browser window (useful for debugging)",
    )
    args = parser.parse_args()

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    headless = not args.headed

    all_leads: list[Lead] = []

    if args.batch:
        for cat in config.BUSINESS_CATEGORIES:
            for zc in config.TARGET_ZIP_CODES:
                leads = scrape_category_zip(cat, zc, headless=headless)
                all_leads.extend(leads)
                fname = config.CSV_FILENAME_TEMPLATE.format(
                    category=cat.replace(" ", "_"), zipcode=zc,
                )
                save_leads_csv(leads, os.path.join(config.OUTPUT_DIR, fname))

    elif args.category and args.zipcode:
        leads = scrape_category_zip(args.category, args.zipcode, headless=headless)
        all_leads.extend(leads)
        fname = config.CSV_FILENAME_TEMPLATE.format(
            category=args.category.replace(" ", "_"), zipcode=args.zipcode,
        )
        save_leads_csv(leads, os.path.join(config.OUTPUT_DIR, fname))

    else:
        parser.error("Provide --category and --zipcode, or use --batch")

    # Combined deduplicated output
    all_leads = deduplicate_leads(all_leads)
    combined_path = os.path.join(config.OUTPUT_DIR, config.COMBINED_CSV_FILENAME)
    if os.path.exists(combined_path):
        os.remove(combined_path)
    save_leads_csv(all_leads, combined_path)

    log.info("Done. Total unique leads: %d", len(all_leads))


if __name__ == "__main__":
    main()
