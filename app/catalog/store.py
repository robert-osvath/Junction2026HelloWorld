"""Catalog storage. SQLite for now; this is the only module that talks SQL, so moving to
MariaDB later means changing this file only.

Portability notes for the MariaDB move:
- keys are VARCHAR (MariaDB rejects TEXT in keys), foreign keys are table-level clauses;
- swap sqlite3 for the `mariadb` connector (same `?` paramstyle) and drop the PRAGMA.

DB path: env CATALOG_DB, default data/catalog.db (gitignored with the rest of data/).
"""
import os
import re
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

# Full-text index. The trigram tokenizer matches substrings, which is what Finnish compounds need
# ("maito" in "kevytmaitojuoma"); remove_diacritics folds a/ä, o/ö. Rebuildable via rebuild_fts().
FTS_COLUMNS = ("name_fi", "name_en", "name_sv", "brand", "key_ingredients_fi", "key_ingredients_en")
# bm25 column weights (same order): names count far more than ingredients
FTS_WEIGHTS = (10.0, 10.0, 4.0, 3.0, 1.0, 1.0)
FTS_SCHEMA = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5("
    "id UNINDEXED, " + ", ".join(FTS_COLUMNS) + ", "
    "tokenize = 'trigram remove_diacritics 1')",
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
    for stmt in SCHEMA + FTS_SCHEMA:
        conn.execute(stmt)
    # existing catalog.db built before the FTS index existed: fill it
    if conn.execute("SELECT count(*) FROM products_fts").fetchone()[0] == 0 and \
            conn.execute("SELECT count(*) FROM products").fetchone()[0]:
        rebuild_fts(conn)
    return conn


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """(Re)build the full-text index from the products table."""
    cols = ", ".join(FTS_COLUMNS)
    with conn:
        conn.execute("DELETE FROM products_fts")
        conn.execute(f"INSERT INTO products_fts (id, {cols}) SELECT id, {cols} FROM products")


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
    rebuild_fts(conn)


# --- search ---

def fts_terms(q: str) -> list[str]:
    """Lower-cased word tokens of a query. Trigram needs >= 3 chars, shorter tokens are dropped."""
    return [t for t in re.findall(r"\w+", q.lower()) if len(t) >= 3]


def search_rows(conn: sqlite3.Connection, terms: list[str], claims: list[str],
                exclude_allergens: list[str], category: str, candidates: int) -> list[sqlite3.Row]:
    """Candidate products matching the filters, best bm25 first. With `terms`, a product matches if
    ANY term occurs (as a substring, so compounds match); callers rerank on how many terms hit.
    Without terms it is a filter-only browse (bm25 = 0). Rows carry `bm25` (lower = better)."""
    where, args = [], []
    if claims:
        where.append(f"(SELECT count(*) FROM product_claims c WHERE c.product_id = p.id "
                     f"AND c.claim IN ({','.join('?' * len(claims))})) = ?")
        args += [*claims, len(set(claims))]
    if exclude_allergens:
        where.append(f"NOT EXISTS (SELECT 1 FROM product_allergens a WHERE a.product_id = p.id "
                     f"AND a.level IN ('CONTAINS', 'MAY_CONTAIN') "
                     f"AND a.code IN ({','.join('?' * len(exclude_allergens))}))")
        args += exclude_allergens
    if category:
        where.append("p.category = ?")
        args.append(category)
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    if terms:
        match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        sql = (f"SELECT p.*, bm25(products_fts, 0, {', '.join(map(str, FTS_WEIGHTS))}) AS bm25 "
               f"FROM products_fts JOIN products p ON p.id = products_fts.id "
               f"WHERE products_fts MATCH ?" + (" AND " + " AND ".join(where) if where else "") +
               " ORDER BY bm25 LIMIT ?")
        return conn.execute(sql, [match, *args, candidates]).fetchall()
    return conn.execute(f"SELECT p.*, 0.0 AS bm25 FROM products p{cond} ORDER BY p.id LIMIT ?",
                        [*args, candidates]).fetchall()


def item_details(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    """claims / allergen lists per product id, for mapping rows to CatalogItem."""
    out = {i: {"claims": [], "contains": [], "may": []} for i in ids}
    marks = ",".join("?" * len(ids))
    if not ids:
        return out
    for r in conn.execute(f"SELECT product_id, claim FROM product_claims WHERE product_id IN ({marks}) "
                          f"ORDER BY claim", ids):
        out[r[0]]["claims"].append(r[1])
    for r in conn.execute(f"SELECT product_id, code, level FROM product_allergens "
                          f"WHERE product_id IN ({marks}) ORDER BY code", ids):
        if r[2] == "CONTAINS":
            out[r[0]]["contains"].append(r[1])
        elif r[2] == "MAY_CONTAIN":
            out[r[0]]["may"].append(r[1])
    return out
