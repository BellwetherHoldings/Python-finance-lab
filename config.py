"""
ClearJet Pressure Washing LLC - Automated Outreach System
Configuration for all modules.
"""
import os

# ---------------------------------------------------------------------------
# Business info
# ---------------------------------------------------------------------------
COMPANY_NAME = "ClearJet Pressure Washing LLC"
SENDER_NAME = "Jakob Tillman"
SENDER_EMAIL = os.environ.get("CLEARJET_EMAIL", "jakobtillman33@gmail.com")
SENDER_PASSWORD = os.environ.get("CLEARJET_EMAIL_PASSWORD", "")  # Gmail App Password

# ---------------------------------------------------------------------------
# Target market
# ---------------------------------------------------------------------------
BUSINESS_CATEGORIES = [
    "property management companies",
    "HOA management",
    "strip malls",
    "commercial plazas",
    "warehouses",
    "car dealerships",
    "restaurant groups",
    "shopping centers",
    "office parks",
    "apartment complexes",
]

TARGET_ZIP_CODES = [
    "38101", "38103", "38104", "38105", "38106", "38107", "38108", "38109",
    "38111", "38112", "38114", "38115", "38116", "38117", "38118", "38119",
    "38120", "38122", "38125", "38126", "38127", "38128", "38131", "38132",
    "38133", "38134", "38135", "38137", "38138", "38139", "38141",
    "38002", "38004", "38011", "38012", "38015", "38016", "38017", "38018",
    "38019", "38023", "38024", "38028", "38053", "38057", "38058", "38060",
    "38066", "38068", "38075", "38076", "38301", "38305",
]

# ---------------------------------------------------------------------------
# Scraper settings
# ---------------------------------------------------------------------------
SEARCH_RESULTS_LIMIT = 40
SCROLL_PAUSE_SECONDS = 1.5
PAGE_LOAD_TIMEOUT_MS = 45000
DETAIL_LOAD_DELAY_MS = 2000

# ---------------------------------------------------------------------------
# Email finder settings
# ---------------------------------------------------------------------------
WEBSITE_TIMEOUT = 10           # Seconds to wait for a business website
MAX_PAGES_PER_SITE = 3         # How many pages to check per website (home, contact, about)
CONTACT_PAGE_PATHS = ["/contact", "/contact-us", "/about", "/about-us"]

# ---------------------------------------------------------------------------
# Email sequence settings
# ---------------------------------------------------------------------------
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993

# Delays between touches (in days)
SEQUENCE_DELAYS = [0, 3, 7, 14, 21]  # Touch 1 immediately, Touch 2 at day 3, etc.
DAILY_SEND_LIMIT = 50                # Gmail daily limit safety margin
DELAY_BETWEEN_EMAILS_SEC = 30        # Seconds between individual sends

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = "clearjet_leads.db"

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
OUTPUT_DIR = "output"
CSV_FILENAME_TEMPLATE = "leads_{category}_{zipcode}.csv"
COMBINED_CSV_FILENAME = "all_leads.csv"
