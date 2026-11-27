"""Central settings, read once from .env."""
import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
# "onnx": Chroma's built-in ONNX copy of all-MiniLM-L6-v2 (~150 MB RAM, fits free hosting).
# "sentence-transformers": same model via PyTorch (~850 MB RAM; pip install sentence-transformers first).
EMBED_BACKEND = os.getenv("EMBED_BACKEND", "onnx")
MODEL_DIR = os.getenv("MODEL_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models"))

TOP_K_TABLES = int(os.getenv("TOP_K_TABLES", "5"))   # tables returned by vector search
MAX_ROWS = int(os.getenv("MAX_ROWS", "200"))         # hard cap on rows returned to the user
DATA_DIR = os.getenv("DATA_DIR", "data")
CACHE_TTL_HOURS = float(os.getenv("CACHE_TTL_HOURS", "24"))  # how long a live MySQL schema profile is reused

# Profiling limits: raise them for accuracy on big databases, lower them for speed on slow remote servers
PROFILE_MAX_ROWS = int(os.getenv("PROFILE_MAX_ROWS", "300000"))       # skip value sampling on bigger tables
PROFILE_QUERY_TIMEOUT_S = float(os.getenv("PROFILE_QUERY_TIMEOUT_S", "3"))  # time limit per profiling/value-check query
QUERY_TIMEOUT_S = float(os.getenv("QUERY_TIMEOUT_S", "10"))  # time limit for answering queries

# Public deployment settings (defaults suit running on your own laptop)
DEMO_DB = os.getenv("DEMO_DB", "")                                # SQLite file preloaded for every visitor
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", "30"))               # visitors with their own database kept in memory
ASK_LIMIT_PER_HOUR = int(os.getenv("ASK_LIMIT_PER_HOUR", "0"))    # questions per visitor IP per hour (0 = no limit)
LOAD_LIMIT_PER_HOUR = int(os.getenv("LOAD_LIMIT_PER_HOUR", "0"))  # uploads/connections per IP per hour (0 = no limit)
ALLOW_PRIVATE_HOSTS = os.getenv("ALLOW_PRIVATE_HOSTS", "true").lower() == "true"  # false on a public server
