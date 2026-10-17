"""Long-term conversation memory in the Neo4j Agent Memory Service (hosted, Neo4j Labs).
Fail-open: if the key is missing or the service is slow or down, the bot works exactly as before.
Only the user's own messages are sent, under the opaque tenant id (never the user's name)."""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from dotenv import load_dotenv

from app import store

load_dotenv()
log = logging.getLogger("kiku.nams")
ENABLED = False  # off until the bot calls enable(); tests and scripts never contact the service
SYNC = False  # tests set True so writes happen inline
MIN_CHARS = 12  # shorter messages ("hi", "ok") are not worth remembering
_down_until = 0.0
_conversations: dict = {}
_pool = ThreadPoolExecutor(max_workers=2)


def configured() -> bool:
    key = os.environ.get("NAMS_API_KEY", "").strip()
    return key.startswith("nams_") and "your_key" not in key.lower()


def _request(method: str, path: str, timeout: float = 10, **kw):
    base = os.environ.get("NAMS_BASE_URL", "https://memory.neo4jlabs.com").rstrip("/")
    r = httpx.request(method, base + path, headers={"Authorization": f"Bearer {os.environ['NAMS_API_KEY'].strip()}"},
                      timeout=timeout, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


def enable() -> bool:
    global ENABLED
    if not configured():
        log.info("NAMS_API_KEY not set: long-term conversation memory is off")
        ENABLED = False
        return False
    try:
        _request("GET", "/v1/conversations", timeout=8, params={"limit": 1})
        ENABLED = True
    except Exception as e:
        log.warning("memory service unavailable, continuing without it: %s", type(e).__name__)
        ENABLED = False
    return ENABLED


def available() -> bool:
    return ENABLED and time.time() >= _down_until


def guard(fn, default=None):
    global _down_until
    if not available():
        return default
    try:
        return fn()
    except Exception as e:
        _down_until = time.time() + 30
        log.warning("memory service error, pausing it for 30 s: %s", type(e).__name__)
        return default


def _conversation_id(uid: str) -> str:
    cid = _conversations.get(uid)
    if cid:
        return cid
    rows = _request("GET", "/v1/conversations", params={"userId": uid, "limit": 20}).get("conversations", [])
    if rows:
        cid = max(rows, key=lambda c: c.get("updatedAt") or c.get("createdAt") or "")["id"]
    else:
        cid = _request("POST", "/v1/conversations", json={"userId": uid, "metadata": {"channel": "telegram"}})["id"]
    _conversations[uid] = cid
    return cid


def remember_message(chat_id, text: str):
    """Store one user message in the background. Never blocks or breaks a reply."""
    text = (text or "").strip()
    if not available() or len(text) < MIN_CHARS:
        return
    uid = store.tenant_id(chat_id)

    def write():
        guard(lambda: _request("POST", f"/v1/conversations/{_conversation_id(uid)}/messages",
                               json={"role": "user", "content": text[:2000], "metadata": {"channel": "telegram"}}))
    write() if SYNC else _pool.submit(write)


def recall(chat_id, query: str, exclude=(), k: int = 4) -> list:
    """Earlier things this user said that relate to `query`, as short dated lines. Empty if unavailable or slow."""
    query = (query or "").strip()
    if not available() or len(query) < 8:
        return []
    uid = store.tenant_id(chat_id)

    def q():
        res = _request("POST", "/v1/messages/search", timeout=4, json={"query": query[:500], "userId": uid, "limit": k + 6})
        seen = {e.strip() for e in exclude} | {query}
        out = []
        for m in res.get("messages", []):
            c = (m.get("content") or "").strip()
            if m.get("role") != "user" or not c or c in seen:
                continue
            seen.add(c)
            out.append(f"[{(m.get('createdAt') or '')[:10]}] {c[:200]}")
            if len(out) >= k:
                break
        return out
    return guard(q, [])


def forget(chat_id):
    """True if the user's memory was deleted, False if that failed, None if the service is not in use."""
    if not ENABLED:
        return None
    uid = store.tenant_id(chat_id)
    _conversations.pop(uid, None)

    def q():
        _request("DELETE", "/v1/memory", params={"userId": uid})
        return True
    return guard(q, False)
