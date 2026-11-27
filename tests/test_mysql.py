"""MySQL tests. Skipped unless SAFEQUERY_TEST_MYSQL is set, e.g.

  SAFEQUERY_TEST_MYSQL=mysql://user:pass@127.0.0.1:3306/retail python -m pytest -q tests/test_mysql.py

The database must contain the sample retail data (scripts/sqlite_to_mysql.py).
"""
import os
from urllib.parse import unquote, urlparse

import pytest

from app.core.db import MySQLDatabase
from app.core.guardrails import check_filter_values, check_sql

URL = os.getenv("SAFEQUERY_TEST_MYSQL")
pytestmark = pytest.mark.skipif(not URL, reason="set SAFEQUERY_TEST_MYSQL to run MySQL tests")


@pytest.fixture(scope="module")
def db():
    u = urlparse(URL)
    return MySQLDatabase(u.hostname, unquote(u.username), unquote(u.password or ""), u.path.lstrip("/"), u.port or 3306)


@pytest.fixture(scope="module")
def schema(db):
    return db.extract_schema()


def test_schema(schema):
    assert "orders" in schema and ("customer_id", "customers", "customer_id") in schema["orders"].foreign_keys
    assert "Delivered" in schema["orders"].value_hints["order_status"]
    assert schema["categories"].parent_links == [("parent_category_id", "category_id")]


def test_mysql_dialect_and_execution(db, schema):
    sql = ("SELECT r.region_name, ROUND(SUM(oi.quantity * oi.unit_price - oi.discount_amount), 2) AS revenue "
           "FROM order_items oi JOIN orders o ON oi.order_id = o.order_id JOIN stores s ON o.store_id = s.store_id "
           "JOIN regions r ON s.region_id = r.region_id WHERE o.order_status = 'Delivered' "
           "GROUP BY r.region_name ORDER BY revenue DESC")
    assert check_sql(sql, schema, dialect="mysql").ok
    result = db.execute(sql, max_rows=10)
    assert result.ok and result.rows[0][0] == "North" and isinstance(result.rows[0][1], float)


def test_session_is_read_only(db):
    result = db.execute("DELETE FROM returns WHERE return_id = 1", max_rows=1)
    assert not result.ok


def test_value_grounding_mysql(db, schema):
    problems = check_filter_values("SELECT * FROM payments WHERE payment_method = 'PhonePe'", schema, db)
    assert problems and "'UPI'" in problems[0]


def test_percent_signs_in_sql(db):
    result = db.execute("SELECT COUNT(*) FROM payments WHERE payment_method LIKE '%UP%' "
                        "AND DATE_FORMAT(paid_at, '%Y') = '2025'", max_rows=1)
    assert result.ok and result.rows[0][0] > 0
