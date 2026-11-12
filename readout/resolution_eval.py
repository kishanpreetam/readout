"""Evaluate sponsor resolution against the gold labels in labels/.

Two label files (see docs/labeling.md for exactly how they were made):
  labels/resolution_gold_groups.csv   every sponsor name of the 20 corporate groups that lead the most studies,
                                      found by keyword search and judged against written rules (member, NONE or UNSURE)
  labels/resolution_pair_audit.csv    seeded random pairs outside those groups: pairs the resolver merged
                                      (precision audit) and similar pairs it did not merge (miss audit)
"""

from __future__ import annotations

import csv
import itertools
import json
import random
from collections import defaultdict

from . import clauses, config
from .db import connect
from .names import core_tokens

GOLD = config.LABELS / "resolution_gold_groups.csv"
AUDIT = config.LABELS / "resolution_pair_audit.csv"
OUT = config.RESULTS / "resolution"
SEED = 11
AUDIT_MERGED = 60
AUDIT_NEAR_MISS = 60
NEAR_MISS_COSINE = 0.6


def _predictions() -> tuple[dict[str, int], dict[int, list[str]], dict[int, str], dict[str, int]]:
    con = connect(read_only=True)
    rows = con.execute("SELECT sponsor_name, org_id, org_name FROM raw.sponsor_organizations").fetchall()
    con.close()
    state = json.loads((config.CACHE / "resolution_state.json").read_text())
    org_of = {s: o for s, o, _ in rows}
    members: dict[int, list[str]] = defaultdict(list)
    for s, o, _ in rows:
        members[o].append(s)
    return org_of, members, {o: n for _, o, n in rows}, state["trial_counts"]


def _gold() -> dict[str, str]:
    with GOLD.open() as fh:
        return {r["sponsor_name"]: r["gold_group"] for r in csv.DictReader(fh)}


def gold_group_metrics() -> dict:
    org_of, members, org_name, trials = _predictions()
    gold = _gold()
    missing = [n for n in gold if n not in org_of]
    groups: dict[str, list[str]] = defaultdict(list)
    for n, g in gold.items():
        if g not in ("NONE", "UNSURE") and n in org_of:
            groups[g].append(n)
    per_group, unlabeled_in_clusters = [], set()
    tot = defaultdict(float)
    for g, names in sorted(groups.items()):
        weight = defaultdict(int)
        for n in names:
            weight[org_of[n]] += trials.get(n, 0)
        main = max(weight, key=lambda o: (weight[o], -o))
        predicted = [n for n in members[main] if gold.get(n) != "UNSURE"]
        unlabeled_in_clusters.update(n for n in predicted if n not in gold)
        hit = [n for n in names if org_of[n] == main]
        wrong = [n for n in predicted if n in gold and gold[n] != g]
        g_trials = sum(trials.get(n, 0) for n in names)
        h_trials = sum(trials.get(n, 0) for n in hit)
        row = {
            "group": g, "predicted_name": org_name[main], "gold_names": len(names), "predicted_names": len(predicted),
            "correct_names": len(hit), "wrongly_merged_names": wrong,
            "name_recall": round(len(hit) / len(names), 3),
            "name_precision": round(len(hit) / len(predicted), 3) if predicted else 0.0,
            "trial_recall": round(h_trials / g_trials, 3) if g_trials else 0.0,
            "missed_names": sorted(set(names) - set(hit)),
        }
        per_group.append(row)
        tot["gold"] += len(names)
        tot["pred"] += len(predicted)
        tot["hit"] += len(hit)
        tot["gold_trials"] += g_trials
        tot["hit_trials"] += h_trials
    # recall by kind of name, to separate spelling variants from differently named subsidiaries
    kinds = defaultdict(lambda: [0, 0])
    for g, names in groups.items():
        head = max(names, key=lambda n: (trials.get(n, 0), n))
        head_token = (core_tokens(clauses.parse(head).base) or [""])[0]
        weight = defaultdict(int)
        for n in names:
            weight[org_of[n]] += trials.get(n, 0)
        main = max(weight, key=lambda o: (weight[o], -o))
        for n in names:
            parsed = clauses.parse(n)
            if parsed.parents:
                kind = "states_its_parent"
            elif (core_tokens(parsed.base) or [""])[0] == head_token:
                kind = "spelling_or_unit_variant"
            else:
                kind = "separately_named_subsidiary"
            kinds[kind][0] += 1
            kinds[kind][1] += org_of[n] == main
    recall_by_kind = {k: {"names": v[0], "found": v[1], "recall": round(v[1] / v[0], 3)} for k, v in sorted(kinds.items())}
    # pairwise metrics over labeled names: members of the 20 groups plus NONE names (negatives)
    labeled = [n for n, g in gold.items() if g != "UNSURE" and n in org_of]
    tp = fp = fn = 0
    for a, b in itertools.combinations(labeled, 2):
        ga, gb = gold[a], gold[b]
        if ga == "NONE" and gb == "NONE":
            continue  # two names outside the 20 groups: their relation was not labeled
        same_gold = ga == gb and ga != "NONE"
        same_pred = org_of[a] == org_of[b]
        tp += same_gold and same_pred
        fp += same_pred and not same_gold
        fn += same_gold and not same_pred
    return {
        "groups": len(per_group),
        "gold_member_names": int(tot["gold"]),
        "labeled_not_member_names": sum(1 for g in gold.values() if g == "NONE"),
        "unsure_names_excluded": sum(1 for g in gold.values() if g == "UNSURE"),
        "name_recall_micro": round(tot["hit"] / tot["gold"], 3),
        "name_precision_micro": round(tot["hit"] / tot["pred"], 3),
        "trial_recall_micro": round(tot["hit_trials"] / tot["gold_trials"], 3),
        "pairwise_precision": round(tp / (tp + fp), 3) if tp + fp else 0.0,
        "pairwise_recall": round(tp / (tp + fn), 3) if tp + fn else 0.0,
        "pairwise_counts": {"tp": tp, "fp": fp, "fn": fn},
        "recall_by_kind_of_name": recall_by_kind,
        "labels_missing_from_data": missing,
        "unlabeled_names_inside_predicted_groups": sorted(unlabeled_in_clusters),
        "per_group": per_group,
    }


def audit_sample() -> list[dict]:
    """Seeded sample of pairs outside the gold groups, for the precision and miss audits."""
    org_of, members, _, trials = _predictions()
    gold = _gold()
    gold_orgs = {org_of[n] for n in gold if n in org_of}
    rng = random.Random(SEED)
    multi = sorted(o for o, m in members.items() if len(m) > 1 and o not in gold_orgs)
    merged = []
    for o in rng.sample(multi, min(AUDIT_MERGED, len(multi))):
        a, b = rng.sample(sorted(members[o]), 2)
        merged.append({"kind": "merged", "name_a": a, "name_b": b})
    state = json.loads((config.CACHE / "resolution_state.json").read_text())
    names = state["names"]
    seen, near = set(), []
    for i, row in enumerate(state["neighbors"]):
        for j, cos in row:
            a, b = names[i], names[j]
            key = tuple(sorted((a, b)))
            if cos >= NEAR_MISS_COSINE and org_of[a] != org_of[b] and key not in seen \
                    and org_of[a] not in gold_orgs and org_of[b] not in gold_orgs:
                seen.add(key)
                near.append({"kind": "near_miss", "name_a": key[0], "name_b": key[1], "cosine": round(cos, 3)})
    population = {"merged_orgs": len(multi), "near_miss_pairs": len(near)}
    near = rng.sample(sorted(near, key=lambda r: (r["name_a"], r["name_b"])), min(AUDIT_NEAR_MISS, len(near)))
    return merged + near, population


def audit_metrics() -> dict | None:
    if not AUDIT.exists():
        return None
    with AUDIT.open() as fh:
        rows = list(csv.DictReader(fh))
    out = {}
    for kind in ("merged", "near_miss"):
        rs = [r for r in rows if r["kind"] == kind]
        same = sum(r["label"] == "same" for r in rs)
        diff = sum(r["label"] == "different" for r in rs)
        unsure = sum(r["label"] == "unsure" for r in rs)
        out[kind] = {"pairs": len(rs), "same": same, "different": diff, "unsure": unsure,
                     "share_same": round(same / (same + diff), 3) if same + diff else 0.0}
    return out


def run() -> dict:
    metrics = {"gold_groups": gold_group_metrics(), "pair_audit": audit_metrics()}
    _, population = audit_sample()
    metrics["pair_audit_population"] = population
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    return metrics
