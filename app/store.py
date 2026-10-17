"""Per-tenant persistence. One tenant = one chat. Every query is parameterised by the tenant id that
code derives from the Telegram chat id; the model never supplies it."""
from concurrent.futures import ThreadPoolExecutor

from app import graph

PREFIX = "tg:"
SYNC = False  # tests set True so a write is finished before the next read
_writer = ThreadPoolExecutor(max_workers=1)  # a single worker keeps every tenant's writes in order
_META = {"tenant_id", "updated_at"}


def tenant_id(chat_id) -> str:
    return f"{PREFIX}{chat_id}"


def _write(fn):
    job = lambda: graph.guard(fn)
    job() if SYNC else _writer.submit(job)


def init_schema():
    graph.guard(lambda: graph.run("CREATE CONSTRAINT user_tenant IF NOT EXISTS FOR (u:User) REQUIRE u.tenant_id IS UNIQUE"))


def load(chat_id):
    def q():
        rows = graph.run_read(
            "MATCH (u:User {tenant_id: $t}) OPTIONAL MATCH (u)-[:COMPLETED]->(p:Procedure) "
            "RETURN properties(u) AS props, collect(p.id) AS done", t=tenant_id(chat_id))
        if not rows:
            return None
        props = rows[0]["props"]
        return {"profile": {k: v for k, v in props.items() if k not in _META and not k.startswith("vault_")},
                "done": set(rows[0]["done"]),
                "vault": {k: v for k, v in props.items() if k.startswith("vault_")}}
    return graph.guard(q)


def _copy(profile: dict) -> dict:  # snapshot now: the caller keeps mutating the live dict
    return {k: (list(v) if isinstance(v, list) else v) for k, v in profile.items()}


def save_profile(chat_id, profile: dict):
    t, props = tenant_id(chat_id), _copy(profile)
    _write(lambda: graph.run("MERGE (u:User {tenant_id: $t}) SET u += $props, u.updated_at = datetime()", t=t, props=props))


def save_vault(chat_id, tokens: dict):
    t, props = tenant_id(chat_id), dict(tokens)
    _write(lambda: graph.run("MERGE (u:User {tenant_id: $t}) SET u += $props, u.updated_at = datetime()", t=t, props=props))


def add_done(chat_id, procedure_id: str):
    t = tenant_id(chat_id)
    _write(lambda: graph.run(
        "MERGE (u:User {tenant_id: $t}) WITH u MATCH (p:Procedure {id: $pid}) "
        "MERGE (u)-[c:COMPLETED]->(p) ON CREATE SET c.at = datetime()", t=t, pid=procedure_id))


def replace(chat_id, profile: dict, done: set):
    t, profile, done = tenant_id(chat_id), _copy(profile), sorted(done)

    def q():
        graph.run("MERGE (u:User {tenant_id: $t}) SET u = $props, u.updated_at = datetime() "
                  "WITH u OPTIONAL MATCH (u)-[c:COMPLETED]->() DELETE c", t=t, props={"tenant_id": t, **profile})
        graph.run("MATCH (u:User {tenant_id: $t}) UNWIND $done AS pid MATCH (p:Procedure {id: pid}) "
                  "MERGE (u)-[c:COMPLETED]->(p) ON CREATE SET c.at = datetime()", t=t, done=done)
    _write(q)


def forget(chat_id):
    """True if the tenant's data was deleted from Neo4j, False if that failed, None if Neo4j is not in use."""
    if not graph.ENABLED:
        return None

    def q():
        graph.run("MATCH (u:User {tenant_id: $t}) DETACH DELETE u", t=tenant_id(chat_id))
        return True
    job = lambda: graph.guard(q, default=False)
    return job() if SYNC else _writer.submit(job).result(timeout=30)  # queued behind pending writes
