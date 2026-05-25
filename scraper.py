"""
Step 1: Google Maps Lead Scraper (FIXED)
ClearJet Pressure Washing LLC

Fixes from v1:
  - Crash recovery: saves progress after each search, resumes where it left off
  - Deduplication: checks DB before inserting, skips already-scraped zip+category combos
  - Speed: faster scrolling, concurrent-safe, skips empty results quickly

Two modes:
  Default (HTTP) — uses requests, no browser needed
  --browser       — uses Playwright for JS rendering (needs: python -m playwright install chromium)

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
import database

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
    return re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)


def clean_phone(raw: str) -> str:
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return raw.strip() if raw.strip() else ""


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Progress tracker (crash recovery)
# ---------------------------------------------------------------------------

PROGRESS_FILE = os.path.join(config.OUTPUT_DIR, ".scrape_progress.json")


def load_progress() -> set:
    """Load set of completed 'category|zipcode' keys."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_progress(completed: set) -> None:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump(list(completed), f)


# ---------------------------------------------------------------------------
# HTTP scraper
# ---------------------------------------------------------------------------

def search_google_maps_http(category: str, zipcode: str) -> list[Lead]:
    leads: list[Lead] = []
    query = f"{category} near {zipcode}"
    url = f"https://www.google.com/maps/search/{quote_plus(query)}"
    log.info("HTTP scraping: '%s' in %s", category, zipcode)

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        leads = parse_maps_html(resp.text, category, zipcode)

        if not leads:
            params = {"q": f"{category} near {zipcode}", "tbm": "lcl"}
            resp2 = session.get("https://www.google.com/search", params=params, timeout=30)
            resp2.raise_for_status()
            leads = parse_local_search_html(resp2.text, category, zipcode)

    except requests.RequestException as exc:
        log.error("HTTP request failed: %s", exc)

    return leads


def parse_maps_html(html: str, category: str, zipcode: str) -> list[Lead]:
    leads: list[Lead] = []
    seen: set[str] = set()

    json_blocks = re.findall(r'(\[(?:"[^"]*",?\s*){3,}\])', html)
    for block in json_blocks:
        try:
            data = json.loads(block)
            if not isinstance(data, list) or len(data) < 3:
                continue
            strings = [s for s in data if isinstance(s, str) and len(s) > 2]
            if len(strings) < 2:
                continue

            name, address, phone, website = "", "", "", ""
            for s in strings:
                if re.search(r"\b\d{5}\b", s) and re.search(r"\b[A-Z]{2}\b", s):
                    address = s
                elif re.search(r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}", s):
                    phone = clean_phone(s)
                elif re.match(r"https?://", s):
                    website = s
                elif not name and len(s) > 2 and not s.startswith("http"):
                    name = s

            if name and name not in seen:
                seen.add(name)
                leads.append(Lead(name, address, phone, website, "", category, zipcode))
        except (json.JSONDecodeError, TypeError):
            continue

    blocks = re.findall(r'\["([^"]{3,80})"\s*,\s*"([^"]*(?:38\d{3}|TN)[^"]*)"', html)
    for name, addr in blocks:
        if name not in seen and len(name) > 2:
            seen.add(name)
            leads.append(Lead(name, addr, "", "", "", category, zipcode))

    return leads


def parse_local_search_html(html: str, category: str, zipcode: str) -> list[Lead]:
    leads: list[Lead] = []
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()

    for div in soup.find_all("div"):
        text = div.get_text(separator="|", strip=True)
        if not (re.search(r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}", text) or
                re.search(r"\b\d{5}\b", text)):
            continue

        parts = [p.strip() for p in text.split("|") if p.strip()]
        if len(parts) < 2:
            continue

        name, address, phone, website = "", "", "", ""
        for p in parts:
            if re.search(r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}", p) and not phone:
                phone = clean_phone(p)
            elif re.search(r"\b\d{5}\b", p) and re.search(r"\b[A-Z]{2}\b", p) and not address:
                address = p
            elif not name and 2 < len(p) < 80:
                name = p

        if name and name not in seen:
            seen.add(name)
            for a in div.find_all("a", href=True):
                if "google" not in a["href"] and a["href"].startswith("http"):
                    website = a["href"]
                    break
            leads.append(Lead(name, address, phone, website, "", category, zipcode))

    return leads


# ---------------------------------------------------------------------------
# Playwright browser scraper (optional)
# ---------------------------------------------------------------------------

def scrape_with_browser(category: str, zipcode: str, headless: bool = True) -> list[Lead]:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        log.error("Playwright not installed. Run: python -m pip install playwright && python -m playwright install chromium")
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

            try:
                btn = page.query_selector('button:has-text("Accept all")')
                if btn:
                    btn.click()
                    time.sleep(1)
            except Exception:
                pass

            try:
                page.wait_for_selector('a[href*="/maps/place/"]', timeout=15000)
            except PwTimeout:
                log.warning("No results for '%s' in %s", category, zipcode)
                browser.close()
                return leads

            # Scroll
            prev_count, stale = 0, 0
            for _ in range(20):
                page.evaluate("""() => {
                    let f = document.querySelector('[role="feed"]');
                    if (f) { f.scrollTop = f.scrollHeight; return; }
                    let m = document.querySelector('[role="main"]');
                    if (m) for (let d of m.querySelectorAll('div'))
                        if (d.scrollHeight > d.clientHeight + 100) {
                            d.scrollTop = d.scrollHeight; return;
                        }
                }""")
                time.sleep(config.SCROLL_PAUSE_SECONDS)
                count = len(page.query_selector_all('a[href*="/maps/place/"]'))
                if count == prev_count:
                    stale += 1
                    if stale >= 3:
                        break
                else:
                    stale = 0
                    prev_count = count

            # Collect URLs first to avoid stale elements
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

            for listing in listings[:config.SEARCH_RESULTS_LIMIT]:
                try:
                    page.goto(listing["href"], wait_until="domcontentloaded")
                    time.sleep(config.DETAIL_LOAD_DELAY_MS / 1000)

                    info = {"address": "", "phone": "", "website": "", "email": ""}

                    el = page.query_selector('[data-item-id="address"]')
                    if el:
                        info["address"] = el.inner_text().strip().split("\n")[0]

                    el = page.query_selector('[data-item-id^="phone"]')
                    if el:
                        info["phone"] = clean_phone(el.inner_text().strip().split("\n")[0])

                    el = page.query_selector('a[data-item-id="authority"]')
                    if el:
                        info["website"] = el.get_attribute("href") or ""

                    main = page.query_selector('[role="main"]')
                    if main:
                        emails = extract_emails(main.inner_text())
                        info["email"] = emails[0] if emails else ""

                    leads.append(Lead(
                        listing["name"], info["address"], info["phone"],
                        info["website"], info["email"], category, zipcode,
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
# CSV + DB output
# ---------------------------------------------------------------------------

def save_leads_csv(leads: list[Lead], filepath: str) -> None:
    file_exists = os.path.isfile(filepath)
    with open(filepath, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists or os.path.getsize(filepath) == 0:
            writer.writeheader()
        for lead in leads:
            writer.writerow(asdict(lead))
    log.info("Saved %d leads -> %s", len(leads), filepath)


def save_leads_to_db(leads: list[Lead]) -> int:
    """Save leads to SQLite. Returns count of new leads."""
    conn = database.get_db()
    new = database.bulk_upsert_leads(conn, [asdict(l) for l in leads])
    conn.close()
    return new


def deduplicate_leads(leads: list[Lead]) -> list[Lead]:
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
    parser.add_argument("--reset", action="store_true", help="Reset progress tracking (re-scrape everything)")
    args = parser.parse_args()

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    if args.reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        log.info("Progress reset — will re-scrape everything")

    if args.browser:
        scrape_fn = lambda cat, zc: scrape_with_browser(cat, zc, headless=not args.headed)
    else:
        scrape_fn = search_google_maps_http

    all_leads: list[Lead] = []

    if args.batch:
        completed = load_progress()
        total = len(config.BUSINESS_CATEGORIES) * len(config.TARGET_ZIP_CODES)
        done = 0

        for cat in config.BUSINESS_CATEGORIES:
            for zc in config.TARGET_ZIP_CODES:
                done += 1
                key = f"{cat}|{zc}"

                if key in completed:
                    log.info("--- [%d/%d] SKIP (already done): '%s' in %s ---", done, total, cat, zc)
                    continue

                log.info("--- [%d/%d] Scraping: '%s' in %s ---", done, total, cat, zc)

                try:
                    leads = scrape_fn(cat, zc)
                    all_leads.extend(leads)

                    # Save per-search CSV
                    if leads:
                        fname = config.CSV_FILENAME_TEMPLATE.format(
                            category=cat.replace(" ", "_"), zipcode=zc,
                        )
                        save_leads_csv(leads, os.path.join(config.OUTPUT_DIR, fname))
                        new_count = save_leads_to_db(leads)
                        log.info("  -> %d leads (%d new to DB)", len(leads), new_count)

                    # Mark as done so we skip on crash+restart
                    completed.add(key)
                    save_progress(completed)

                except Exception as exc:
                    log.error("  CRASHED on '%s' in %s: %s — continuing to next", cat, zc, exc)
                    continue

                random_delay_between = random.uniform(1, 3)
                time.sleep(random_delay_between)

    elif args.category and args.zipcode:
        leads = scrape_fn(args.category, args.zipcode)
        all_leads.extend(leads)
        if leads:
            fname = config.CSV_FILENAME_TEMPLATE.format(
                category=args.category.replace(" ", "_"), zipcode=args.zipcode,
            )
            save_leads_csv(leads, os.path.join(config.OUTPUT_DIR, fname))
            new_count = save_leads_to_db(leads)
            log.info("%d leads (%d new to DB)", len(leads), new_count)
    else:
        parser.error("Provide --category and --zipcode, or use --batch")

    # Combined deduplicated CSV
    all_leads = deduplicate_leads(all_leads)
    combined = os.path.join(config.OUTPUT_DIR, config.COMBINED_CSV_FILENAME)
    if os.path.exists(combined):
        os.remove(combined)
    if all_leads:
        save_leads_csv(all_leads, combined)

    log.info("Done. Total unique leads this run: %d", len(all_leads))

    # Show DB stats
    try:
        conn = database.get_db()
        stats = database.get_stats(conn)
        conn.close()
        log.info("DB totals: %d leads, %d with email, %d emailed, %d replied",
                 stats["total"], stats["with_email"], stats["emails_sent"], stats["replied"])
    except Exception:
        pass


if __name__ == "__main__":
    main()
