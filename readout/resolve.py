"""Sponsor resolution: turn about 11,000 sponsor name strings into organizations.

Two layers, both closed with union-find:
  entity  spellings of one company ("Pfizer", "Pfizer Inc.", "Pfizer Japan Inc."), merged by pair rules
  group   entities under one corporate parent, from clauses the sponsors wrote into their own names
          ("Seagen, a wholly owned subsidiary of Pfizer", "Kite, A Gilead Company", "X d/b/a Y")

Entity pair rules (any one merges two base names):
  exact      equal normalized names (legal suffixes such as "Inc." or "Co., Ltd." removed)
  core       equal distinctive cores with compatible industry words ("Arcus Biosciences" ~ "Arcus Bioscience")
  affiliate  one core is the other plus country or unit words ("Pfizer Japan" ~ "Pfizer")
  fuzzy      token-sort ratio of the cores at least FUZZY_MIN, same first three letters, compatible words
Candidate pairs come from blocking (same normalized name, same core, same first core word, or a character
3-gram TF-IDF nearest neighbor), so the rules never compare all 60 million pairs.
No hand-made alias list is used, which keeps the hand-labeled evaluation independent of the resolver.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

import numpy as np
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer

from . import clauses, config
from .linking import compatible
from .names import AFFILIATE, core_tokens, industry_words, normalize

FUZZY_MIN = 92
NEIGHBORS = 10
MIN_COSINE = 0.5
MAX_BLOCK = 60
OUT = config.RESULTS / "resolution"


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def add(self) -> int:
        self.parent.append(len(self.parent))
        return len(self.parent) - 1

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[max(ra, rb)] = min(ra, rb)
        return True


def profile(name: str) -> dict:
    return {"name": name, "norm": normalize(name), "core": tuple(core_tokens(name)),
            "words": frozenset(industry_words(name))}


def pair_rule(a: dict, b: dict) -> str | None:
    """Which rule (if any) says profiles a and b are the same company. Pure function, easy to test."""
    if not a["norm"] or not b["norm"]:
        return None
    if a["norm"] == b["norm"]:
        return "exact"
    if not compatible(a["words"], b["words"]):
        return None
    if a["core"] == b["core"] and len(" ".join(a["core"])) >= 4:
        return "core"
    short, long_ = (a, b) if len(a["core"]) <= len(b["core"]) else (b, a)
    k = len(short["core"])
    if k and len(" ".join(short["core"])) >= 4 and long_["core"][:k] == short["core"] \
            and all(t in AFFILIATE for t in long_["core"][k:]):
        return "affiliate"
    ca, cb = " ".join(a["core"]), " ".join(b["core"])
    if min(len(ca), len(cb)) >= 5 and ca[:3] == cb[:3] and fuzz.token_sort_ratio(ca, cb) >= FUZZY_MIN:
        return "fuzzy"
    return None


def neighbors(texts: list[str], k: int = NEIGHBORS, chunk: int = 1000) -> list[list[tuple[int, float]]]:
    """Top-k cosine neighbors over character 3-gram TF-IDF vectors, computed in chunks to keep memory low."""
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 3), lowercase=False)
    m = vec.fit_transform(texts)
    out: list[list[tuple[int, float]]] = []
    k = min(k, m.shape[0] - 1)
    if k < 1:
        return [[] for _ in range(m.shape[0])]
    for start in range(0, m.shape[0], chunk):
        sims = (m[start:start + chunk] @ m.T).toarray()
        for row, i in enumerate(range(start, min(start + chunk, m.shape[0]))):
            sims[row, i] = 0.0
            top = np.argpartition(-sims[row], k)[:k]
            out.append(sorted(((int(j), float(sims[row, j])) for j in top if sims[row, j] > 0), key=lambda x: -x[1]))
    return out


def resolve(names: list[str], trial_counts: dict[str, int]) -> dict:
    n = len(names)
    parsed = [clauses.parse(x) for x in names]
    prof = [profile(p.base) for p in parsed]
    entity = UnionFind(n)
    rule_of_pair: dict[tuple[int, int], str] = {}

    # blocking
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(prof):
        blocks["n:" + p["norm"]].append(i)
        blocks["c:" + " ".join(p["core"])].append(i)
        if p["core"] and len(p["core"][0]) >= 4:
            blocks["f:" + p["core"][0]].append(i)
    pairs: set[tuple[int, int]] = set()
    for members in blocks.values():
        if 1 < len(members) <= MAX_BLOCK:
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    pairs.add((min(members[x], members[y]), max(members[x], members[y])))
    nn = neighbors([p["norm"] or p["name"].lower() for p in prof])
    for i, row in enumerate(nn):
        for j, cos in row:
            if cos >= MIN_COSINE:
                pairs.add((min(i, j), max(i, j)))
    for i, j in sorted(pairs):
        rule = pair_rule(prof[i], prof[j])
        if rule:
            rule_of_pair[(i, j)] = rule
            entity.union(i, j)

    # lookups for clause targets (parents and "dba" names) against the base names
    by_norm: dict[str, list[int]] = defaultdict(list)
    by_core: dict[tuple, list[int]] = defaultdict(list)
    for i, p in enumerate(prof):
        by_norm[p["norm"]].append(i)
        by_core[p["core"]].append(i)

    def find_node(text: str) -> int | None:
        p = profile(text)
        hits = by_norm.get(p["norm"]) or [i for i in by_core.get(p["core"], []) if compatible(p["words"], prof[i]["words"])]
        if not hits and p["core"]:  # the parent written as a longer or shorter form ("GSK plc" ~ "GSK")
            for k in range(len(p["core"]) - 1, 0, -1):
                hits = [i for i in by_core.get(p["core"][:k], []) if all(t in AFFILIATE for t in p["core"][k:])]
                if hits:
                    break
        return min(hits, key=lambda i: (-trial_counts.get(names[i], 0), i)) if hits else None

    for i, p in enumerate(parsed):
        for alias in p.aliases:
            j = find_node(alias)
            if j is not None and entity.union(i, j):
                rule_of_pair[(min(i, j), max(i, j))] = "aka_clause"

    # group layer: entity components plus parent links; parents never seen as sponsors become virtual nodes
    group = UnionFind(n)
    for i in range(n):
        group.union(i, entity.find(i))
    virtual: dict[str, int] = {}
    virtual_names: dict[int, str] = {}
    parent_links = 0
    children_of: dict[int, set[int]] = defaultdict(set)  # for each parent node, its child nodes (entity roots)
    is_child: set[int] = set()
    for i, p in enumerate(parsed):
        child = i
        for parent_text in p.parents:
            node = find_node(parent_text)
            if node is None:
                key = normalize(parent_text)
                if key not in virtual:
                    virtual[key] = group.add()
                    virtual_names[virtual[key]] = parent_text
                node = virtual[key]
            if group.union(child, node):
                parent_links += 1
            children_of[entity.find(node) if node < n else node].add(entity.find(child) if child < n else child)
            is_child.add(entity.find(child) if child < n else child)
            if node < n:
                rule_of_pair[(min(child, node), max(child, node))] = "parent_clause"
            child = node

    members: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        members[group.find(i)].append(i)
    entity_trials: dict[int, int] = Counter()
    for i in range(n):
        entity_trials[entity.find(i)] += trial_counts.get(names[i], 0)
    tops_by_group: dict[int, list[int]] = defaultdict(list)
    for node in children_of:
        if node not in is_child:  # an ultimate parent: named as a parent, never as a child
            tops_by_group[group.find(node)].append(node)
    orgs, org_names = [], []
    for root, m in members.items():
        m.sort(key=lambda i: (-trial_counts.get(names[i], 0), names[i]))
        tops = tops_by_group.get(root)
        if tops:  # name the group after its ultimate parent, e.g. "Merck & Co., Inc." rather than a unit
            top = max(tops, key=lambda t: (len(children_of[t]), entity_trials.get(t, 0), -t))
            if top < n:
                top_members = [i for i in m if entity.find(i) == top]
                label = names[top_members[0]] if top_members else names[m[0]]
            else:
                label = virtual_names[top]
        else:
            label = names[m[0]]
        orgs.append(m)
        org_names.append(label)
    order = sorted(range(len(orgs)), key=lambda k: org_names[k].lower())
    orgs = [orgs[k] for k in order]
    org_names = [org_names[k] for k in order]
    entity_of = {i: entity.find(i) for i in range(n)}
    return {"parsed": parsed, "orgs": orgs, "org_names": org_names, "entity_of": entity_of,
            "rule_of_pair": rule_of_pair,
            "neighbors": nn, "candidate_pairs": len(pairs), "parent_links": parent_links,
            "virtual_parents": len(virtual)}


def run() -> dict:
    from .db import connect
    from .linking import sponsor_names

    con = connect()
    names = sponsor_names(con)
    counts = dict(con.execute("""
        SELECT name, count(DISTINCT nct_id) FROM (
            SELECT trim(lead_sponsor_name) AS name, nct_id FROM raw.ct_studies
            UNION ALL SELECT trim(name), nct_id FROM raw.ct_collaborators WHERE class = 'INDUSTRY')
        GROUP BY name""").fetchall())
    res = resolve(names, counts)
    rows = []
    for org_id, m in enumerate(res["orgs"], 1):
        canonical = res["org_names"][org_id - 1]
        for i in m:
            p = res["parsed"][i]
            rows.append((names[i], org_id, canonical, len(m), p.base, p.clause or "none",
                         "; ".join(p.parents) or None))
    con.execute("DROP TABLE IF EXISTS raw.sponsor_organizations")
    con.execute("CREATE TABLE raw.sponsor_organizations (sponsor_name VARCHAR, org_id INTEGER, org_name VARCHAR, "
                "n_name_variants INTEGER, base_name VARCHAR, name_clause VARCHAR, stated_parents VARCHAR)")
    con.executemany("INSERT INTO raw.sponsor_organizations VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.close()
    multi = [m for m in res["orgs"] if len(m) > 1]
    summary = {
        "sponsor_names": len(names),
        "organizations": len(res["orgs"]),
        "names_merged_away": len(names) - len(res["orgs"]),
        "organizations_with_several_names": len(multi),
        "names_in_multi_name_organizations": sum(len(m) for m in multi),
        "names_with_ownership_or_alias_clause": sum(1 for p in res["parsed"] if p.clause),
        "parent_links": res["parent_links"],
        "parents_not_seen_as_sponsors": res["virtual_parents"],
        "candidate_pairs_compared": res["candidate_pairs"],
        "merging_pairs_by_rule": dict(Counter(res["rule_of_pair"].values())),
        "largest_organizations": [
            {"organization": res["org_names"][k], "name_variants": len(m),
             "trials": sum(counts.get(names[i], 0) for i in m)}
            for k, m in sorted(enumerate(res["orgs"]), key=lambda km: (-len(km[1]), res["org_names"][km[0]]))[:12]
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    state = {
        "names": names,
        "org_of": {names[i]: k for k, m in enumerate(res["orgs"]) for i in m},
        "org_names": res["org_names"],
        "entity_of": {names[i]: int(res["entity_of"][i]) for i in range(len(names))},
        "neighbors": res["neighbors"],
        "rule_of_pair": {f"{i},{j}": r for (i, j), r in res["rule_of_pair"].items()},
        "trial_counts": counts,
    }
    config.CACHE.mkdir(parents=True, exist_ok=True)
    (config.CACHE / "resolution_state.json").write_text(json.dumps(state))
    return summary
