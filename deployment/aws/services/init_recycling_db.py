#!/usr/bin/env python3
"""
Master database initialisation script for recycling.db.

Consolidates:
  - Table definitions from sqlite_server.py (_ensure_database_exists)
  - Table + index definitions from init_iot_db.py
  - task_tokens table (used by iot-store-task-token Lambda)
  - All indexes from src/database/indexes.py (DocumentIndexManager)

Run on the EC2 database server:
  python3 init_recycling_db.py [--db-path /var/lib/sqlite-server/recycling.db]

Safe to re-run: every statement uses CREATE TABLE/INDEX IF NOT EXISTS.
"""

import os
import pwd
import sqlite3
import sys
import argparse
from pathlib import Path


DB_DEFAULT = "/var/lib/sqlite-server/recycling.db"


def init_tables(cursor: sqlite3.Cursor) -> None:
    """Create all document collection tables and the event store."""

    print("Creating tables...")

    # ------------------------------------------------------------------
    # NoSQL document collections
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendor_invoices_docs (
            invoice_id INTEGER PRIMARY KEY,
            document   TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gateways_docs (
            gateway_id TEXT PRIMARY KEY,
            document   TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices_docs (
            device_id TEXT PRIMARY KEY,
            document  TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS measurements_docs (
            measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            document       TEXT NOT NULL,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config_updates_docs (
            update_id  TEXT PRIMARY KEY,
            document   TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ------------------------------------------------------------------
    # Event store  (src/database/event_store.py)
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            aggregate_id   TEXT    NOT NULL,
            aggregate_type TEXT    NOT NULL,
            event_type     TEXT    NOT NULL,
            event_data     TEXT    NOT NULL,
            version        INTEGER NOT NULL,
            timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ------------------------------------------------------------------
    # Task tokens  (iot-store-task-token Lambda / Step Functions)
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_tokens (
            update_id  TEXT    NOT NULL,
            event_type TEXT    NOT NULL,
            task_token TEXT    NOT NULL,
            gateway_id TEXT    NOT NULL,
            timestamp  TEXT    NOT NULL,
            ttl        INTEGER,
            PRIMARY KEY (update_id, event_type)
        )
    """)

    print("  ✓ vendor_invoices_docs")
    print("  ✓ gateways_docs")
    print("  ✓ devices_docs")
    print("  ✓ measurements_docs")
    print("  ✓ config_updates_docs")
    print("  ✓ events")
    print("  ✓ task_tokens")


def init_indexes(cursor: sqlite3.Cursor) -> None:
    """Create all indexes (events, task_tokens, and all document collections)."""

    print("\nCreating indexes...")

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_aggregate
        ON events(aggregate_id, aggregate_type, version)
    """)

    # ------------------------------------------------------------------
    # task_tokens
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_task_tokens_gateway
        ON task_tokens(gateway_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_task_tokens_ttl
        ON task_tokens(ttl)
    """)

    # ------------------------------------------------------------------
    # vendor_invoices_docs
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_vendor_id
        ON vendor_invoices_docs(json_extract(document, '$.vendor.vendor_id'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_vendor_name
        ON vendor_invoices_docs(json_extract(document, '$.vendor.vendor_name'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_status
        ON vendor_invoices_docs(json_extract(document, '$.extraction_status'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_upload_date
        ON vendor_invoices_docs(json_extract(document, '$.upload_date'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_processing_date
        ON vendor_invoices_docs(json_extract(document, '$.processing_date'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_category_id
        ON vendor_invoices_docs(json_extract(document, '$.category.category_id'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_category_name
        ON vendor_invoices_docs(json_extract(document, '$.category.category_name'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_total_amount
        ON vendor_invoices_docs(json_extract(document, '$.total_amount'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_weight
        ON vendor_invoices_docs(json_extract(document, '$.reported_weight_kg'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoice_vendor_status
        ON vendor_invoices_docs(
            json_extract(document, '$.vendor.vendor_id'),
            json_extract(document, '$.extraction_status')
        )
    """)

    # ------------------------------------------------------------------
    # gateways_docs
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gateway_status
        ON gateways_docs(json_extract(document, '$.status'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gateway_last_updated
        ON gateways_docs(json_extract(document, '$.last_updated'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gateway_last_heartbeat
        ON gateways_docs(json_extract(document, '$.last_heartbeat'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gateway_created_at
        ON gateways_docs(json_extract(document, '$.created_at'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gateway_location
        ON gateways_docs(json_extract(document, '$.location'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gateway_name
        ON gateways_docs(json_extract(document, '$.name'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gateway_health
        ON gateways_docs(json_extract(document, '$.health'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_gateway_status_health
        ON gateways_docs(
            json_extract(document, '$.status'),
            json_extract(document, '$.health')
        )
    """)

    # ------------------------------------------------------------------
    # devices_docs
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_device_gateway_id
        ON devices_docs(json_extract(document, '$.gateway_id'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_device_status
        ON devices_docs(json_extract(document, '$.status'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_device_type
        ON devices_docs(json_extract(document, '$.device_type'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_device_last_updated
        ON devices_docs(json_extract(document, '$.last_updated'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_device_last_measurement
        ON devices_docs(json_extract(document, '$.last_measurement'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_device_location
        ON devices_docs(json_extract(document, '$.location'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_device_gateway_status
        ON devices_docs(
            json_extract(document, '$.gateway_id'),
            json_extract(document, '$.status')
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_device_gateway_type
        ON devices_docs(
            json_extract(document, '$.gateway_id'),
            json_extract(document, '$.device_type')
        )
    """)

    # ------------------------------------------------------------------
    # measurements_docs
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_device_id
        ON measurements_docs(json_extract(document, '$.device_info.device_id'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_gateway_id
        ON measurements_docs(json_extract(document, '$.device_info.gateway_id'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_device_type
        ON measurements_docs(json_extract(document, '$.device_info.device_type'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_type
        ON measurements_docs(json_extract(document, '$.measurement_type'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_timestamp
        ON measurements_docs(json_extract(document, '$.timestamp'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_processed
        ON measurements_docs(json_extract(document, '$.processed'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_uploaded
        ON measurements_docs(json_extract(document, '$.uploaded_to_cloud'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_weight
        ON measurements_docs(json_extract(document, '$.payload.weight_kg'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_material_type
        ON measurements_docs(json_extract(document, '$.payload.material_type'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_device_timestamp
        ON measurements_docs(
            json_extract(document, '$.device_info.device_id'),
            json_extract(document, '$.timestamp')
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_gateway_timestamp
        ON measurements_docs(
            json_extract(document, '$.device_info.gateway_id'),
            json_extract(document, '$.timestamp')
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_type_timestamp
        ON measurements_docs(
            json_extract(document, '$.measurement_type'),
            json_extract(document, '$.timestamp')
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurement_analytics
        ON measurements_docs(
            json_extract(document, '$.device_info.gateway_id'),
            json_extract(document, '$.measurement_type'),
            json_extract(document, '$.timestamp'),
            json_extract(document, '$.processed')
        )
    """)

    # ------------------------------------------------------------------
    # config_updates_docs
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_config_gateway_id
        ON config_updates_docs(json_extract(document, '$.gateway_id'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_config_state
        ON config_updates_docs(json_extract(document, '$.state'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_config_created_at
        ON config_updates_docs(json_extract(document, '$.created_at'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_config_last_updated
        ON config_updates_docs(json_extract(document, '$.last_updated'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_config_version
        ON config_updates_docs(json_extract(document, '$.config_version'))
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_config_gateway_state
        ON config_updates_docs(
            json_extract(document, '$.gateway_id'),
            json_extract(document, '$.state')
        )
    """)

    print("  ✓ events indexes (1)")
    print("  ✓ task_tokens indexes (2)")
    print("  ✓ vendor_invoices_docs indexes (10)")
    print("  ✓ gateways_docs indexes (8)")
    print("  ✓ devices_docs indexes (8)")
    print("  ✓ measurements_docs indexes (14)")
    print("  ✓ config_updates_docs indexes (6)")


def verify(cursor: sqlite3.Cursor) -> None:
    """Print summary of tables and indexes in the database."""

    print("\nVerification:")

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cursor.fetchall()]
    print(f"  Tables ({len(tables)}):")
    for t in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {t}")
        count = cursor.fetchone()[0]
        print(f"    - {t}  ({count} rows)")

    cursor.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    )
    idx_count = cursor.fetchone()[0]
    print(f"  Custom indexes: {idx_count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Master initialisation script for recycling.db"
    )
    parser.add_argument(
        "--db-path",
        default=DB_DEFAULT,
        help=f"Path to SQLite database file (default: {DB_DEFAULT})",
    )
    args = parser.parse_args()

    db_path = args.db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"Initialising database: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        init_tables(cursor)
        init_indexes(cursor)
        conn.commit()
        verify(cursor)
        print(f"\n✓ Database initialisation complete: {db_path}")
    except Exception as e:
        conn.rollback()
        print(f"\n✗ Initialisation failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    # Fix ownership so the sqlite-server service (runs as 'ubuntu') can write
    try:
        pw = pwd.getpwnam("ubuntu")
        os.chown(db_path, pw.pw_uid, pw.pw_gid)
        print(f"✓ Ownership set to ubuntu:ubuntu on {db_path}")
    except (KeyError, PermissionError):
        pass  # Not on EC2 or not running as root — ownership fix not needed


if __name__ == "__main__":
    main()
