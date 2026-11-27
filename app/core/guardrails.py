"""Layer 1 of hallucination detection: static checks on the generated SQL before it runs.

- Safety: exactly one read-only statement (SELECT / WITH ... SELECT / UNION).
- Grounding: every table and column the SQL references must exist in the live schema.
- Value grounding (used after an empty result): string literals in WHERE filters must
  exist in the column they are compared against.
"""
import difflib
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.core.db import Database, Table

FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
             exp.Command, exp.Merge, exp.TruncateTable, exp.Pragma)


@dataclass
class GuardResult:
    ok: bool
    unsafe: bool = False                                  # tried to write / run a command
    errors: list[str] = field(default_factory=list)       # hallucinated tables/columns, parse errors
    warnings: list[str] = field(default_factory=list)     # suspicious but not certain
    tables_used: list[str] = field(default_factory=list)


def _schema_lookup(schema: dict[str, Table]):
    """Case-insensitive maps, because SQLite identifiers are case-insensitive."""
    tables = {name.lower(): name for name in schema}
    columns = {name.lower(): {c.lower() for c in t.column_names} for name, t in schema.items()}
    return tables, columns


def check_sql(sql: str, schema: dict[str, Table], dialect: str = "sqlite") -> GuardResult:
    # 1. Parse
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        return GuardResult(ok=False, errors=[f"SQL could not be parsed: {str(e).splitlines()[0]}"])
    # 2. Read-only: any write/command anywhere is blocked outright (no retry)
    for s in statements:
        if isinstance(s, FORBIDDEN) or any(s.find_all(*FORBIDDEN)):
            return GuardResult(ok=False, unsafe=True,
                               errors=[f"Only read-only SELECT queries are allowed (found {s.key.upper()})."])
    if len(statements) != 1:
        return GuardResult(ok=False, errors=["Exactly one SQL statement is allowed."])
    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.SetOperation)):
        return GuardResult(ok=False, errors=["The output is not a valid SELECT query."])

    tables_l, columns_l = _schema_lookup(schema)
    errors, warnings = [], []

    # Names that are defined inside the query itself (CTEs, derived tables) are not schema objects.
    cte_names = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
    derived_aliases = {s.alias.lower() for s in stmt.find_all(exp.Subquery) if s.alias}
    has_inner_scopes = bool(cte_names or derived_aliases)

    # 3. Tables
    alias_to_table: dict[str, str] = {}
    used: list[str] = []
    for t in stmt.find_all(exp.Table):
        name = t.name.lower()
        if not name or name in cte_names:
            continue
        if name not in tables_l:
            errors.append(f"Table '{t.name}' does not exist. Available tables: {', '.join(schema)}.")
            continue
        alias_to_table[t.alias_or_name.lower()] = name
        alias_to_table[name] = name
        if tables_l[name] not in used:
            used.append(tables_l[name])

    # 4. Columns
    select_aliases = {a.alias.lower() for a in stmt.find_all(exp.Alias)}
    used_columns = set().union(*(columns_l[t.lower()] for t in used)) if used else set()
    for col in stmt.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue
        cname, qual = col.name.lower(), col.table.lower()
        if qual:
            if qual in alias_to_table:
                real = alias_to_table[qual]
                if cname not in columns_l[real]:
                    errors.append(f"Column '{col.name}' does not exist in table '{tables_l[real]}'. "
                                  f"Its columns are: {', '.join(schema[tables_l[real]].column_names)}.")
            elif qual not in cte_names and qual not in derived_aliases:
                errors.append(f"'{col.table}.{col.name}' refers to an unknown table or alias '{col.table}'.")
        else:
            if cname in used_columns or cname in select_aliases:
                continue
            quoted = isinstance(col.this, exp.Identifier) and col.this.quoted
            if quoted:
                warnings.append(f'"{col.name}" in double quotes is treated as a column name; '
                                f"use single quotes for text values.")
            elif has_inner_scopes:
                warnings.append(f"Could not verify column '{col.name}' (defined inside a subquery or CTE?).")
            else:
                errors.append(f"Column '{col.name}' does not exist in the tables used ({', '.join(used)}).")

    errors, warnings = list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))  # drop duplicates
    return GuardResult(ok=not errors, errors=errors, warnings=warnings, tables_used=used)


def check_filter_values(sql: str, schema: dict[str, Table], db: Database) -> list[str]:
    """For `column = 'text'` filters, confirm the text exists in that column. Returns problems found.

    Used when a query returns zero rows: an invented filter value ('Rock music' instead of 'Rock')
    is a common hallucination that runs without error but silently returns nothing.
    """
    try:
        stmt = sqlglot.parse_one(sql, read=db.dialect)
    except Exception:
        return []
    tables_l, columns_l = _schema_lookup(schema)
    alias_to_table = {}
    for t in stmt.find_all(exp.Table):
        if t.name.lower() in tables_l:
            alias_to_table[t.alias_or_name.lower()] = t.name.lower()
            alias_to_table[t.name.lower()] = t.name.lower()

    problems = []
    for cmp in stmt.find_all(exp.EQ):
        col, lit = cmp.left, cmp.right
        if isinstance(lit, exp.Column):
            col, lit = lit, col
        if not (isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string):
            continue
        # find which table owns this column
        if col.table:
            owner = alias_to_table.get(col.table.lower())
        else:
            owners = [t for t in set(alias_to_table.values()) if col.name.lower() in columns_l[t]]
            owner = owners[0] if len(owners) == 1 else None
        if not owner:
            continue
        table, column, value = tables_l[owner], col.name, lit.this
        try:
            if db.value_exists(table, column, value):
                continue
            distinct = db.distinct_values(table, column)
        except Exception:
            continue  # too slow to check on this table (e.g. huge remote table): don't block the answer
        if len(distinct) <= 15:
            hint = f" Existing values: {', '.join(repr(d) for d in distinct)}."
        else:
            close = difflib.get_close_matches(value, distinct, n=5, cutoff=0.4)
            close += [d for d in distinct if value.lower() in d.lower() and d not in close][:5 - len(close)]
            hint = f" Similar existing values: {', '.join(repr(c) for c in close)}." if close else ""
        problems.append(f"Value '{value}' does not exist in {table}.{column}.{hint}")
    return list(dict.fromkeys(problems))
