"""SQL Generation Agent: writes SQLite SQL using only the selected tables."""
from app.core import llm
from app.core.db import Table

SYSTEM = """You are the SQL Generation Agent in a text-to-SQL system. Write ONE {dialect} SELECT query
that answers the user's question.

Rules:
- Use ONLY the tables and columns listed in the schema. Never invent names.
- Use functions that exist in this SQL dialect (e.g. dates: julianday/strftime in SQLite, DATEDIFF/DATE_FORMAT in MySQL).
- Read-only: SELECT (optionally WITH ... SELECT). Never INSERT, UPDATE, DELETE, DROP, PRAGMA, etc.
- Put text values in single quotes ('Rock'), never double quotes.
- Use the sample rows to match value formats (dates, casing, codes).
- Give readable column aliases. Add ORDER BY when the question implies ranking.
- If the question cannot be answered from this schema, return an empty "sql" and explain why.

Respond in JSON: {"sql": "<query or empty>", "explanation": "<one sentence on how the query answers the question>"}"""


DIALECT_NAMES = {"sqlite": "SQLite", "mysql": "MySQL"}


def generate_sql(question: str, tables: list[Table], feedback: list[str] | None = None,
                 previous_sql: str | None = None, dialect: str = "sqlite") -> tuple[str, str]:
    prompt = "Schema:\n\n" + "\n\n".join(t.to_prompt() for t in tables) + f"\n\nQuestion: {question}"
    if feedback:
        prompt += (f"\n\nYour previous query was:\n{previous_sql}\n\nIt was rejected for these reasons:\n- "
                   + "\n- ".join(feedback) + "\n\nWrite a corrected query.")
    out = llm.chat_json(SYSTEM.replace("{dialect}", DIALECT_NAMES.get(dialect, dialect)), prompt)
    sql = (out.get("sql") or "").strip().rstrip(";").strip()
    if sql.startswith("```"):  # defensive: strip markdown fences if the model adds them
        sql = sql.strip("`").removeprefix("sql").strip()
    return sql, out.get("explanation", "")
