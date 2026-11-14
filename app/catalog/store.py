"""Catalog storage. SQLite for now; this is the only module that talks SQL, so moving to
MariaDB later means changing this file only.

Portability notes for the MariaDB move:
- keys are VARCHAR (MariaDB rejects TEXT in keys), foreign keys are table-level clauses;
- swap sqlite3 for the `mariadb` connector (same `?` paramstyle) and drop the PRAGMA.

DB path: env CATALOG_DB, default data/catalog.db (gitignored with the rest of data/).
"""
import os
import sqlite3
from pathlib import Path
from typing import Iterable

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "catalog.db"

# Order matters: it's the INSERT column order. normalize() must produce exactly these keys.
PRODUCT_COLUMNS = (
    "id", "name_fi", "name_en", "name_sv", "brand", "category", "vendor_name", "sales_unit",
    "base_unit", "allowed_lot_size", "country_of_origin", "temperature_condition",
    "has_allergen_data", "delivery_reliability", "co2_kg_per_kg",
    "key_ingredients_fi", "key_ingredients_en",
)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS products (
        id                    VARCHAR(32) PRIMARY KEY,
        name_fi               TEXT,
        name_en               TEXT,
        name_sv               TEXT,
        brand                 VARCHAR(255),
        category              VARCHAR(32),
        vendor_name           VARCHAR(255),
        sales_unit            VARCHAR(16) NOT NULL,
        base_unit             VARCHAR(16),
        allowed_lot_size      DOUBLE,
        country_of_origin     VARCHAR(8),
        temperature_condition INTEGER,
        has_allergen_data     SMALLINT NOT NULL,  -- 1 if any CONTAINS / MAY_CONTAIN / FREE_FROM is declared
        delivery_reliability  DOUBLE,             -- 0-1 from CSV, NULL if not listed
        co2_kg_per_kg         DOUBLE,             -- category average from CSV, NULL if not listed
        key_ingredients_fi    TEXT,
        key_ingredients_en    TEXT
    )""",
    # nutritionalClaim ids, e.g. FREE_FROM_LACTOSE, VEGAN
    """CREATE TABLE IF NOT EXISTS product_claims (
        product_id VARCHAR(32) NOT NULL,
        claim      VARCHAR(64) NOT NULL,
        PRIMARY KEY (product_id, claim),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
    # GS1 allergen codes; level is one of ALLERGEN_LEVELS (see loader), one level per code
    """CREATE TABLE IF NOT EXISTS product_allergens (
        product_id VARCHAR(32) NOT NULL,
        code       VARCHAR(8)  NOT NULL,
        level      VARCHAR(16) NOT NULL,
        PRIMARY KEY (product_id, code),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
    # packagingMarkedLabel ids (certifications, origin marks, temperature class)
    """CREATE TABLE IF NOT EXISTS product_labels (
        product_id VARCHAR(32) NOT NULL,
        label      VARCHAR(64) NOT NULL,
        PRIMARY KEY (product_id, label),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
    """CREATE TABLE IF NOT EXISTS product_units (
        product_id         VARCHAR(32) NOT NULL,
        unit_id            VARCHAR(16) NOT NULL,
        size_in_base_units DOUBLE,
        PRIMARY KEY (product_id, unit_id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
    # explicit substitution rules from the catalog (only ~190 products have any)
    """CREATE TABLE IF NOT EXISTS substitutions (
        product_id    VARCHAR(32) NOT NULL,
        substitute_id VARCHAR(32) NOT NULL,
        PRIMARY KEY (product_id, substitute_id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        FOREIGN KEY (substitute_id) REFERENCES products(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_claims_claim ON product_claims(claim)",
    "CREATE INDEX IF NOT EXISTS idx_allergens_code ON product_allergens(code, level)",
    "CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)",
]

# children first, so deletes never violate foreign keys
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
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite-only; MariaDB enforces FKs by default
    for stmt in SCHEMA:
        conn.execute(stmt)
    return conn


def replace_catalog(conn: sqlite3.Connection, products: list[dict],
                    claims: Iterable[tuple[str, str]], allergens: Iterable[tuple[str, str, str]],
                    labels: Iterable[tuple[str, str]], units: Iterable[tuple[str, str, float | None]],
                    substitutions: Iterable[tuple[str, str]]) -> None:
    """Atomically replace all catalog rows. `products` are dicts keyed by PRODUCT_COLUMNS."""
    cols = ", ".join(PRODUCT_COLUMNS)
    marks = ", ".join("?" * len(PRODUCT_COLUMNS))
    with conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.executemany(f"INSERT INTO products ({cols}) VALUES ({marks})",
                         [tuple(p[c] for c in PRODUCT_COLUMNS) for p in products])
        conn.executemany("INSERT INTO product_claims VALUES (?, ?)", claims)
        conn.executemany("INSERT INTO product_allergens VALUES (?, ?, ?)", allergens)
        conn.executemany("INSERT INTO product_labels VALUES (?, ?)", labels)
        conn.executemany("INSERT INTO product_units VALUES (?, ?, ?)", units)
        conn.executemany("INSERT INTO substitutions VALUES (?, ?)", substitutions)


def offer_rows(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    """Products by id, each with `size_in_base_units` of its sales unit (None if not listed)."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT p.*, u.size_in_base_units FROM products p "
        f"LEFT JOIN product_units u ON u.product_id = p.id AND u.unit_id = p.sales_unit "
        f"WHERE p.id IN ({marks})", ids).fetchall()
    return {r["id"]: dict(r) for r in rows}
