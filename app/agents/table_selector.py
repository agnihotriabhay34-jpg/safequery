"""Table Selection Agent: narrows the retrieved candidates to the tables the query truly needs."""
from app.core import llm
from app.core.db import Table

SYSTEM = """You are the Table Selection Agent in a text-to-SQL system.
You get a user question and candidate tables found by vector search.
Choose the MINIMUM set of tables needed to answer the question, including any
bridge tables required to join them. Never invent table names.
Respond in JSON: {"tables": ["<name>", ...], "reason": "<one sentence>"}"""


def select_tables(question: str, candidates: list[Table]) -> tuple[list[str], str]:
    prompt = f"Question: {question}\n\nCandidate tables:\n\n" + "\n\n".join(t.to_prompt() for t in candidates)
    allowed = {t.name for t in candidates}
    try:
        out = llm.chat_json(SYSTEM, prompt)
        chosen = [n for n in out.get("tables", []) if n in allowed]  # drop hallucinated names
        reason = out.get("reason", "")
    except Exception as e:
        chosen, reason = [], f"selector failed: {e}"
    if not chosen:  # fail open: better to give the generator too many tables than none
        return [t.name for t in candidates], reason or "fallback: kept all candidates"
    return chosen, reason
