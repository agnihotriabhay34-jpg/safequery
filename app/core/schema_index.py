"""RAG over the schema: describe each table once, embed it, and retrieve the tables relevant to a question."""
import uuid

import chromadb
from chromadb.utils import embedding_functions

from app import config
from app.core import llm
from app.core.db import Table

DESCRIBE_SYSTEM = """You document database tables for a text-to-SQL system.
For each table you are given, write ONE short sentence describing what a row represents
and what questions the table helps answer. Use the column names and the sample row.
Respond in JSON: {"descriptions": {"<table name>": "<one sentence>", ...}}"""


def describe_tables(tables: dict[str, Table], batch_size: int = 15) -> None:
    """Fill Table.description using the LLM, a batch of tables per call."""
    names = [n for n, t in tables.items() if t.row_count > 0]  # empty tables: nothing to describe
    for i in range(0, len(names), batch_size):
        chunk = [tables[n] for n in names[i:i + batch_size]]
        prompt = "\n\n".join(t.to_prompt() for t in chunk)
        try:
            result = llm.chat_json(DESCRIBE_SYSTEM, prompt).get("descriptions", {})
        except Exception:
            result = {}  # descriptions are a bonus; retrieval still works on names + columns
        for t in chunk:
            t.description = str(result.get(t.name, "")).strip()


def _document(t: Table) -> str:
    """The text that gets embedded for each table."""
    return f"{t.name}. {t.description} Columns: {', '.join(t.column_names)}"


_embedder = None
_client = None


def _get_embedder():
    """Load the embedding model once per process (it takes a few seconds)."""
    global _embedder
    if _embedder is None:
        if config.EMBED_BACKEND == "sentence-transformers":
            _embedder = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=config.EMBED_MODEL)
        else:
            from pathlib import Path

            from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
            # keep the model inside the project folder, so a host that builds once and runs elsewhere
            # (e.g. Render) finds the copy downloaded at build time
            ONNXMiniLM_L6_V2.DOWNLOAD_PATH = Path(config.MODEL_DIR) / "all-MiniLM-L6-v2"
            _embedder = ONNXMiniLM_L6_V2()
    return _embedder


def _get_client():
    global _client
    if _client is None:
        _client = chromadb.EphemeralClient()
    return _client


class SchemaIndex:
    def __init__(self, tables: dict[str, Table], embedding_function=None):
        self.tables = tables
        ef = embedding_function or _get_embedder()
        client = _get_client()
        # unique name: several visitors can load the same database at once without clashing
        self.name = f"schema_{uuid.uuid4().hex}"
        self.collection = client.create_collection(
            name=self.name, embedding_function=ef, metadata={"hnsw:space": "cosine"}
        )
        self.collection.add(ids=list(tables), documents=[_document(t) for t in tables.values()])

    def retrieve(self, question: str, k: int | None = None) -> list[str]:
        """Top-k tables by similarity, plus their foreign-key neighbours (needed for joins)."""
        k = min(k or config.TOP_K_TABLES, len(self.tables))
        hits = self.collection.query(query_texts=[question], n_results=k)["ids"][0]
        result = list(hits)
        for name in hits:
            for _, ref_table, _ in self.tables[name].foreign_keys:
                if ref_table in self.tables and ref_table not in result:
                    result.append(ref_table)
            # tables that point AT this one (e.g. order_items -> orders)
            for other in self.tables.values():
                if other.name not in result and any(rt == name for _, rt, _ in other.foreign_keys):
                    result.append(other.name)
        return result

    def close(self) -> None:
        """Free the vector collection when this database is unloaded."""
        try:
            _get_client().delete_collection(self.name)
        except Exception:
            pass
