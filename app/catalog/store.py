"""Catalog storage. SQLite for now; the schema is plain SQL so it ports to MariaDB later.

DB path: env CATALOG_DB, default data/catalog.db (gitignored with the rest of data/).
"""
import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "catalog.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id                    TEXT PRIMARY KEY,
    name_fi               TEXT,
    name_en               TEXT,
    name_sv               TEXT,
    brand                 TEXT,
    category              TEXT,
    vendor_name           TEXT,
    sales_unit            TEXT NOT NULL,
    base_unit             TEXT,
    allowed_lot_size      REAL,
    country_of_origin     TEXT,
    temperature_condition INTEGER,
    has_allergen_data     INTEGER NOT NULL,  -- 1 if any allergen or free-from entry is declared
    delivery_reliability  REAL,              -- 0-1 from CSV, NULL if not listed
    co2_kg_per_kg         REAL,              -- category average from CSV, NULL if not listed
    key_ingredients_fi    TEXT,
    key_ingredients_en    TEXT
);
-- nutritionalClaim ids, e.g. FREE_FROM_LACTOSE, VEGAN
CREATE TABLE IF NOT EXISTS product_claims (
    product_id TEXT NOT NULL REFERENCES products(id),
    claim      TEXT NOT NULL,
    PRIMARY KEY (product_id, claim)
);
-- GS1 allergen codes with level CONTAINS / MAY_CONTAIN / FREE_FROM
CREATE TABLE IF NOT EXISTS product_allergens (
    product_id TEXT NOT NULL REFERENCES products(id),
    code       TEXT NOT NULL,
    level      TEXT NOT NULL,
    PRIMARY KEY (product_id, code, level)
);
-- packagingMarkedLabel ids (certifications, origin marks, temperature class)
CREATE TABLE IF NOT EXISTS product_labels (
    product_id TEXT NOT NULL REFERENCES products(id),
    label      TEXT NOT NULL,
    PRIMARY KEY (product_id, label)
);
CREATE TABLE IF NOT EXISTS product_units (
    product_id         TEXT NOT NULL REFERENCES products(id),
    unit_id            TEXT NOT NULL,
    size_in_base_units REAL,
    PRIMARY KEY (product_id, unit_id)
);
-- explicit substitution rules from the catalog (only ~190 products have any)
CREATE TABLE IF NOT EXISTS substitutions (
    product_id    TEXT NOT NULL REFERENCES products(id),
    substitute_id TEXT NOT NULL,
    PRIMARY KEY (product_id, substitute_id)
);
CREATE INDEX IF NOT EXISTS idx_claims_claim ON product_claims(claim);
CREATE INDEX IF NOT EXISTS idx_allergens_code ON product_allergens(code, level);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
"""

TABLES = ("substitutions", "product_units", "product_labels", "product_allergens",
          "product_claims", "products")


def db_path() -> Path:
    return Path(os.environ.get("CATALOG_DB", DEFAULT_DB))


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the catalog DB (creating the schema if needed). Use ':memory:' in tests."""
    path = str(path or db_path())
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
