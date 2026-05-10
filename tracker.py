"""
Outreach Tracker — ClearJet Pressure Washing LLC

SQLite-backed store for the full email outreach pipeline.
Import this as a module from outreach.py (not meant to be run directly).

Schema
------
leads        — one row per business contact
outreach_log — one row per email step sent to a lead
"""

import csv
import logging
import math
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

DB_PATH = "outreach.db"

# Collierville, TN — centre of the 250-mile service radius
ORIGIN_LAT = 35.0423
ORIGIN_LON = -89.6645
MAX_RADIUS_MILES = 250

log = logging.getLogger(__name__)

FOLLOW_UP_1_DAYS = 3   # days after initial before first follow-up
FOLLOW_UP_2_DAYS = 7   # days after follow-up 1 before final follow-up

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name TEXT NOT NULL,
    address       TEXT DEFAULT '',
    phone         TEXT DEFAULT '',
    website       TEXT DEFAULT '',
    email         TEXT NOT NULL,
    category      TEXT DEFAULT '',
    zipcode       TEXT DEFAULT '',
    lat           REAL DEFAULT NULL,
    lon           REAL DEFAULT NULL,
    imported_at   TEXT DEFAULT (datetime('now')),
    status        TEXT DEFAULT 'new'
    -- status values: new | active | unsubscribed | closed
);

CREATE TABLE IF NOT EXISTS outreach_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL REFERENCES leads(id),
    step       TEXT NOT NULL,
    -- step values: initial | follow_up_1 | follow_up_2
    sent_at    TEXT,
    due_at     TEXT,   -- when the NEXT step becomes due
    replied_at TEXT,
    status     TEXT DEFAULT 'sent'
    -- status values: sent | replied | bounced
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _within_radius(row: sqlite3.Row) -> bool:
    lat, lon = row["lat"], row["lon"]
    if lat is None or lon is None:
        return False
    return _haversine_miles(ORIGIN_LAT, ORIGIN_LON, lat, lon) <= MAX_RADIUS_MILES


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        # Migrate existing DBs that predate the lat/lon columns
        for col in ("lat", "lon"):
            try:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col} REAL DEFAULT NULL")
            except sqlite3.OperationalError:
                pass  # column already exists


def import_leads(csv_path: str) -> int:
    """Import enriched leads CSV into the DB. Returns count of newly added rows."""
    imported = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        with _conn() as conn:
            for row in reader:
                email = row.get("email", "").strip().lower()
                if not email:
                    continue
                # Deduplicate by email address
                if conn.execute("SELECT 1 FROM leads WHERE email = ?", (email,)).fetchone():
                    continue
                conn.execute(
                    """INSERT INTO leads
                           (business_name, address, phone, website, email, category, zipcode)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row.get("business_name", "").strip(),
                        row.get("address", "").strip(),
                        row.get("phone", "").strip(),
                        row.get("website", "").strip(),
                        email,
                        row.get("category", "").strip(),
                        row.get("zipcode", "").strip(),
                    ),
                )
                imported += 1
    return imported


def geocode_leads() -> int:
    """Geocode leads that have an address but no lat/lon. Returns count geocoded."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, address FROM leads WHERE lat IS NULL AND address != ''"
        ).fetchall()

    if not rows:
        return 0

    geolocator = Nominatim(user_agent="clearjet-outreach/1.0")
    geocoded = 0
    for row in rows:
        try:
            location = geolocator.geocode(row["address"], timeout=10)
            if location:
                with _conn() as conn:
                    conn.execute(
                        "UPDATE leads SET lat = ?, lon = ? WHERE id = ?",
                        (location.latitude, location.longitude, row["id"]),
                    )
                geocoded += 1
            time.sleep(1.1)  # Nominatim rate limit: 1 req/sec
        except (GeocoderTimedOut, GeocoderServiceError):
            pass

    return geocoded


def get_pending_leads(limit: int = 25) -> list[sqlite3.Row]:
    """Leads that have not yet received an initial email and are within the service radius."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT l.*
               FROM   leads l
               WHERE  l.status = 'new'
               AND    NOT EXISTS (
                          SELECT 1 FROM outreach_log ol
                          WHERE  ol.lead_id = l.id AND ol.step = 'initial'
                      )"""
        ).fetchall()
    return [r for r in rows if _within_radius(r)][:limit]


def get_due_follow_ups(step: str) -> list[sqlite3.Row]:
    """Leads where the given follow-up step is due, not yet sent, and within the service radius."""
    prev = "initial" if step == "follow_up_1" else "follow_up_1"
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT l.*
               FROM   leads l
               JOIN   outreach_log prev_log ON prev_log.lead_id = l.id
               WHERE  l.status = 'active'
               AND    prev_log.step    = ?
               AND    prev_log.due_at <= ?
               AND    NOT EXISTS (
                          SELECT 1 FROM outreach_log ol
                          WHERE  ol.lead_id = l.id AND ol.step = ?
                      )""",
            (prev, now, step),
        ).fetchall()
    return [r for r in rows if _within_radius(r)]


def log_sent(lead_id: int, step: str) -> None:
    """Record a sent email and set when the next step is due."""
    days = FOLLOW_UP_1_DAYS if step == "initial" else FOLLOW_UP_2_DAYS
    due_at = (datetime.utcnow() + timedelta(days=days)).isoformat()
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO outreach_log (lead_id, step, sent_at, due_at)
               VALUES (?, ?, ?, ?)""",
            (lead_id, step, now, due_at),
        )
        # Mark lead active once the initial email goes out
        if step == "initial":
            conn.execute("UPDATE leads SET status = 'active' WHERE id = ?", (lead_id,))


def log_reply(lead_id: int) -> None:
    now = datetime.utcnow().isoformat()
    with _conn() as conn:
        conn.execute(
            """UPDATE outreach_log
               SET    replied_at = ?, status = 'replied'
               WHERE  lead_id = ?
               ORDER  BY id DESC
               LIMIT  1""",
            (now, lead_id),
        )
        conn.execute("UPDATE leads SET status = 'closed' WHERE id = ?", (lead_id,))


def log_unsubscribe(lead_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE leads SET status = 'unsubscribed' WHERE id = ?", (lead_id,))


def get_stats() -> dict:
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        contacted = conn.execute(
            "SELECT COUNT(DISTINCT lead_id) FROM outreach_log WHERE step = 'initial'"
        ).fetchone()[0]
        replied = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status = 'closed'"
        ).fetchone()[0]
        unsub = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status = 'unsubscribed'"
        ).fetchone()[0]

    return {
        "total_leads":   total,
        "contacted":     contacted,
        "pending":       total - contacted,
        "replied":       replied,
        "unsubscribed":  unsub,
    }
