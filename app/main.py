"""FastAPI backend: upload a SQLite file or connect to a live MySQL database, then ask questions.

Each visitor gets their own session (an id the page sends in the X-Session-Id header), so two
people using a public deployment never see each other's database. Visitors who haven't loaded
anything use the shared, read-only demo database (DEMO_DB), if one is configured.
"""
import ipaddress
import os
import re
import socket
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.core.db import MySQLDatabase, SQLiteDatabase
from app.pipeline import SafeQuery

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
UPLOAD_DIR = os.path.join(config.DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DEMO_EXAMPLES = [
    "Which region had the highest revenue from delivered orders?",
    "What is the most common return reason for Electronics products?",
    "How many payments were made with PhonePe in 2025?",
    "Which manager has the most employees reporting to them?",
    "Delete all cancelled orders",
]


# ---------------------------------------------------------------- sessions
class Sessions:
    """Each visitor's own loaded database, oldest dropped first when there are too many."""

    def __init__(self, limit: int):
        self.limit = limit
        self.items: OrderedDict[str, tuple[SafeQuery, str | None]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, sid: str) -> SafeQuery | None:
        with self.lock:
            if sid in self.items:
                self.items.move_to_end(sid)
                return self.items[sid][0]
        return None

    def put(self, sid: str, engine: SafeQuery, upload_path: str | None = None) -> None:
        with self.lock:
            if sid in self.items:
                self._drop(*self.items.pop(sid))
            self.items[sid] = (engine, upload_path)
            while len(self.items) > self.limit:
                _, old = self.items.popitem(last=False)
                self._drop(*old)

    @staticmethod
    def _drop(engine: SafeQuery, upload_path: str | None) -> None:
        engine.close()
        if upload_path:
            try:
                os.remove(upload_path)
            except OSError:
                pass


sessions = Sessions(config.MAX_SESSIONS)
demo = {"engine": None, "error": None, "loading": False}


def _load_demo() -> None:
    demo["loading"] = True
    try:
        demo["engine"] = SafeQuery(SQLiteDatabase(config.DEMO_DB))
        print(f"[demo] {config.DEMO_DB} ready", flush=True)
    except Exception as e:  # the app still works without a demo; visitors just load their own database
        demo["error"] = str(e)
        print(f"[demo] could not load {config.DEMO_DB}: {e}", flush=True)
    finally:
        demo["loading"] = False


@asynccontextmanager
async def lifespan(_app):
    if config.DEMO_DB:
        # load in the background so the server answers immediately while the demo warms up
        threading.Thread(target=_load_demo, daemon=True).start()
    yield


app = FastAPI(title="SafeQuery", description="Multi-agent text-to-SQL with hallucination detection",
              lifespan=lifespan)


# ---------------------------------------------------------------- rate limits
class RateLimiter:
    def __init__(self, limit_per_hour: int, what: str):
        self.limit, self.what = limit_per_hour, what
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.limit <= 0:
            return
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.limit:
                wait = int((3600 - (now - q[0])) / 60) + 1
                raise HTTPException(429, f"This demo allows {self.limit} {self.what} per hour per visitor, "
                                         f"to stay inside the free LLM quota. Try again in about {wait} min.")
            q.append(now)


ask_limit = RateLimiter(config.ASK_LIMIT_PER_HOUR, "questions")
load_limit = RateLimiter(config.LOAD_LIMIT_PER_HOUR, "database loads")


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")


def _session_id(request: Request) -> str:
    sid = request.headers.get("x-session-id", "")
    return sid if re.fullmatch(r"[A-Za-z0-9-]{8,64}", sid) else "anonymous"


def _summary(engine: SafeQuery, is_demo: bool) -> dict:
    data = {"loaded": True, "demo": is_demo, **engine.summary()}
    if is_demo:
        data["examples"] = DEMO_EXAMPLES
    return data


def _check_public_host(host: str) -> None:
    """On a public server, refuse private/internal addresses so the app can't be used to probe its own network."""
    if config.ALLOW_PRIVATE_HOSTS:
        return
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        raise HTTPException(400, f"Could not find a server called '{host}'. Check the host name.")
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise HTTPException(400, "This public demo can only connect to databases on the public internet. "
                                     "Run SafeQuery on your own computer to connect to a local or private database.")


# ---------------------------------------------------------------- routes
class AskRequest(BaseModel):
    question: str = Field(max_length=1000)


class MySQLRequest(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(3306, ge=1, le=65535)
    user: str
    password: str = ""
    database: str
    refresh: bool = False  # ignore the cached schema profile and re-read the database


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/status")
def status(request: Request):
    engine = sessions.get(_session_id(request))
    if engine:
        return _summary(engine, is_demo=False)
    if demo["engine"]:
        return _summary(demo["engine"], is_demo=True)
    return {"loaded": False, "demo_loading": demo["loading"]}


@app.post("/api/upload")
def upload(request: Request, file: UploadFile = File(...)):
    load_limit.check(_client_ip(request))
    original = os.path.basename(file.filename or "database.db")
    if not original.lower().endswith((".db", ".sqlite", ".sqlite3")):
        raise HTTPException(400, "Upload a SQLite file ending in .db, .sqlite or .sqlite3.")
    max_bytes = int(config.MAX_UPLOAD_MB * 1024 * 1024)
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"That file is over the {config.MAX_UPLOAD_MB:g} MB upload limit.")
    if not data.startswith(b"SQLite format 3\x00"):
        raise HTTPException(400, "This file is not a valid SQLite database.")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", original)
    path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex[:12]}_{safe}")  # unique per upload: visitors never collide
    with open(path, "wb") as f:
        f.write(data)
    try:
        db = SQLiteDatabase(path)
        db.display_name = safe
        engine = SafeQuery(db)
    except Exception as e:
        os.remove(path)
        raise HTTPException(400, f"Could not load the database: {e}")
    sessions.put(_session_id(request), engine, upload_path=path)
    return _summary(engine, is_demo=False)


@app.post("/api/connect/mysql")
def connect_mysql(req: MySQLRequest, request: Request):
    load_limit.check(_client_ip(request))
    _check_public_host(req.host)
    try:
        db = MySQLDatabase(req.host, req.user, req.password, req.database, req.port)
        engine = SafeQuery(db, refresh=req.refresh)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Could not connect to MySQL: {e}")  # pymysql errors never include the password
    sessions.put(_session_id(request), engine)
    return _summary(engine, is_demo=False)


@app.post("/api/ask")
def ask(req: AskRequest, request: Request):
    engine = sessions.get(_session_id(request)) or demo["engine"]
    if engine is None:
        raise HTTPException(400, "Load a database first." if not demo["loading"]
                            else "The demo database is still loading. Try again in a few seconds.")
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Type a question first.")
    ask_limit.check(_client_ip(request))
    try:
        return engine.ask(question)
    except Exception as e:
        raise HTTPException(500, f"Something went wrong while answering: {e}")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
