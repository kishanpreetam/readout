"""Company and sponsor name normalization.

Three views of a name, from strict to loose:
  normalize("Vertex Pharmaceuticals (Europe) Ltd.")      == "vertex pharmaceuticals"
  core("Vertex Pharmaceuticals (Europe) Ltd.")           == "vertex"
  industry_words("Vertex Pharmaceuticals (Europe) Ltd.") == {"pharma"}
The linker combines them with affiliate words (countries, "research and development") and aliases.
"""

from __future__ import annotations

import re
import unicodedata

# "KGaA" is kept on purpose: it is what separates Merck KGaA (Darmstadt) from Merck & Co.
LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "cos", "ltd", "limited", "llc", "plc", "lp",
    "llp", "sa", "sas", "sarl", "ag", "nv", "bv", "gmbh", "kk", "spa", "srl", "ab", "as", "asa", "oy", "oyj",
    "se", "pty", "pte", "pvt", "kft", "sro", "sl", "slu", "aps", "bhd", "sdn", "tbk", "ulc", "uc", "dac", "mbh", "gk", "ltda", "sau",
    "holdings", "holding", "group", "cv", "the", "and", "of", "de",
}
# Words that describe the industry rather than the firm, mapped to a family so that singular and plural
# forms compare equal. Removing them leaves the distinctive "core" of a name.
GENERIC_FAMILY = {
    "pharmaceutical": "pharma", "pharmaceuticals": "pharma", "pharma": "pharma", "pharmaceutica": "pharma",
    "pharmaceutique": "pharma", "pharmaceutique": "pharma",
    "therapeutics": "therapeutics", "therapeutic": "therapeutics", "therapies": "therapeutics",
    "therapy": "therapeutics", "biotherapeutics": "therapeutics",
    "biosciences": "bioscience", "bioscience": "bioscience",
    "biotech": "biotech", "biotechnology": "biotech", "biotechnologies": "biotech",
    "biopharma": "biopharma", "biopharmaceutical": "biopharma", "biopharmaceuticals": "biopharma",
    "biologics": "biologics", "biologic": "biologics", "bio": "bio",
    "sciences": "science", "science": "science",
    "medical": "medicine", "medicines": "medicine", "medicine": "medicine",
    "oncology": "oncology", "labs": "labs", "laboratories": "labs", "laboratory": "labs",
    "life": "life", "health": "health", "healthcare": "health",
    "technologies": "technology", "technology": "technology", "innovations": "innovation",
    "innovation": "innovation", "rx": "rx", "tx": "therapeutics",
}
# Short industry suffixes that some sponsors glue onto the name ("ModernaTX" is Moderna's operating company).
GLUED_SUFFIXES = ("tx", "rx")
GENERIC = set(GENERIC_FAMILY)
# Tokens that mark a country unit or business unit of the same company ("Pfizer Japan", "Janssen R&D").
AFFILIATE = {
    "research", "development", "rd", "global", "international", "worldwide", "usa", "us", "america", "americas",
    "north", "europe", "european", "eu", "uk", "japan", "china", "shanghai", "beijing", "korea", "taiwan", "india",
    "canada", "australia", "germany", "deutschland", "france", "italia", "italy", "spain", "espana", "brasil",
    "brazil", "mexico", "nederland", "netherlands", "belgium", "switzerland", "schweiz", "ireland", "israel",
    "singapore", "hong", "kong", "asia", "pacific", "latin", "operations", "services", "clinical", "trials",
    "ventures", "manufacturing", "sales", "distribution", "products", "investments", "subsidiary", "affiliate",
    "division", "unit", "center", "centre", "affairs",
}
STOPWORDS = {"and", "of", "the", "de", "for", "a"}

_paren = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_state_suffix = re.compile(r"\s*/\s*[a-z]{2,3}\s*/?\s*$")  # SEC names such as "VERTEX PHARMACEUTICALS INC / MA"
_nonword = re.compile(r"[^a-z0-9]+")
_dotted = re.compile(r"\b([a-z])\.(?=[a-z]\b)")  # "s.p.a." becomes "spa.", "r.d." becomes "rd."


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def tokens(name: str | None) -> list[str]:
    """Lowercase ASCII tokens with parentheses, SEC state suffixes and punctuation removed."""
    if not name:
        return []
    text = ascii_fold(name).lower()
    text = _state_suffix.sub("", text)
    text = _paren.sub(" ", text)
    text = re.sub(r"\ba/s\b", "as", text)
    text = re.sub(r"\br\s*&\s*d\b", "rd", text)  # "R&D" is one unit word, not "r and d"
    text = re.sub(r"\b(?:scientific|medical) affairs\b", "affairs", text)  # a business unit, not part of the name
    text = text.replace("&", " and ").replace("+", " and ")
    text = _dotted.sub(r"\1", text)
    text = text.replace("'s ", "s ").replace("'", "")
    out: list[str] = []
    for t in _nonword.split(text):
        if not t:
            continue
        glued = next((g for g in GLUED_SUFFIXES if t.endswith(g) and len(t) - len(g) >= 5), None)
        out.extend([t[: -len(glued)], glued] if glued else [t])
    return out


def strip_legal(toks: list[str]) -> list[str]:
    """Drop legal-form words at the end ("Inc.", "Co., Ltd.") and a leading "the"."""
    out = list(toks)
    while out and out[0] == "the":
        out = out[1:]
    while len(out) > 1 and out[-1] in LEGAL_SUFFIXES:
        out = out[:-1]
    return out


def normalize(name: str | None) -> str:
    return " ".join(strip_legal(tokens(name)))


def core_tokens(name: str | None) -> list[str]:
    """Distinctive tokens: legal forms, industry words and stopwords removed (never empty if the name isn't)."""
    base = strip_legal(tokens(name))
    kept = [t for t in base if t not in GENERIC and t not in STOPWORDS and t not in LEGAL_SUFFIXES]
    return kept or [t for t in base if t not in STOPWORDS] or base


def core(name: str | None) -> str:
    return " ".join(core_tokens(name))


def industry_words(name: str | None) -> set[str]:
    """The industry-word families in a name, used to keep "Prime Medicine" apart from "Prime Pharma"."""
    return {GENERIC_FAMILY[t] for t in strip_legal(tokens(name)) if t in GENERIC_FAMILY}


def parenthetical(name: str | None) -> list[str]:
    """Text inside parentheses, which sometimes names the parent ("Henlius (a Fosun Pharma company)")."""
    if not name:
        return []
    return [m.strip("()[] ") for m in _paren.findall(name) if m.strip("()[] ")]
