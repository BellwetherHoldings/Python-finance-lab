"""
Google Maps Lead Scraper - Step 1
ClearJet Pressure Washing LLC

Scrapes business listings from Google Maps by category + zip code.
Extracts: business name, address, phone, website, email.

Usage:
    python scraper.py --category "property management companies" --zipcode 38117
    python scraper.py --batch  (runs all categories x zip codes from config)
"""

import argparse
import csv
import os
import re
import time
import logging
from dataclasses import dataclass, fields, asdict
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
# Parsing helpers
# ---------------------------------------------------------------------------

def extract_email_from_text(text: str) -> str:
    """Find the first email address in a block of text."""
    match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    return match.group(0) if match else ""


def build_search_url(category: str, zipcode: str) -> str:
    """Build a Google Maps search URL."""
    query = f"{category} near {zipcode}"
    return f"https://www.google.com/maps/search/{query.replace(' ', '+')}"


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------

def scroll_results_panel(page, max_scrolls: int = 15) -> None:
    """Scroll the left-side results panel to load more listings."""
    panel_selector = 'div[role="feed"]'
    for i in range(max_scrolls):
        try:
            page.evaluate(
                f'document.querySelector(\'{panel_selector}\').scrollBy(0, 1000)'
            )
            time.sleep(config.SCROLL_PAUSE_SECONDS)
            # Check for "end of results" indicator
            end_marker = page.query_selector("span.HlvSq")
            if end_marker:
                log.info("Reached end of results after %d scrolls", i + 1)
                return
        except Exception:
            break
    log.info("Finished scrolling (%d scrolls)", max_scrolls)


def scrape_listing_detail(page) -> dict:
    """Extract details from the currently-open listing detail panel."""
    info = {"address": "", "phone": "", "website": "", "email": ""}

    # Address - look for data-item-id starting with "address"
    addr_el = page.query_selector('[data-item-id="address"] .fontBodyMedium')
    if addr_el:
        info["address"] = addr_el.inner_text().strip()

    # Phone - data-item-id containing "phone:tel:"
    phone_el = page.query_selector('[data-item-id^="phone:tel:"] .fontBodyMedium')
    if phone_el:
        info["phone"] = phone_el.inner_text().strip()

    # Website
    website_el = page.query_selector('a[data-item-id="authority"]')
    if website_el:
        href = website_el.get_attribute("href") or ""
        info["website"] = href

    # Email - sometimes visible in the info panel text
    panel_text = page.query_selector('[role="main"]')
    if panel_text:
        info["email"] = extract_email_from_text(panel_text.inner_text())

    return info


def scrape_category_zip(
    category: str,
    zipcode: str,
    headless: bool = True,
) -> list[Lead]:
    """
    Scrape Google Maps for a single category + zip code combination.
    Returns a list of Lead objects.
    """
    leads: list[Lead] = []
    url = build_search_url(category, zipcode)
    log.info("Scraping: %s in %s", category, zipcode)
    log.info("URL: %s", url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.set_default_timeout(config.PAGE_LOAD_TIMEOUT_MS)

        try:
            page.goto(url, wait_until="domcontentloaded")
            time.sleep(3)  # Let initial results render

            # Scroll to load more results
            scroll_results_panel(page)

            # Collect all listing links from the results panel
            listing_links = page.query_selector_all('a[href*="/maps/place/"]')
            log.info("Found %d listing links", len(listing_links))

            seen_names: set[str] = set()

            for i, link in enumerate(listing_links):
                if len(leads) >= config.SEARCH_RESULTS_LIMIT:
                    break

                try:
                    # Get business name from aria-label
                    name = link.get_attribute("aria-label") or ""
                    if not name or name in seen_names:
                        continue
                    seen_names.add(name)

                    # Click to open detail panel
                    link.click()
                    time.sleep(config.DETAIL_LOAD_DELAY_MS / 1000)

                    detail = scrape_listing_detail(page)

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
                        "  [%d] %s | %s | %s",
                        len(leads), name, detail["phone"], detail["website"],
                    )

                    # Navigate back to results list
                    back_btn = page.query_selector('button[aria-label="Back"]')
                    if back_btn:
                        back_btn.click()
                        time.sleep(1)

                except (PlaywrightTimeout, Exception) as exc:
                    log.warning("  Skipping listing %d: %s", i, exc)
                    # Try to get back to results
                    try:
                        page.goto(url, wait_until="domcontentloaded")
                        time.sleep(3)
                        scroll_results_panel(page, max_scrolls=5)
                    except Exception:
                        pass
                    continue

        except PlaywrightTimeout:
            log.error("Page load timed out for %s in %s", category, zipcode)
        finally:
            browser.close()

    log.info("Collected %d leads for '%s' in %s", len(leads), category, zipcode)
    return leads


# ---------------------------------------------------------------------------
# CSV output
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
        key = f"{lead.business_name.lower()}|{lead.address.lower()}"
        if key not in seen:
            seen.add(key)
            unique.append(lead)
    return unique


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Google Maps Lead Scraper - ClearJet Pressure Washing LLC"
    )
    parser.add_argument("--category", type=str, help="Business category to search")
    parser.add_argument("--zipcode", type=str, help="Zip code to search in")
    parser.add_argument(
        "--batch", action="store_true",
        help="Run all category x zip code combos from config.py",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Run browser in headed mode (visible) for debugging",
    )
    args = parser.parse_args()

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    headless = not args.headed

    all_leads: list[Lead] = []

    if args.batch:
        # Run every category x zip code combination
        for cat in config.BUSINESS_CATEGORIES:
            for zc in config.TARGET_ZIP_CODES:
                leads = scrape_category_zip(cat, zc, headless=headless)
                all_leads.extend(leads)
                # Save per-search CSV
                fname = config.CSV_FILENAME_TEMPLATE.format(
                    category=cat.replace(" ", "_"),
                    zipcode=zc,
                )
                save_leads_csv(leads, os.path.join(config.OUTPUT_DIR, fname))
    elif args.category and args.zipcode:
        leads = scrape_category_zip(args.category, args.zipcode, headless=headless)
        all_leads.extend(leads)
        fname = config.CSV_FILENAME_TEMPLATE.format(
            category=args.category.replace(" ", "_"),
            zipcode=args.zipcode,
        )
        save_leads_csv(leads, os.path.join(config.OUTPUT_DIR, fname))
    else:
        parser.error("Provide --category and --zipcode, or use --batch")

    # Save combined, deduplicated CSV
    all_leads = deduplicate_leads(all_leads)
    combined_path = os.path.join(config.OUTPUT_DIR, config.COMBINED_CSV_FILENAME)
    # Overwrite combined file (not append)
    if os.path.exists(combined_path):
        os.remove(combined_path)
    save_leads_csv(all_leads, combined_path)

    log.info("Done. Total unique leads: %d", len(all_leads))


if __name__ == "__main__":
    main()
