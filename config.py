"""
Configuration for ClearJet Pressure Washing LLC - Lead Scraper
Target market: Commercial clients in Memphis & West Tennessee
"""

# Default business categories to scrape
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

# Memphis & West Tennessee zip codes
TARGET_ZIP_CODES = [
    "38101", "38103", "38104", "38105", "38106", "38107", "38108", "38109",
    "38111", "38112", "38114", "38115", "38116", "38117", "38118", "38119",
    "38120", "38122", "38125", "38126", "38127", "38128", "38131", "38132",
    "38133", "38134", "38135", "38137", "38138", "38139", "38141",
    # Surrounding West TN
    "38002", "38004", "38011", "38012", "38015", "38016", "38017", "38018",
    "38019", "38023", "38024", "38028", "38053", "38057", "38058", "38060",
    "38066", "38068", "38075", "38076", "38301", "38305",  # Jackson, TN
]

# Scraper settings
SEARCH_RESULTS_LIMIT = 40        # Max results to collect per search
SCROLL_PAUSE_SECONDS = 2.0       # Pause between scrolls in results list
PAGE_LOAD_TIMEOUT_MS = 60000     # Timeout for page loads
DETAIL_LOAD_DELAY_MS = 3000      # Wait time after clicking a listing

# Output
OUTPUT_DIR = "output"
CSV_FILENAME_TEMPLATE = "leads_{category}_{zipcode}.csv"
COMBINED_CSV_FILENAME = "all_leads.csv"
