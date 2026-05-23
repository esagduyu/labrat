"""Seed the test DuckDB fixture with orders, users, and regions tables."""

from pathlib import Path

import duckdb

DB_PATH = Path(__file__).parent.parent / "tests" / "fixtures" / "sample_dbs" / "test.duckdb"


def seed(path: Path = DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))

    conn.execute("""
        CREATE OR REPLACE TABLE regions (
            id          INTEGER PRIMARY KEY,
            name        VARCHAR(50) NOT NULL,
            country     VARCHAR(50) NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO regions VALUES
            (1, 'Americas', 'US'),
            (2, 'EMEA',     'UK'),
            (3, 'APAC',     'JP'),
            (4, 'Other',    'N/A')
    """)

    conn.execute("""
        CREATE OR REPLACE TABLE users (
            id          INTEGER PRIMARY KEY,
            username    VARCHAR(100) NOT NULL,
            email       VARCHAR(200) NOT NULL,
            region_id   INTEGER,
            created_at  DATE NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO users VALUES
            (1, 'alice',   'alice@example.com',   1, '2024-01-15'),
            (2, 'bob',     'bob@example.com',     2, '2024-02-20'),
            (3, 'charlie', 'charlie@example.com', 3, '2024-03-10'),
            (4, 'diana',   'diana@example.com',   1, '2024-04-05'),
            (5, 'eve',     'eve@example.com',     4, '2024-05-01')
    """)

    conn.execute("""
        CREATE OR REPLACE TABLE orders (
            id           INTEGER PRIMARY KEY,
            user_id      INTEGER NOT NULL,
            region_id    INTEGER NOT NULL,
            total_amount DECIMAL(10, 2) NOT NULL,
            status       VARCHAR(20) NOT NULL,
            order_date   DATE NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO orders VALUES
            (1, 1, 1, 1250.00, 'completed', '2025-10-05'),
            (2, 2, 2, 890.50,  'completed', '2025-10-15'),
            (3, 3, 3, 420.75,  'pending',   '2025-11-01'),
            (4, 4, 1, 3150.00, 'completed', '2025-11-20'),
            (5, 1, 1, 2200.00, 'completed', '2025-12-10'),
            (6, 2, 2, 675.00,  'cancelled', '2025-12-15'),
            (7, 5, 4, 88.50,   'completed', '2025-12-20')
    """)

    conn.close()
    print(f"Seeded test database at {path}")


if __name__ == "__main__":
    seed()
