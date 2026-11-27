"""The SafeQuery pipeline: retrieval -> table selection -> generation -> guardrails -> execution -> verification.

Every step is recorded in a trace so the UI (and you, in an interview) can see exactly
why a query was accepted, retried, flagged or blocked.
"""
import time

from app.agents.sql_generator import generate_sql
from app.agents.table_selector import select_tables
from app.agents.verifier import verify
from app import config
from app.core import guardrails, schema_cache
from app.core.db import TIMEOUT_MESSAGE, Database
from app.core.schema_index import SchemaIndex, describe_tables

MAX_ATTEMPTS = 3  # first try + 2 self-corrections


def _looks_empty(rows: list[list]) -> bool:
    """No rows, or a single row of zeros/NULLs (what COUNT/SUM return when a filter matches nothing)."""
    if not rows:
        return True
    return len(rows) == 1 and all(v is None or v == 0 for v in rows[0])


class SafeQuery:
    def __init__(self, db: Database, embedding_function=None, refresh: bool = False):
        t0 = time.time()
        self.db = db
        key = db.cache_key()
        cached = None if refresh else schema_cache.load(key)
        self.from_cache = cached is not None
        if cached:
            self.schema = cached
            print(f"[schema] {db.display_name}: loaded {len(cached)} tables from cache", flush=True)
        else:
            self.schema = db.extract_schema()
            if not self.schema:
                raise ValueError("The database has no tables.")
            t1 = time.time()
            describe_tables(self.schema)
            print(f"[schema] table descriptions written in {time.time() - t1:.1f}s", flush=True)
            described = any(t.description for t in self.schema.values())
            has_data = any(t.row_count for t in self.schema.values())
            if described or not has_data:
                schema_cache.save(key, self.schema)  # never cache a profile the LLM couldn't describe
        self.index = SchemaIndex(self.schema, embedding_function=embedding_function)
        self.load_ms = int((time.time() - t0) * 1000)

    def close(self) -> None:
        self.index.close()

    def summary(self) -> dict:
        return {
            "tables": [{"name": t.name, "rows": t.row_count, "columns": t.column_names,
                        "description": t.description} for t in self.schema.values()],
            "load_ms": self.load_ms,
            "database": self.db.display_name,
            "dialect": self.db.dialect,
            "from_cache": self.from_cache,
        }

    def ask(self, question: str) -> dict:
        t0 = time.time()
        trace: list[dict] = []
        result = {"question": question, "status": "error", "sql": "", "columns": [], "rows": [],
                  "truncated": False, "answer": "", "flags": [], "trace": trace}

        # 1. Retrieval (vector search + FK neighbours)
        candidates = self.index.retrieve(question)
        trace.append({"stage": "retrieval", "tables": candidates})

        # 2. Table selection
        chosen, reason = select_tables(question, [self.schema[n] for n in candidates])
        trace.append({"stage": "table_selection", "tables": chosen, "reason": reason})
        tables = [self.schema[n] for n in chosen]

        feedback, sql = None, None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            # 3. Generation
            sql, explanation = generate_sql(question, tables, feedback, sql, dialect=self.db.dialect)
            trace.append({"stage": "generation", "attempt": attempt, "sql": sql, "explanation": explanation})
            if not sql:
                # clear anything left over from an earlier, rejected attempt
                result.update(status="unanswerable", sql="", columns=[], rows=[], truncated=False, flags=[],
                              answer=explanation or "This question can't be answered from this database.")
                break

            # 4. Guardrails (Layer 1): safety + schema grounding
            guard = guardrails.check_sql(sql, self.schema, dialect=self.db.dialect)
            trace.append({"stage": "guardrails", "attempt": attempt, "ok": guard.ok,
                          "errors": guard.errors, "warnings": guard.warnings})
            if guard.unsafe:
                result.update(status="blocked", sql=sql, columns=[], rows=[], truncated=False, answer="",
                              flags=guard.errors)
                break
            if not guard.ok:
                # the generator referenced something that isn't in the schema; show it exactly what was wrong
                feedback = guard.errors
                continue

            # 5. Execution (Layer 2: runtime + empty-result value grounding)
            run = self.db.execute(sql, max_rows=config.MAX_ROWS)
            trace.append({"stage": "execution", "attempt": attempt, "ok": run.ok, "error": run.error,
                          "rows": len(run.rows), "truncated": run.truncated, "ms": run.elapsed_ms})
            if not run.ok:
                if run.error == TIMEOUT_MESSAGE:
                    feedback = self._slow_query_feedback(guard.tables_used)
                else:
                    feedback = [f"Database error: {run.error}"]
                continue
            if _looks_empty(run.rows):
                value_problems = guardrails.check_filter_values(sql, self.schema, self.db)
                if value_problems:
                    trace.append({"stage": "value_check", "attempt": attempt, "errors": value_problems})
                    feedback = value_problems + ["The query matched nothing because of these filter values."]
                    continue

            # 6. Verification (Layer 3): always runs
            used = [self.schema[n] for n in guard.tables_used if n in self.schema]
            check = verify(question, sql, run.columns, run.rows, len(run.rows), run.truncated, tables=used)
            trace.append({"stage": "verification", "attempt": attempt, **check})
            result.update(sql=sql, columns=run.columns, rows=run.rows, truncated=run.truncated,
                          answer=check["answer"], flags=guard.warnings + check["issues"])
            if check["verdict"] == "correct":
                result["status"] = "verified"
                break
            empty_and_unsure = _looks_empty(run.rows) and check["verdict"] == "uncertain"
            if (check["verdict"] == "incorrect" or empty_and_unsure) and attempt < MAX_ATTEMPTS:
                feedback = ["The verifier says the result may not answer the question:"] + check["issues"]
                if _looks_empty(run.rows):
                    feedback.append("The query matched no rows. Re-check the join path and filter granularity.")
                    for t in tables:
                        for col, _ in t.parent_links:
                            feedback.append(f"Table {t.name} is a hierarchy via {col}: if the filter value is a "
                                            f"parent row, join {t.name} to itself through {col}.")
                continue
            result["status"] = "flagged"  # shown to the user, with the verifier's concerns attached
            break
        else:
            # ran out of attempts without a usable query
            last_errors = feedback or []
            result.update(status="error", sql=sql or "", columns=[], rows=[], truncated=False, answer="",
                          flags=last_errors)

        result["attempts"] = sum(1 for s in trace if s["stage"] == "generation")
        result["total_ms"] = int((time.time() - t0) * 1000)
        return result

    def _slow_query_feedback(self, tables_used: list[str]) -> list[str]:
        """Tell the generator WHY the query was slow, so the retry changes strategy instead of repeating it."""
        sizes = sorted(((self.schema[n].row_count, n) for n in tables_used if n in self.schema), reverse=True)
        big = [f"{n} (~{rows:,} rows)" for rows, n in sizes if rows >= 1_000_000]
        lines = [f"The query exceeded the {int(config.QUERY_TIMEOUT_S)}-second time limit."]
        if big:
            lines.append(f"It scans very large tables: {', '.join(big)}.")
        lines.append("Write a cheaper query: look for pre-computed count/total/summary columns in smaller tables "
                     "(e.g. a num_* or *_count column), filter early on indexed key columns, or aggregate fewer rows. "
                     "If the question truly needs a full scan of a huge table, return an empty sql and explain.")
        return lines
