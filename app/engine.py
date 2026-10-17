import json
import os
from datetime import date, timedelta
from graphlib import TopologicalSorter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
REQUIRED_FIELDS = [
    "ward", "visa_category", "age", "moving_from", "move_in_date",
    "employer_social_insurance", "has_residence_card", "has_school_age_children",
]
KIND_ORDER = {"required": 0, "bundled": 1, "action": 2, "info": 3, "optional": 4}


def load_procedures(path=None) -> dict:
    with open(path or DATA / "procedures.json", encoding="utf-8") as f:
        return {p["id"]: p for p in json.load(f)["procedures"]}


def load_meta(path=None) -> dict:
    with open(path or DATA / "procedures.json", encoding="utf-8") as f:
        return json.load(f)["meta"]


def local_ward_names() -> str:
    """Wards that have office-level detail, from the data file (the names listed in the data file)."""
    return ", ".join(load_meta().get("local_wards", [])) or "no ward"


def load_personas(path=None) -> list:
    with open(path or DATA / "personas.json", encoding="utf-8") as f:
        return json.load(f)


def ref_date(ref=None) -> date:
    if ref:
        return ref if isinstance(ref, date) else date.fromisoformat(ref)
    env = os.environ.get("KIKU_REF_DATE")
    return date.fromisoformat(env) if env else date.today()


def ward_key(ward) -> str:
    w = (ward or "").strip().lower()
    for s in ("-ku", " ku", " ward", " city", "区", "市"):
        w = w.replace(s, "")
    return w.strip()


def has_local_data(profile: dict) -> bool:
    return ward_key(profile.get("ward")) in set(load_meta().get("local_ward_aliases", []))


def missing_fields(profile: dict) -> list:
    return [f for f in REQUIRED_FIELDS if profile.get(f) is None]


def _ok(c: dict, profile: dict, ref: date) -> bool:
    v = profile.get(c["field"])
    if "eq" in c and v != c["eq"]:
        return False
    if "in" in c and v not in c["in"]:
        return False
    if "set" in c and (v is not None) != c["set"]:
        return False
    if "min" in c and (v is None or v < c["min"]):
        return False
    if "max" in c and (v is None or v > c["max"]):
        return False
    if "days_until_max" in c and (v is None or (date.fromisoformat(v) - ref).days > c["days_until_max"]):
        return False
    return True


def _date_from(rule, profile):
    if not rule or not profile.get(rule["anchor"]):
        return None
    return date.fromisoformat(profile[rule["anchor"]]) + timedelta(days=rule["days"])


def plan(profile: dict, ref=None, done=(), procs=None) -> list:
    """Applicable procedures in prerequisite order. Prerequisites that don't apply to this profile are dropped."""
    ref, procs = ref_date(ref), procs or load_procedures()
    local = has_local_data(profile)
    missing = missing_fields(profile)
    if missing:
        raise ValueError(f"profile incomplete, missing: {missing}")
    applicable = {i for i, p in procs.items() if all(_ok(c, profile, ref) for c in p["applies_if"])}
    deps = {i: [q for q in procs[i]["prerequisites"] if q in applicable] for i in applicable}
    done = set(done) & applicable
    done |= {i for i in applicable if procs[i].get("bundled_with") in done}

    def sort_key(i):
        return (_date_from(procs[i]["deadline"], profile) or date.max, KIND_ORDER[procs[i]["kind"]], i)

    ts = TopologicalSorter(deps)
    ts.prepare()
    out, pool = [], []
    while ts.is_active():
        pool += ts.get_ready()
        i = min(pool, key=sort_key)
        pool.remove(i)
        p = procs[i]
        due, opens = _date_from(p["deadline"], profile), _date_from(p.get("opens"), profile)
        blocked_by = [q for q in deps[i] if q not in done]
        window_open = None if opens is None else ref >= opens
        out.append({
            "id": i, "name_en": p["name_en"], "name_ja": p["name_ja"],
            "office": p["office"] if local else p["office_generic"], "local_data": local,
            "kind": p["kind"], "prerequisites": deps[i], "documents": p["documents"],
            "phrase_ja": p["phrase_ja"], "romaji": p["romaji"], "source_url": p["source_url"],
            "status": p["status"], "confidence": p["confidence"], "notes": p["notes"],
            "due": due.isoformat() if due else None,
            "days_left": (due - ref).days if due else None,
            "opens_on": opens.isoformat() if opens else None,
            "window_open": window_open,
            "done": i in done, "blocked_by": blocked_by,
            "actionable": i not in done and not blocked_by and window_open is not False,
        })
        ts.done(i)
    return out


def next_actions(profile: dict, done=(), ref=None, procs=None) -> list:
    return [p for p in plan(profile, ref, done, procs) if p["actionable"]]
