"""Shared pytest fixtures for LabRat tests.

The snap_compare fixture is provided automatically by pytest-textual-snapshot.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest


@pytest.fixture(scope="session")
def ecommerce_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small ecommerce-shaped DuckDB built in a temp dir.

    Tests must NOT depend on ``tests/fixtures/sample_dbs/ecommerce.duckdb`` — that file is
    gitignored (a manually-created artifact for TUI testing) and absent in CI. This builds
    an equivalent read-only-friendly DB once per session so the cartographer tests are
    self-contained. Schema mirrors the manual fixture (customers/products/orders/events);
    data is shaped so that: emails are unique per customer (high-cardinality → skipped in
    Dimensions), order status is low-cardinality (listed), and every FK value resolves
    (joins verify at 100%).
    """
    path = tmp_path_factory.mktemp("sample_dbs") / "ecommerce.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        """
        CREATE TABLE customers (
            customer_id INTEGER, name VARCHAR, email VARCHAR, region VARCHAR,
            signup_date DATE, is_test BOOLEAN
        );
        CREATE TABLE products (
            product_id INTEGER, name VARCHAR, category VARCHAR,
            price DECIMAL(10,2), supplier VARCHAR
        );
        CREATE TABLE orders (
            order_id INTEGER, customer_id INTEGER, product_id INTEGER,
            total_amount DECIMAL(10,2), status VARCHAR, region VARCHAR, created_at TIMESTAMP
        );
        CREATE TABLE events (
            event_id INTEGER, customer_id INTEGER, event_type VARCHAR,
            wau INTEGER, is_test BOOLEAN, created_at TIMESTAMP
        );
        INSERT INTO customers VALUES
            (1,'Alice','alice@x.com','North',DATE '2024-01-01',false),
            (2,'Bob','bob@x.com','South',DATE '2024-01-02',false),
            (3,'Carol','carol@x.com','North',DATE '2024-01-03',false),
            (4,'Dan','dan@x.com','South',DATE '2024-01-04',false),
            (5,'Eve','eve@x.com','North',DATE '2024-01-05',true),
            (6,'Frank','frank@x.com','South',DATE '2024-01-06',false);
        INSERT INTO products VALUES
            (1,'Widget','tools',9.99,'Acme'),
            (2,'Gadget','tools',19.99,'Acme'),
            (3,'Gizmo','toys',4.99,'Globex'),
            (4,'Doohickey','toys',14.99,'Globex');
        INSERT INTO orders VALUES
            (1,1,1,9.99,'placed','North',TIMESTAMP '2024-02-01 10:00:00'),
            (2,2,2,19.99,'shipped','South',TIMESTAMP '2024-02-02 10:00:00'),
            (3,3,3,4.99,'delivered','North',TIMESTAMP '2024-02-03 10:00:00'),
            (4,4,1,9.99,'placed','South',TIMESTAMP '2024-02-04 10:00:00'),
            (5,5,2,19.99,'shipped','North',TIMESTAMP '2024-02-05 10:00:00'),
            (6,6,4,14.99,'delivered','South',TIMESTAMP '2024-02-06 10:00:00'),
            (7,1,3,4.99,'placed','North',TIMESTAMP '2024-02-07 10:00:00'),
            (8,2,4,14.99,'shipped','South',TIMESTAMP '2024-02-08 10:00:00');
        INSERT INTO events VALUES
            (1,1,'view',100,false,TIMESTAMP '2024-03-01 09:00:00'),
            (2,2,'click',150,false,TIMESTAMP '2024-03-02 09:00:00'),
            (3,3,'view',120,false,TIMESTAMP '2024-03-03 09:00:00'),
            (4,4,'purchase',200,false,TIMESTAMP '2024-03-04 09:00:00'),
            (5,5,'view',90,true,TIMESTAMP '2024-03-05 09:00:00');
        """
    )
    con.close()
    return path
