"""Guardrail tests. No API key needed:  python -m pytest -q"""
import os
import sqlite3

import pytest

from app.core.db import SQLiteDatabase
from app.core.guardrails import check_filter_values, check_sql


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("db") / "t.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT, city TEXT);
        CREATE TABLE orders (order_id INTEGER PRIMARY KEY,
                             customer_id INTEGER REFERENCES customers(customer_id), status TEXT);
        INSERT INTO customers VALUES (1, 'Asha', 'Kanpur'), (2, 'Ravi', 'Delhi');
        INSERT INTO orders VALUES (1, 1, 'shipped'), (2, 2, 'pending');
    """)
    conn.commit()
    conn.close()
    return SQLiteDatabase(path)


@pytest.fixture(scope="module")
def schema(db):
    return db.extract_schema()


def test_schema_extraction(schema):
    assert set(schema) == {"customers", "orders"}
    assert schema["orders"].foreign_keys == [("customer_id", "customers", "customer_id")]


def test_valid_join_passes(schema):
    r = check_sql("SELECT c.city, COUNT(*) AS n FROM orders o JOIN customers c "
                  "ON o.customer_id = c.customer_id GROUP BY c.city ORDER BY n DESC", schema)
    assert r.ok and not r.errors


@pytest.mark.parametrize("sql", ["DELETE FROM orders", "DROP TABLE orders",
                                 "UPDATE orders SET status = 'x'", "SELECT 1; DROP TABLE orders",
                                 "PRAGMA table_info(orders)"])
def test_writes_are_blocked(schema, sql):
    r = check_sql(sql, schema)
    assert not r.ok and r.unsafe


def test_hallucinated_table(schema):
    r = check_sql("SELECT * FROM sales", schema)
    assert not r.ok and "sales" in r.errors[0]


def test_hallucinated_qualified_column(schema):
    r = check_sql("SELECT c.town FROM customers c", schema)
    assert not r.ok and "town" in r.errors[0]


def test_hallucinated_unqualified_column(schema):
    assert not check_sql("SELECT revenue FROM orders", schema).ok


def test_cte_and_aliases_are_not_false_positives(schema):
    sql = ("WITH t AS (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) "
           "SELECT c.name, t.n FROM t JOIN customers c ON c.customer_id = t.customer_id ORDER BY t.n DESC")
    assert check_sql(sql, schema).ok


def test_invented_filter_value(db, schema):
    problems = check_filter_values("SELECT * FROM orders WHERE status = 'delivered'", schema, db)
    assert problems and "'shipped'" in problems[0]


def test_real_filter_value_is_fine(db, schema):
    assert check_filter_values("SELECT * FROM customers WHERE city = 'Kanpur'", schema, db) == []


def test_value_hints_and_readonly_execution(db, schema):
    assert schema["orders"].value_hints["status"] == ["pending", "shipped"]
    result = db.execute("DELETE FROM orders", max_rows=10)  # guardrails would block this; the connection must too
    assert not result.ok and "readonly" in result.error.lower().replace(" ", "")


def test_zero_aggregate_counts_as_empty():
    from app.pipeline import _looks_empty
    assert _looks_empty([]) and _looks_empty([[0]]) and _looks_empty([[None, 0]])
    assert not _looks_empty([[5]]) and not _looks_empty([[0], [0]])
