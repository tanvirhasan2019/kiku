import logging
import os
import time

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()
log = logging.getLogger("kiku.graph")

ENABLED = False  # off by default so tests and scripts never touch the database; the bot calls enable()
_driver = None
_down_until = 0.0


def driver():
    global _driver
    if _driver is None:
        user = os.environ.get("NEO4J_USER") or os.environ["NEO4J_USERNAME"]  # Aura's credentials file uses NEO4J_USERNAME
        _driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(user, os.environ["NEO4J_PASSWORD"]))
    return _driver


def _session():
    return driver().session(database=os.environ.get("NEO4J_DATABASE") or None)


def run(cypher: str, **params) -> list[dict]:
    with _session() as s:
        return [r.data() for r in s.run(cypher, **params)]


def run_read(cypher: str, **params) -> list[dict]:
    with _session() as s:
        return s.execute_read(lambda tx: [r.data() for r in tx.run(cypher, **params)])


def reset_reference():
    """Clear only procedure reference data (edges and offices). Never touches User nodes or their progress."""
    run("MATCH (:Procedure)-[r:REQUIRES|AT]->() DELETE r")
    run("MATCH (o:Office) DETACH DELETE o")


def enable() -> bool:
    global ENABLED
    try:
        driver().verify_connectivity()
        ENABLED = True
    except Exception as e:
        log.warning("Neo4j unavailable, running from memory and the engine: %s", type(e).__name__)
        ENABLED = False
    return ENABLED


def available() -> bool:
    return ENABLED and time.time() >= _down_until


def guard(fn, default=None):
    """Run a Neo4j call; on failure pause Neo4j use for 30 s and return `default` so the bot keeps answering."""
    global _down_until
    if not available():
        return default
    try:
        return fn()
    except Exception as e:
        _down_until = time.time() + 30
        log.warning("Neo4j error, falling back for 30 s: %s", type(e).__name__)
        return default
