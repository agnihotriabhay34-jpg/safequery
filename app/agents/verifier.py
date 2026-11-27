"""Layer 3 of hallucination detection: an LLM judge that checks whether the result answers the question.

Runs on EVERY query, not only when earlier layers complain: a query can be valid,
grounded and error-free and still answer a different question.
"""
from app.core import llm
from app.core.db import Table

SYSTEM = """You are the Verification Agent in a text-to-SQL system. Be skeptical: your job is to catch
queries that run fine but answer a DIFFERENT question. Judge whether the SQL and its result correctly
answer the user's question, using the schema of the tables the query used.

Check, in this order:
1. ENTITY: What does one row of the main table represent (see its description and columns)? Is that the
   same kind of thing the question asks about? Counting genomes is not counting sequences; counting
   orders is not counting customers. If the entity is different, the verdict is "incorrect".
2. LOGIC: Does the query filter, join, group, aggregate and sort the way the question asks?
3. PLAUSIBILITY: Is the result believable given the table sizes shown? (e.g. 1 sequence for a species,
   when the sequence table has millions of rows, is suspicious.) Empty or zero results need a reason.
If you are not sure the query answers the question, use "uncertain", not "correct".

The "answer" must describe what was ACTUALLY computed, in the table's own terms
(e.g. "1 genome record", not "1 sequence"), and must not claim more than the result shows.

Respond in JSON:
{"verdict": "correct" | "incorrect" | "uncertain",
 "confidence": <number 0-1>,
 "issues": ["<specific problem>", ...],
 "answer": "<one-sentence natural-language answer to the question based on the result, or empty>"}"""


def verify(question: str, sql: str, columns: list[str], rows: list[list], row_count: int,
           truncated: bool, tables: list[Table] | None = None) -> dict:
    preview = "\n".join(str(r) for r in rows[:15])
    schema = "\n\n".join(t.to_prompt() for t in (tables or []))
    prompt = (f"Question: {question}\n\nTables used by the query:\n{schema or '(unknown)'}\n\n"
              f"SQL:\n{sql}\n\nResult columns: {columns}\n"
              f"Rows returned: {row_count}{'+ (truncated)' if truncated else ''}\n"
              f"First rows:\n{preview or '(no rows)'}")
    try:
        out = llm.chat_json(SYSTEM, prompt, reasoning="medium")  # judging needs more thought than writing
    except Exception as e:
        return {"verdict": "uncertain", "confidence": 0.0, "issues": [f"verifier failed: {e}"], "answer": ""}
    verdict = out.get("verdict", "uncertain")
    if verdict not in ("correct", "incorrect", "uncertain"):
        verdict = "uncertain"
    try:
        confidence = max(0.0, min(1.0, float(out.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"verdict": verdict, "confidence": confidence,
            "issues": [str(i) for i in out.get("issues", [])], "answer": str(out.get("answer", ""))}
