"""
Step 4: SQLite Lead Database
Tracks every lead through the outreach pipeline.

Schema:
  - leads: business info + status + sequence tracking
  - email_log: every email sent with timestamps
"""

import sqlite3
import os
from datetime import datetime, timedelta

import config


def get_db(db_path: str = None) -> sqlite3.Connection:
    """Get a database connection, creating tables if needed."""
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT NOT NULL,
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            website TEXT DEFAULT '',
            email TEXT DEFAULT '',
            category TEXT DEFAULT '',
            zipcode TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            current_touch INTEGER DEFAULT 0,
            last_emailed_at TEXT,
            next_email_at TEXT,
            replied_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(business_name, address)
        );

        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            touch_number INTEGER NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            sent_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );

        CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
        CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
        CREATE INDEX IF NOT EXISTS idx_leads_next_email ON leads(next_email_at);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Lead CRUD
# ---------------------------------------------------------------------------

def upsert_lead(conn: sqlite3.Connection, lead_data: dict) -> int:
    """Insert or update a lead. Returns the lead ID."""
    existing = conn.execute(
        "SELECT id FROM leads WHERE business_name = ? AND address = ?",
        (lead_data.get("business_name", ""), lead_data.get("address", "")),
    ).fetchone()

    if existing:
        lead_id = existing["id"]
        # Only update fields that are non-empty in the new data
        updates = []
        values = []
        for field in ["phone", "website", "email", "category", "zipcode"]:
            val = lead_data.get(field, "")
            if val:
                updates.append(f"{field} = ?")
                values.append(val)
        if updates:
            updates.append("updated_at = datetime('now')")
            values.append(lead_id)
            conn.execute(
                f"UPDATE leads SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            conn.commit()
        return lead_id
    else:
        cur = conn.execute(
            """INSERT INTO leads (business_name, address, phone, website, email,
               category, zipcode, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'new')""",
            (
                lead_data.get("business_name", ""),
                lead_data.get("address", ""),
                lead_data.get("phone", ""),
                lead_data.get("website", ""),
                lead_data.get("email", ""),
                lead_data.get("category", ""),
                lead_data.get("zipcode", ""),
            ),
        )
        conn.commit()
        return cur.lastrowid


def bulk_upsert_leads(conn: sqlite3.Connection, leads: list[dict]) -> int:
    """Insert/update many leads at once. Returns count of new leads."""
    count = 0
    for lead in leads:
        existing = conn.execute(
            "SELECT id FROM leads WHERE business_name = ? AND address = ?",
            (lead.get("business_name", ""), lead.get("address", "")),
        ).fetchone()
        if not existing:
            count += 1
        upsert_lead(conn, lead)
    return count


def get_leads_needing_email(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Get leads with email addresses that are due for their next touch."""
    now = datetime.utcnow().isoformat()
    return conn.execute(
        """SELECT * FROM leads
           WHERE email != ''
             AND status IN ('new', 'emailing')
             AND current_touch < ?
             AND (next_email_at IS NULL OR next_email_at <= ?)
           ORDER BY next_email_at ASC
           LIMIT ?""",
        (len(config.SEQUENCE_DELAYS), now, config.DAILY_SEND_LIMIT),
    ).fetchall()


def get_leads_without_email(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Get leads that have a website but no email (need scraping)."""
    return conn.execute(
        """SELECT * FROM leads
           WHERE email = '' AND website != '' AND status != 'dead'
           ORDER BY created_at ASC""",
    ).fetchall()


def mark_email_sent(conn: sqlite3.Connection, lead_id: int, touch: int,
                    subject: str, body: str) -> None:
    """Record that an email was sent."""
    now = datetime.utcnow()
    next_touch_idx = touch  # 0-indexed, so touch 1 = index 0 already sent
    if next_touch_idx < len(config.SEQUENCE_DELAYS):
        next_at = now + timedelta(days=config.SEQUENCE_DELAYS[next_touch_idx])
        next_at_str = next_at.isoformat()
    else:
        next_at_str = None

    conn.execute(
        """UPDATE leads SET
             current_touch = ?,
             last_emailed_at = ?,
             next_email_at = ?,
             status = 'emailing',
             updated_at = datetime('now')
           WHERE id = ?""",
        (touch, now.isoformat(), next_at_str, lead_id),
    )
    conn.execute(
        "INSERT INTO email_log (lead_id, touch_number, subject, body) VALUES (?, ?, ?, ?)",
        (lead_id, touch, subject, body),
    )
    conn.commit()


def mark_replied(conn: sqlite3.Connection, lead_id: int) -> None:
    """Mark a lead as having replied — pauses their sequence."""
    conn.execute(
        """UPDATE leads SET
             status = 'replied',
             replied_at = datetime('now'),
             next_email_at = NULL,
             updated_at = datetime('now')
           WHERE id = ?""",
        (lead_id,),
    )
    conn.commit()


def mark_dead(conn: sqlite3.Connection, lead_id: int) -> None:
    """Mark a lead as dead (bounced, unsubscribed, etc)."""
    conn.execute(
        "UPDATE leads SET status = 'dead', next_email_at = NULL, updated_at = datetime('now') WHERE id = ?",
        (lead_id,),
    )
    conn.commit()


def update_lead_email(conn: sqlite3.Connection, lead_id: int, email: str) -> None:
    """Set a lead's email address."""
    conn.execute(
        "UPDATE leads SET email = ?, updated_at = datetime('now') WHERE id = ?",
        (email, lead_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_stats(conn: sqlite3.Connection) -> dict:
    """Get a summary of the pipeline."""
    stats = {}
    for status in ["new", "emailing", "replied", "dead"]:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM leads WHERE status = ?", (status,)
        ).fetchone()
        stats[status] = row["c"]

    row = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()
    stats["total"] = row["c"]

    row = conn.execute("SELECT COUNT(*) as c FROM leads WHERE email != ''").fetchone()
    stats["with_email"] = row["c"]

    row = conn.execute("SELECT COUNT(*) as c FROM email_log").fetchone()
    stats["emails_sent"] = row["c"]

    return stats
