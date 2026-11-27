"""Database layer: one interface for SQLite files and live MySQL servers.

Everything above this file (agents, guardrails, pipeline) talks to a `Database` object
and never needs to know which engine is underneath.
"""
import sqlite3
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------- schema model
@dataclass
class Column:
    name: str
    type: str
    pk: bool = False


@dataclass
class Table:
    name: str
    columns: list[Column]
    foreign_keys: list[tuple[str, str, str]] = field(default_factory=list)  # (column, ref_table, ref_column)
    sample_row: dict = field(default_factory=dict)
    row_count: int = 0
    description: str = ""
    value_hints: dict[str, list] = field(default_factory=dict)  # low-cardinality text columns -> all values

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def parent_links(self) -> list[tuple[str, str]]:
        """Self-referencing foreign keys, i.e. the table is a hierarchy (category -> parent category)."""
        return [(c, rc) for c, t, rc in self.foreign_keys if t == self.name]

    def to_prompt(self) -> str:
        """Compact text form of the table, used inside LLM prompts."""
        cols = ", ".join(f"{c.name} {c.type}{' PK' if c.pk else ''}" for c in self.columns)
        lines = [f"Table: {self.name} (~{self.row_count} rows)"]
        if self.description:
            lines.append(f"Description: {self.description}")
        lines.append(f"Columns: {cols}")
        if self.foreign_keys:
            fks = ", ".join(f"{c} -> {t}.{rc}" for c, t, rc in self.foreign_keys)
            lines.append(f"Foreign keys: {fks}")
        for col, ref_col in self.parent_links:
            lines.append(f"Hierarchy: {col} points to another row of {self.name} ({ref_col}). Rows can be parents "
                         f"of other rows; other tables may link only to child rows, so filtering on a parent "
                         f"needs a join through {col}.")
        if self.value_hints:
            hints = "; ".join(f"{c} in ({', '.join(repr(v) for v in vals)})" for c, vals in self.value_hints.items())
            lines.append(f"Allowed values: {hints}")
        if self.sample_row:
            lines.append(f"Sample row: {self.sample_row}")
        return "\n".join(lines)


@dataclass
class ExecResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False
    elapsed_ms: int = 0
    error: str = ""


TEXT_TYPES = ("CHAR", "TEXT", "CLOB", "ENUM")
from app.config import PROFILE_MAX_ROWS, PROFILE_QUERY_TIMEOUT_S  # see .env.example

TIMEOUT_MESSAGE = "Query took too long and was stopped."


def _short(value, limit: int = 40):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    if isinstance(value, (bytes, bytearray)):
        return "<binary>"
    if value is not None and not isinstance(value, (str, int, float, bool)):
        return str(value)  # dates, decimals
    return value


def _clean(value):
    """Make a DB value JSON-friendly (MySQL returns Decimal / date objects)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return "<binary>"
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return float(value)
    except ImportError:
        pass
    return str(value)


# ---------------------------------------------------------------- base class
class Database:
    dialect: str = ""          # sqlglot dialect name
    display_name: str = ""     # shown in the UI

    def quote(self, name: str) -> str:
        raise NotImplementedError

    def _connect(self):
        raise NotImplementedError

    def _list_tables(self, conn) -> list[str]:
        raise NotImplementedError

    def _columns(self, conn, table: str) -> list[Column]:
        raise NotImplementedError

    def _foreign_keys(self, conn, table: str) -> list[tuple[str, str, str]]:
        raise NotImplementedError

    def _row_count(self, conn, table: str) -> int:
        raise NotImplementedError

    def _limit_timeout(self, conn, timeout_s: float):
        """Engine-specific way to stop runaway queries."""

    def _run(self, conn, sql: str, params=()):
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            # no params: pass the SQL through untouched, so '%' in LIKE / DATE_FORMAT isn't read as a placeholder
            cur.execute(sql)
        return cur

    # ---- shared logic
    profile_workers = 1  # parallel connections used while profiling (remote servers benefit from more)

    def cache_key(self) -> str:
        """Identifies this database for the schema cache."""
        raise NotImplementedError

    def _bulk_metadata(self, conn) -> dict[str, tuple[list[Column], list[tuple[str, str, str]], int]]:
        """name -> (columns, foreign keys, row count). Default: a few queries per table."""
        return {name: (self._columns(conn, name), self._foreign_keys(conn, name), self._row_count(conn, name))
                for name in self._list_tables(conn)}

    def _profile_table(self, name: str, cols: list[Column], rows: int) -> tuple[dict, dict]:
        """Sample row + allowed values for one table, on its own connection (so tables run in parallel)."""
        conn = self._connect()
        try:
            self._limit_timeout(conn, PROFILE_QUERY_TIMEOUT_S)
            sample = {}
            try:
                cur = self._run(conn, f"SELECT * FROM {self.quote(name)} LIMIT 1")
                row = cur.fetchone()
                sample = {d[0]: _short(v) for d, v in zip(cur.description, row)} if row else {}
            except Exception:
                pass  # a sample row is a nice-to-have
            hints = self._value_hints(conn, name, cols) if sample and rows <= PROFILE_MAX_ROWS else {}
            return sample, hints
        finally:
            conn.close()

    def extract_schema(self) -> dict[str, Table]:
        """Every table: columns, keys, approximate row count, a sample row and allowed values."""
        from concurrent.futures import ThreadPoolExecutor

        started = time.time()
        conn = self._connect()
        try:
            self._limit_timeout(conn, PROFILE_QUERY_TIMEOUT_S)
            meta = self._bulk_metadata(conn)
        finally:
            conn.close()
        print(f"[schema] {self.display_name}: {len(meta)} tables, metadata in {time.time() - started:.1f}s",
              flush=True)

        def work(name):
            cols, _, rows = meta[name]
            return name, self._profile_table(name, cols, rows)

        tables: dict[str, Table] = {}
        with ThreadPoolExecutor(max_workers=self.profile_workers) as pool:
            for i, (name, (sample, hints)) in enumerate(pool.map(work, meta), 1):
                cols, fks, rows = meta[name]
                tables[name] = Table(name, cols, fks, sample, rows, value_hints=hints)
                if i % 10 == 0 or i == len(meta):
                    print(f"[schema]   profiled {i}/{len(meta)} tables ({time.time() - started:.1f}s)", flush=True)
        print(f"[schema] done in {time.time() - started:.1f}s", flush=True)
        return tables

    def _value_hints(self, conn, table: str, cols: list[Column], max_values: int = 10) -> dict[str, list]:
        """For text columns with few distinct values (status, tier, payment method), list every value.

        Showing the real values up front stops the LLM inventing ones like 'Completed' or 'PhonePe'.
        """
        hints = {}
        for c in cols:
            if c.pk or (c.type and not any(t in c.type.upper() for t in TEXT_TYPES)):
                continue
            try:
                values = [r[0] for r in self._run(
                    conn, f"SELECT DISTINCT {self.quote(c.name)} FROM {self.quote(table)} "
                          f"WHERE {self.quote(c.name)} IS NOT NULL LIMIT {max_values + 1}").fetchall()]
            except Exception:
                continue  # timed out or unsupported: skip this column, keep going
            looks_like_date = all(isinstance(v, str) and v[:4].isdigit() and v[4:5] == "-" for v in values)
            if (0 < len(values) <= max_values and not looks_like_date
                    and all(isinstance(v, str) and len(v) <= 40 for v in values)):
                hints[c.name] = sorted(values)
        return hints

    def execute(self, sql: str, max_rows: int, timeout_s: float | None = None) -> ExecResult:
        """Run a (guardrail-checked) query read-only, with a row cap and a time limit."""
        from app.config import QUERY_TIMEOUT_S
        timeout_s = timeout_s or QUERY_TIMEOUT_S
        start = time.time()
        try:
            conn = self._connect()
        except Exception as e:
            return ExecResult(False, error=f"Could not connect: {e}")
        try:
            self._limit_timeout(conn, timeout_s)
            cur = self._run(conn, sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = [[_clean(v) for v in r] for r in rows[:max_rows]]
            return ExecResult(True, columns, rows, truncated, int((time.time() - start) * 1000))
        except Exception as e:
            msg = str(e)
            if "interrupted" in msg or "max_execution_time" in msg.lower() or "max_statement_time" in msg.lower():
                msg = TIMEOUT_MESSAGE
            return ExecResult(False, error=msg, elapsed_ms=int((time.time() - start) * 1000))
        finally:
            conn.close()

    def value_exists(self, table: str, column: str, value: str) -> bool:
        conn = self._connect()
        try:
            self._limit_timeout(conn, PROFILE_QUERY_TIMEOUT_S)
            sql = f"SELECT 1 FROM {self.quote(table)} WHERE {self.quote(column)} = {self.placeholder} LIMIT 1"
            return self._run(conn, sql, (value,)).fetchone() is not None
        finally:
            conn.close()

    def distinct_values(self, table: str, column: str, limit: int = 2000) -> list[str]:
        conn = self._connect()
        try:
            self._limit_timeout(conn, PROFILE_QUERY_TIMEOUT_S)
            sql = (f"SELECT DISTINCT {self.quote(column)} FROM {self.quote(table)} "
                   f"WHERE {self.quote(column)} IS NOT NULL LIMIT {int(limit)}")
            return [str(r[0]) for r in self._run(conn, sql).fetchall()]
        finally:
            conn.close()


# ---------------------------------------------------------------- SQLite
class SQLiteDatabase(Database):
    dialect = "sqlite"
    placeholder = "?"

    def __init__(self, path: str):
        self.path = path
        self.display_name = path.replace("\\", "/").split("/")[-1]
        with open(path, "rb") as f:
            if not f.read(16).startswith(b"SQLite format 3\x00"):
                raise ValueError("This file is not a valid SQLite database.")

    def quote(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def cache_key(self) -> str:
        import os
        st = os.stat(self.path)
        return f"sqlite|{os.path.abspath(self.path)}|{st.st_size}|{int(st.st_mtime)}"

    def _connect(self):
        # mode=ro: SQLite itself refuses any write, even if a guardrail were bypassed
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)

    def _list_tables(self, conn):
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def _columns(self, conn, table):
        return [Column(r[1], r[2] or "TEXT", bool(r[5])) for r in conn.execute(f"PRAGMA table_info({self.quote(table)})")]

    def _foreign_keys(self, conn, table):
        return [(r[3], r[2], r[4]) for r in conn.execute(f"PRAGMA foreign_key_list({self.quote(table)})")]

    def _row_count(self, conn, table):
        return conn.execute(f"SELECT COUNT(*) FROM {self.quote(table)}").fetchone()[0]

    def _limit_timeout(self, conn, timeout_s):
        deadline = time.time() + timeout_s
        # SQLite calls this every N instructions; returning 1 aborts the query
        conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10_000)


# ---------------------------------------------------------------- MySQL / MariaDB
class MySQLDatabase(Database):
    dialect = "mysql"
    placeholder = "%s"
    profile_workers = 8  # remote round-trips dominate; profile 8 tables at a time

    def __init__(self, host: str, user: str, password: str, database: str, port: int = 3306):
        self._params = dict(host=host, port=int(port), user=user, password=password, database=database)
        self.database = database
        self.display_name = f"{database} @ {host}:{port} (MySQL)"
        conn = self._connect()  # fail fast with a clear error if the details are wrong
        conn.close()

    def quote(self, name: str) -> str:
        return "`" + name.replace("`", "``") + "`"

    def cache_key(self) -> str:
        p = self._params
        return f"mysql|{p['host']}|{p['port']}|{p['database']}"

    def _bulk_metadata(self, conn):
        """Whole-database metadata in 3 queries instead of 3 per table (matters a lot for remote servers)."""
        meta: dict[str, tuple[list[Column], list, int]] = {}
        for name, rows in self._run(conn, "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
                                          "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                                          "ORDER BY TABLE_NAME", (self.database,)).fetchall():
            meta[name] = ([], [], int(rows or 0))
        for table, col, dtype, key in self._run(
                conn, "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.COLUMNS "
                      "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION", (self.database,)).fetchall():
            if table in meta:
                meta[table][0].append(Column(col, dtype.upper(), key == "PRI"))
        for table, col, ref_table, ref_col in self._run(
                conn, "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
                      "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = %s "
                      "AND REFERENCED_TABLE_NAME IS NOT NULL", (self.database,)).fetchall():
            if table in meta:
                meta[table][1].append((col, ref_table, ref_col))
        return meta

    def _connect(self):
        import pymysql
        # read_timeout: never hang forever on a slow or unreachable remote server
        conn = pymysql.connect(**self._params, charset="utf8mb4", connect_timeout=8, read_timeout=60,
                               autocommit=True)
        with conn.cursor() as cur:
            # second safety net next to the guardrails: the session itself refuses writes
            cur.execute("SET SESSION TRANSACTION READ ONLY")
        return conn

    def _list_tables(self, conn):
        rows = self._run(conn, "SELECT TABLE_NAME FROM information_schema.TABLES "
                               "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME",
                         (self.database,)).fetchall()
        return [r[0] for r in rows]

    def _columns(self, conn, table):
        rows = self._run(conn, "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.COLUMNS "
                               "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
                         (self.database, table)).fetchall()
        return [Column(r[0], r[1].upper(), r[2] == "PRI") for r in rows]

    def _foreign_keys(self, conn, table):
        rows = self._run(conn, "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
                               "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = %s "
                               "AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL",
                         (self.database, table)).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def _row_count(self, conn, table):
        # information_schema gives an estimate instantly; exact COUNT(*) can be slow on big tables
        row = self._run(conn, "SELECT TABLE_ROWS FROM information_schema.TABLES "
                              "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (self.database, table)).fetchone()
        return int(row[0] or 0) if row else 0

    def _limit_timeout(self, conn, timeout_s):
        with conn.cursor() as cur:
            try:
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_s * 1000)}")   # MySQL (ms)
            except Exception:
                try:
                    cur.execute(f"SET SESSION max_statement_time = {float(timeout_s)}")  # MariaDB (s)
                except Exception:
                    pass
