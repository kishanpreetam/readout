"""Link ClinicalTrials.gov sponsor names to SEC companies.

Tiers, tried in order; the first tier that finds exactly one company wins:
  exact      normalized names are equal (legal suffixes such as "Inc." or "Co., Ltd." removed)
  core       distinctive cores are equal and the industry words agree ("Vertex Pharmaceuticals (Europe)")
  affiliate  the sponsor is the company core plus country or unit words ("Pfizer Japan", "Janssen R&D")
  fuzzy      character n-gram TF-IDF finds candidates, then a token-sort ratio of at least FUZZY_MIN
Each tier runs against the SEC name, SEC former names and a small curated alias list (subsidiaries and
acquired companies, each with a source). When two companies tie, the sponsor is left unlinked.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from .names import AFFILIATE, core_tokens, industry_words, normalize, parenthetical

FUZZY_MIN = 92
CANDIDATES_K = 5
TIER_SCORE = {"exact": 1.0, "core": 0.97, "affiliate": 0.94}


@dataclass
class Entry:
    text: str
    cik: int
    source: str  # sec_name | sec_former_name | curated_alias
    norm: str = ""
    core: tuple[str, ...] = ()
    words: frozenset[str] = field(default_factory=frozenset)


@dataclass
class Link:
    sponsor_name: str
    cik: int | None
    method: str  # exact | core | affiliate | fuzzy | none | ambiguous
    score: float
    matched_alias: str | None
    alias_source: str | None
    best_candidate_cik: int | None
    best_candidate_alias: str | None
    best_candidate_cosine: float


def compatible(a: set[str], b: set[str]) -> bool:
    """Industry words agree when either side has none, or one side's families contain the other's."""
    return not a or not b or a <= b or b <= a


class Linker:
    def __init__(self, companies: list[dict], corpus: list[str] | None = None):
        """`corpus` (all sponsor names) is only used to fit the n-gram vocabulary, so that words a
        company name lacks still count against a candidate."""
        self.entries: list[Entry] = []
        for c in companies:
            texts = [(c["name"], "sec_name")]
            texts += [(f, "sec_former_name") for f in c.get("former_names", [])]
            texts += [(a, "curated_alias") for a in c.get("aliases", [])]
            seen = set()
            for text, source in texts:
                e = Entry(text, int(c["cik"]), source, normalize(text), tuple(core_tokens(text)),
                          frozenset(industry_words(text)))
                if e.norm and (e.norm, e.cik) not in seen:
                    seen.add((e.norm, e.cik))
                    self.entries.append(e)
        self.by_norm: dict[str, list[Entry]] = defaultdict(list)
        self.by_core: dict[tuple[str, ...], list[Entry]] = defaultdict(list)
        for e in self.entries:
            self.by_norm[e.norm].append(e)
            self.by_core[e.core].append(e)
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 3), lowercase=False)
        vocab_texts = [e.norm for e in self.entries] + [normalize(n) for n in (corpus or [])]
        self.vectorizer.fit(vocab_texts)
        self.matrix = self.vectorizer.transform([e.norm for e in self.entries])

    @staticmethod
    def _unique(entries: list[Entry]) -> Entry | None | str:
        ciks = {e.cik for e in entries}
        if not ciks:
            return None
        if len(ciks) > 1:
            return "ambiguous"
        # prefer the SEC name over former names over curated aliases when several entries agree
        order = {"sec_name": 0, "sec_former_name": 1, "curated_alias": 2}
        return sorted(entries, key=lambda e: order[e.source])[0]

    def _rule_tiers(self, name: str) -> tuple[str, Entry | str | None]:
        norm, toks, words = normalize(name), tuple(core_tokens(name)), industry_words(name)
        hit = self._unique(self.by_norm.get(norm, []))
        if hit:
            return "exact", hit
        hit = self._unique([e for e in self.by_core.get(toks, []) if compatible(words, e.words)])
        if hit:
            return "core", hit
        for k in range(len(toks) - 1, 0, -1):
            if all(t in AFFILIATE for t in toks[k:]):
                hit = self._unique([e for e in self.by_core.get(toks[:k], []) if compatible(words, e.words)])
                if hit:
                    return "affiliate", hit
        return "none", None

    def candidates(self, name: str, k: int = CANDIDATES_K) -> list[tuple[Entry, float]]:
        vec = self.vectorizer.transform([normalize(name)])
        sims = linear_kernel(vec, self.matrix).ravel()
        top = sims.argsort()[::-1][:k]
        return [(self.entries[i], float(sims[i])) for i in top if sims[i] > 0]

    def link(self, name: str) -> Link:
        cands = self.candidates(name)
        best = cands[0] if cands else (None, 0.0)
        method, hit = self._rule_tiers(name)
        if hit is None:  # a parent named in parentheses, e.g. "Henlius (a Fosun Pharma company)"
            for inner in parenthetical(name):
                method, hit = self._rule_tiers(inner)
                if isinstance(hit, Entry):
                    method = f"{method}_parenthetical"
                    break
        if hit is None:
            hit = self._fuzzy(name, cands)
            method = "fuzzy" if hit else "none"
        score = 0.0
        if hit == "ambiguous":
            method, hit = "ambiguous", None
        elif isinstance(hit, Entry):
            score = TIER_SCORE.get(method.replace("_parenthetical", ""), 0.0)
            if method == "fuzzy":
                score = fuzz.token_sort_ratio(" ".join(core_tokens(name)), " ".join(hit.core)) / 100
        return Link(
            sponsor_name=name,
            cik=hit.cik if isinstance(hit, Entry) else None,
            method=method,
            score=round(score, 4),
            matched_alias=hit.text if isinstance(hit, Entry) else None,
            alias_source=hit.source if isinstance(hit, Entry) else None,
            best_candidate_cik=best[0].cik if best[0] else None,
            best_candidate_alias=best[0].text if best[0] else None,
            best_candidate_cosine=round(best[1], 4),
        )

    def _fuzzy(self, name: str, cands: list[tuple[Entry, float]]) -> Entry | str | None:
        toks, words = core_tokens(name), industry_words(name)
        sponsor_core = " ".join(toks)
        if len(sponsor_core) < 4:
            return None
        passing = []
        for e, _cos in cands:
            company_core = " ".join(e.core)
            if not company_core or company_core[:3] != sponsor_core[:3] or not compatible(words, e.words):
                continue
            ratio = fuzz.token_sort_ratio(sponsor_core, company_core)
            if ratio >= FUZZY_MIN:
                passing.append((ratio, e))
        if not passing:
            return None
        top = max(r for r, _ in passing)
        return self._unique([e for r, e in passing if r == top])


def link_all(companies: list[dict], sponsor_names: list[str]) -> list[Link]:
    linker = Linker(companies, corpus=sponsor_names)
    return [linker.link(n) for n in sponsor_names]


ALIASES_CSV = "labels/company_aliases.csv"
LINK_COLUMNS = {
    "sponsor_name": "VARCHAR", "cik": "BIGINT", "link_method": "VARCHAR", "link_score": "DOUBLE",
    "matched_alias": "VARCHAR", "alias_source": "VARCHAR", "best_candidate_cik": "BIGINT",
    "best_candidate_alias": "VARCHAR", "best_candidate_cosine": "DOUBLE",
}


def load_companies(con, use_aliases: bool = True) -> list[dict]:
    """Universe companies with SEC former names and (optionally) curated aliases."""
    import csv

    from . import config

    companies = {
        int(cik): {"cik": int(cik), "name": name, "former_names": list(former or []), "aliases": []}
        for cik, name, former in con.execute("SELECT cik, name, former_names FROM raw.sec_companies").fetchall()
    }
    path = config.ROOT / ALIASES_CSV
    if use_aliases and path.exists():
        with path.open() as fh:
            for row in csv.DictReader(fh):
                cik = int(row["cik"])
                if cik in companies:
                    companies[cik]["aliases"].append(row["alias"])
    return list(companies.values())


def sponsor_names(con) -> list[str]:
    """Distinct lead sponsor names plus industry collaborator names, in a stable order."""
    sql = """
        SELECT DISTINCT trim(lead_sponsor_name) FROM raw.ct_studies WHERE lead_sponsor_name IS NOT NULL
        UNION
        SELECT DISTINCT trim(name) FROM raw.ct_collaborators WHERE class = 'INDUSTRY' AND name IS NOT NULL
    """
    return sorted(r[0] for r in con.execute(sql).fetchall())


def run(use_aliases: bool = True) -> dict:
    import json
    from collections import Counter

    from . import config
    from .db import connect

    con = connect()
    companies = load_companies(con, use_aliases)
    names = sponsor_names(con)
    links = link_all(companies, names) if companies else []
    con.execute("DROP TABLE IF EXISTS raw.sponsor_links")
    ddl = ", ".join(f"{k} {v}" for k, v in LINK_COLUMNS.items())
    con.execute(f"CREATE TABLE raw.sponsor_links ({ddl})")
    if links:
        con.executemany(
            f"INSERT INTO raw.sponsor_links VALUES ({', '.join('?' * len(LINK_COLUMNS))})",
            [[l.sponsor_name, l.cik, l.method, l.score, l.matched_alias, l.alias_source,
              l.best_candidate_cik, l.best_candidate_alias, l.best_candidate_cosine] for l in links],
        )
    linked = [l for l in links if l.cik is not None]
    trial_counts = dict(con.execute("""
        SELECT l.sponsor_name, count(DISTINCT s.nct_id)
        FROM raw.sponsor_links l JOIN raw.ct_studies s ON trim(s.lead_sponsor_name) = l.sponsor_name
        WHERE l.cik IS NOT NULL GROUP BY 1
    """).fetchall()) if linked else {}
    con.close()
    summary = {
        "companies": len(companies),
        "companies_with_aliases": sum(1 for c in companies if c["aliases"]),
        "aliases": sum(len(c["aliases"]) for c in companies),
        "sponsor_names": len(names),
        "linked_sponsor_names": len(linked),
        "companies_with_a_link": len({l.cik for l in linked}),
        "methods": dict(Counter(l.method for l in links)),
        "lead_trials_linked": sum(trial_counts.values()),
        "use_aliases": use_aliases,
    }
    out = config.RESULTS / "linking"
    out.mkdir(parents=True, exist_ok=True)
    (out / ("links_summary.json" if use_aliases else "links_summary_no_aliases.json")).write_text(
        json.dumps(summary, indent=2) + "\n")
    return summary
