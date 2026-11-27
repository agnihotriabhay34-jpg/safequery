# SafeQuery

**Multi-agent text-to-SQL with layered hallucination detection.**

Ask a question in plain English about any SQLite database you upload or any live MySQL database you connect. SafeQuery finds the relevant tables,
writes the SQL, checks it for hallucinations **before and after** it runs, and tells you whether the answer
can be trusted — with a full trace of what every agent did.

> LLMs are good at writing SQL that *looks* right. The dangerous failure isn't an error message —
> it's a query that runs fine and returns a plausible wrong answer. SafeQuery is built around catching that.

---

## How it works

```mermaid
flowchart LR
    Q([User question]) --> R[Schema retrieval<br/>ChromaDB + FK expansion]
    R --> S[Table Selection Agent]
    S --> G[SQL Generation Agent]
    G --> L1{Layer 1<br/>Guardrails}
    L1 -- unsafe --> B([Blocked])
    L1 -- hallucinated table/column --> G
    L1 -- ok --> E[Layer 2<br/>Read-only execution]
    E -- SQL error / invented filter value --> G
    E -- rows --> V{Layer 3<br/>Verification Agent}
    V -- incorrect --> G
    V -- correct --> OK([Verified answer])
    V -- uncertain / out of retries --> F([Flagged answer])
```

| Stage | What it does | Why |
|---|---|---|
| **Table descriptions** | On upload, the LLM writes a one-line description of every table from its columns and a sample row. | Works on any database with no hand-written docs. |
| **Schema profiling** | Lists every value of low-cardinality text columns (statuses, tiers, payment methods) and marks self-referencing tables as hierarchies. | Stops invented filter values *before* generation, and explains parent/child tables like categories. |
| **Schema retrieval** | Embeds each table (name + description + columns) in ChromaDB (all-MiniLM-L6-v2) and retrieves the top-k for the question, then adds foreign-key neighbours. | Large schemas don't fit in a prompt. FK expansion brings in bridge tables that similarity search misses. |
| **Table Selection Agent** | Narrows the candidates to the minimum set of tables needed. Names not in the candidate list are discarded. | Fewer tables in the prompt means fewer wrong joins. |
| **SQL Generation Agent** | Writes SQLite SQL using only the selected tables; returns an empty query if the question can't be answered from the schema. | Refusing is better than inventing. |
| **Layer 1 — Guardrails** | Parses the SQL with `sqlglot`. Blocks anything that isn't a single read-only `SELECT`. Checks every table and column against the live schema (alias-, CTE- and case-aware). | Catches hallucinated schema elements before execution. |
| **Layer 2 — Execution** | Runs on a **read-only** connection (SQLite `mode=ro`, MySQL `READ ONLY` session) with a timeout and row cap. If the result is empty, or an aggregate returns only 0/NULL, checks that every `column = 'text'` filter value actually exists, and suggests real values. | Invented filter values (`'delivered'` vs `'shipped'`) return zero rows silently. |
| **Layer 3 — Verification Agent** | An LLM judge reviews the question, SQL and result on **every** query and returns `correct` / `incorrect` / `uncertain` with specific issues. | A query can be valid and grounded and still answer a different question. |
| **Self-correction** | Any rejection, or an empty result the verifier is unsure about, is fed back to the generator with the exact reason (up to 3 attempts). | Specific feedback ("column `town` doesn't exist; columns are …") fixes most errors in one retry. |

Every answer gets one status:

- **Verified** — passed all three layers.
- **Flagged** — returned with the verifier's concerns attached; check before trusting.
- **Blocked** — the query tried to modify data or run a command.
- **Unanswerable** — the database doesn't contain what the question asks for.
- **Failed** — no valid query after all retries.

---

## Quick start

```bash
git clone https://github.com/agnihotriabhay34-jpg/safequery.git
cd safequery
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # Windows: copy .env.example .env
# put your free Groq API key in .env  (https://console.groq.com)

python scripts/make_sample_db.py  # creates data/shop.db to try it with
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>, upload `data/shop.db` (or any SQLite file), and ask a question.

### Connecting to MySQL

In the UI, choose **Connect MySQL** and enter host, port, user, password and database.
Use a **read-only user**: SafeQuery already blocks writes twice (guardrails + a read-only session),
but database permissions are the strongest guarantee.

```sql
CREATE USER 'safequery_ro'@'%' IDENTIFIED BY 'choose-a-password';
GRANT SELECT ON your_database.* TO 'safequery_ro'@'%';
```

No MySQL data to try? Copy any SQLite file into MySQL:

```bash
python scripts/sqlite_to_mysql.py data/shop.db --host 127.0.0.1 --user root --password yourpass --database shop
```

From the terminal: `python cli.py mysql://user:password@127.0.0.1:3306/shop "Top 5 customers by revenue"`
The first upload downloads the embedding model (~90 MB) once.

**From the terminal:**

```bash
python cli.py data/shop.db "Which 5 customers spent the most on delivered orders?" --trace
```

**Tests** (no API key needed):

```bash
python -m pytest -q
# MySQL tests too (needs a MySQL database loaded with the sample data):
SAFEQUERY_TEST_MYSQL=mysql://user:pass@127.0.0.1:3306/retail python -m pytest -q
```

### Example questions for `shop.db`

- Which 5 customers spent the most on delivered orders?
- What is the total revenue per product category?
- How many orders were cancelled in each city?
- Which supplier's products sell the most units?
- Delete all cancelled orders *(blocked)*
- What is the weather in Kanpur? *(unanswerable)*

---

## API

| Method | Endpoint | Body | Returns |
|---|---|---|---|
| `POST` | `/api/upload` | multipart `file` (.db / .sqlite / .sqlite3) | tables, row counts, descriptions |
| `POST` | `/api/connect/mysql` | `{"host", "port", "user", "password", "database"}` | tables, row counts, descriptions |
| `POST` | `/api/ask` | `{"question": "..."}` | status, sql, columns, rows, answer, flags, full agent trace |
| `GET` | `/api/status` | – | currently loaded database |

Interactive docs at `/docs`.

---

## Project structure

```
safequery/
├── app/
│   ├── main.py                # FastAPI app
│   ├── pipeline.py            # orchestrates agents, layers and retries; builds the trace
│   ├── config.py              # settings from .env
│   ├── agents/
│   │   ├── table_selector.py  # Table Selection Agent
│   │   ├── sql_generator.py   # SQL Generation Agent
│   │   └── verifier.py        # Verification Agent (Layer 3)
│   └── core/
│       ├── db.py              # SQLite + MySQL behind one interface: schema, profiling, read-only execution
│       ├── schema_index.py    # LLM table descriptions + ChromaDB retrieval
│       ├── guardrails.py      # Layer 1 checks + filter-value grounding
│       └── llm.py             # Groq client wrapper
├── static/index.html          # web UI (plain HTML/CSS/JS)
├── scripts/
│   ├── make_sample_db.py      # small sample e-commerce database
│   ├── make_retail_db.py      # a bigger 19-table retail database for testing
│   └── sqlite_to_mysql.py     # copy any SQLite database into MySQL
├── tests/                     # guardrail + SQLite tests, optional MySQL tests
└── cli.py
```

---

## Design decisions

- **Defence in depth, not one check.** Static checks are cheap and certain but can't judge meaning; the LLM
  judge can judge meaning but is fuzzy. Running both, in that order, catches different failure classes.
- **Read-only at two levels.** The guardrail rejects writes, and the connection itself is read-only
  (SQLite `mode=ro`, MySQL `SET SESSION TRANSACTION READ ONLY`), so even a guardrail bug can't modify data.
- **One interface, many engines.** Agents and guardrails talk to a `Database` object; SQLite and MySQL
  differ only in how they list schema, quote names and stop slow queries. The SQL dialect is passed to
  the generator and to the `sqlglot` parser.
- **Fail open on retrieval, fail closed on safety.** If table selection returns nothing usable, all candidates
  are kept (a slower query beats no answer). If a write is detected, the request is blocked with no retry.
- **Precise feedback over blind retries.** Rejections tell the generator exactly what was wrong and what exists
  instead, so retries converge.
- **Fast on remote servers.** MySQL metadata comes from 3 bulk `information_schema` queries, tables are
  profiled 8 at a time, value sampling skips very large tables, and every profiling query has a time limit.
  The finished profile is cached on disk, so reconnecting is instant (tick *Re-read the schema* to refresh).
- **Free to run.** Groq's free tier for the LLM (any Groq chat model works; set `GROQ_MODEL` in `.env`), a local embedding model, SQLite.

## Limitations

- One active database at a time. MySQL row counts are the engine's estimates.
- Value grounding checks `=` comparisons on text; `LIKE`, `IN` and numeric ranges are not yet checked.
- The verifier is itself an LLM and can be wrong; its verdict is a signal, not proof.

## Roadmap

- [x] Live MySQL connections
- [ ] PostgreSQL
- [ ] Evaluation on the Spider benchmark: execution accuracy and the share of wrong queries each layer catches
- [ ] Failure forensics: classify each failure by root cause (retrieval miss, schema ambiguity, generation error)
- [ ] Conversation memory for follow-up questions

## Tech stack

Python · FastAPI · Groq (GPT-OSS 120B) · ChromaDB (ONNX MiniLM embeddings) · sqlglot · SQLite · MySQL (PyMySQL)
