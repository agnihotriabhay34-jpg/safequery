"""Ask questions from the terminal.

  python cli.py data/shop.db "Which 5 customers spent the most?"
  python cli.py mysql://user:password@127.0.0.1:3306/retail "Which region has the most revenue?" --trace
"""
import json
import sys
from urllib.parse import unquote, urlparse

from app.core.db import MySQLDatabase, SQLiteDatabase
from app.pipeline import SafeQuery

if len(sys.argv) < 3:
    sys.exit('usage: python cli.py <database.db | mysql://user:pass@host:port/db> "<question>" [--trace]')

target = sys.argv[1]
if target.startswith("mysql://"):
    u = urlparse(target)
    db = MySQLDatabase(u.hostname or "127.0.0.1", unquote(u.username or ""), unquote(u.password or ""),
                       u.path.lstrip("/"), u.port or 3306)
else:
    db = SQLiteDatabase(target)

engine = SafeQuery(db)
result = engine.ask(sys.argv[2])

print(f"\nStatus:  {result['status'].upper()}   ({result['attempts']} attempt(s), {result['total_ms']} ms)")
print(f"SQL:     {result['sql']}")
if result["answer"]:
    print(f"Answer:  {result['answer']}")
for flag in result["flags"]:
    print(f"Flag:    {flag}")
if result["rows"]:
    print("\n" + " | ".join(result["columns"]))
    for row in result["rows"][:10]:
        print(" | ".join(str(v) for v in row))
    if len(result["rows"]) > 10:
        print(f"... {len(result['rows']) - 10} more rows")
if "--trace" in sys.argv:
    print("\nTrace:")
    for step in result["trace"]:
        print(" ", json.dumps(step, default=str))
