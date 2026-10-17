"""Procedure-graph traversals in Neo4j. Applicability (applies_if) and dates stay in the engine; the graph
answers the dependency questions. Each function returns None when Neo4j is unavailable so callers fall back."""
from app import graph


def unblocked(applicable: list, done: list):
    """Applicable, not-done procedures whose applicable prerequisites are all done."""
    def q():
        rows = graph.run_read(
            "MATCH (p:Procedure) WHERE p.id IN $applicable AND NOT p.id IN $done "
            "AND NOT EXISTS { MATCH (p)-[:REQUIRES]->(r:Procedure) WHERE r.id IN $applicable AND NOT r.id IN $done } "
            "RETURN p.id AS id", applicable=applicable, done=done)
        return {r["id"] for r in rows}
    return graph.guard(q)


def unlocks(procedure_id: str, applicable: list):
    """Procedures that directly require this one."""
    return graph.guard(lambda: [r["id"] for r in graph.run_read(
        "MATCH (q:Procedure)-[:REQUIRES]->(p:Procedure {id: $pid}) WHERE q.id IN $applicable RETURN q.id AS id ORDER BY q.id",
        pid=procedure_id, applicable=applicable)])


def prerequisites(procedure_id: str, applicable: list):
    """Everything that must be done before this one, nearest first."""
    return graph.guard(lambda: [r["id"] for r in graph.run_read(
        "MATCH path = (p:Procedure {id: $pid})-[:REQUIRES*1..6]->(r:Procedure) "
        "WHERE ALL(n IN nodes(path)[1..] WHERE n.id IN $applicable) "
        "RETURN r.id AS id, min(length(path)) AS depth ORDER BY depth, id",
        pid=procedure_id, applicable=applicable)])
