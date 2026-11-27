"""Copy a SQLite database into MySQL (schema, keys and data) so you can test the live MySQL connection.

  python scripts/sqlite_to_mysql.py data/retail_india.db --host 127.0.0.1 --user root --password yourpass --database retail
"""
import argparse
import os
import sqlite3
import sys

import pymysql

TYPE_MAP = {"INTEGER": "BIGINT", "INT": "BIGINT", "REAL": "DOUBLE", "FLOAT": "DOUBLE", "NUMERIC": "DECIMAL(18,4)",
            "TEXT": "VARCHAR(255)", "": "VARCHAR(255)", "BLOB": "LONGBLOB"}


def q(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sqlite_path")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="")
    ap.add_argument("--database", required=True)
    args = ap.parse_args()

    if not os.path.isfile(args.sqlite_path) or os.path.getsize(args.sqlite_path) == 0:
        sys.exit(f"SQLite file not found (or empty): {os.path.abspath(args.sqlite_path)}")
    src = sqlite3.connect(f"file:{args.sqlite_path}?mode=ro", uri=True)  # never create a new empty file
    server = pymysql.connect(host=args.host, port=args.port, user=args.user, password=args.password, autocommit=True)
    with server.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {q(args.database)} CHARACTER SET utf8mb4")
    server.close()
    dst = pymysql.connect(host=args.host, port=args.port, user=args.user, password=args.password,
                          database=args.database, charset="utf8mb4", autocommit=False)
    cur = dst.cursor()
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")

    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        cols = list(src.execute(f'PRAGMA table_info("{t}")'))
        fks = list(src.execute(f'PRAGMA foreign_key_list("{t}")'))
        defs = [f"{q(c[1])} {TYPE_MAP.get((c[2] or '').upper(), 'VARCHAR(255)')}" for c in cols]
        pks = [c[1] for c in sorted(cols, key=lambda c: c[5]) if c[5]]
        if pks:
            defs.append(f"PRIMARY KEY ({', '.join(q(p) for p in pks)})")
        for fk in fks:
            defs.append(f"FOREIGN KEY ({q(fk[3])}) REFERENCES {q(fk[2])}({q(fk[4])})")
        cur.execute(f"DROP TABLE IF EXISTS {q(t)}")
        cur.execute(f"CREATE TABLE {q(t)} ({', '.join(defs)}) ENGINE=InnoDB")

        names = [c[1] for c in cols]
        insert = f"INSERT INTO {q(t)} ({', '.join(q(n) for n in names)}) VALUES ({', '.join(['%s'] * len(names))})"
        rows = src.execute(f'SELECT * FROM "{t}"')
        total = 0
        while batch := rows.fetchmany(5000):
            cur.executemany(insert, batch)
            total += len(batch)
        dst.commit()
        print(f"  {t:20} {total:>8} rows")

    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    dst.commit()
    dst.close()
    print(f"Done: {len(tables)} tables copied into MySQL database '{args.database}'.")


if __name__ == "__main__":
    main()
