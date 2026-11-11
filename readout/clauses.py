"""Parse ownership and alias clauses that sponsors write into their own names.

  "Seagen, a wholly owned subsidiary of Pfizer"            base "Seagen",  parent "Pfizer"
  "Kite, A Gilead Company"                                  base "Kite",    parent "Gilead"
  "ArQule, Inc., a subsidiary of Merck Sharp & Dohme LLC, a subsidiary of Merck & Co., Inc."
                                                            base "ArQule, Inc.", parents MSD then Merck & Co.
  "Advanced Cooling Therapy, Inc., d/b/a Attune Medical"    base "Advanced Cooling Therapy, Inc.", aka "Attune Medical"
  "Pfizer's Upjohn has merged with Mylan to form Viatris Inc."   base "Viatris Inc."
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SEP = r"(?:\s*[,\-–(]\s*|\s+)"
_MERGED = re.compile(r"^.+?\bhas merged with\b.+?\bto form\s+(?P<new>.+)$", re.I)
_PARENT = [
    re.compile(SEP.join([r"^(?P<base>.+?)", r"(?:is\s+)?(?:an?\s+)?(?:wholly[\s-]owned\s+|indirect\s+|direct\s+)?"
                         r"subsidiary of\s+(?P<parent>.+?)\)?\.?$"]), re.I),
    re.compile(SEP.join([r"^(?P<base>.+?)", r"an?\s+(?:healthcare|pharmaceutical|biopharmaceutical)\s+company of\s+"
                         r"(?P<parent>.+?)\)?\.?$"]), re.I),
    re.compile(SEP.join([r"^(?P<base>.+?)", r"an affiliate of\s+(?P<parent>.+?)\)?\.?$"]), re.I),
    re.compile(SEP.join([r"^(?P<base>.+?)", r"a member of the\s+(?P<parent>.+?)\s+group(?: of companies)?\)?\.?$"]), re.I),
    re.compile(SEP.join([r"^(?P<base>.+?)", r"(?:now\s+)?(?:a\s+)?part of\s+(?P<parent>.+?)\)?\.?$"]), re.I),
    re.compile(SEP.join([r"^(?P<base>.+?)", r"an?\s+(?P<parent>[A-Z][\w&.'\- ]*?)\s+company\)?\.?$"]), re.I),
]
_AKA = [
    re.compile(r"^(?P<base>.+?)\s*[,(]?\s*\b(?:d/b/a|dba)\b[:\s]+(?P<aka>.+?)\)?\.?$", re.I),
    re.compile(r"^(?P<base>.+?)\s*[,(]?\s*\b(?:formerly|previously|also known as)\b[:\s]+(?P<aka>.+?)\)?\.?$", re.I),
]
# Cut location text after the parent's legal form, so "Merck & Co., Inc., Rahway, NJ USA" becomes "Merck & Co., Inc."
_LEGAL_END = re.compile(r"^(.+?\b(?:Inc|Incorporated|LLC|Ltd|Limited|plc|Corp|Corporation|S\.?A|AG|N\.?V|GmbH)\.?)"
                        r"\s*[,(].*$", re.I)


@dataclass
class Parsed:
    name: str
    base: str
    parents: list[str] = field(default_factory=list)  # nearest parent first
    aliases: list[str] = field(default_factory=list)
    clause: str | None = None  # subsidiary | merged | aka


def _clean_parent(text: str) -> str:
    text = text.strip(" ,-()")
    m = _LEGAL_END.match(text)
    text = (m.group(1) if m else text).strip(" ,-()")
    if text.endswith(".") and not re.search(r"\b(?:Inc|Ltd|Co|Corp|S\.A|N\.V|plc)\.$", text, re.I):
        text = text[:-1]
    return text


def parse(name: str) -> Parsed:
    text = " ".join(name.split())
    m = _MERGED.match(text)
    if m:
        return Parsed(name, _clean_parent(m.group("new")), clause="merged")
    for pat in _AKA:
        m = pat.match(text)
        if m and len(m.group("base")) >= 3:
            return Parsed(name, m.group("base").strip(" ,("), aliases=[m.group("aka").strip(" ,.()")], clause="aka")
    for pat in _PARENT:
        m = pat.match(text)
        if m and len(m.group("base").strip(" ,(-")) >= 2 and len(m.group("parent").strip()) >= 2:
            inner = parse(m.group("parent"))  # the parent text can carry its own clause
            parents = [_clean_parent(inner.base)] + inner.parents
            return Parsed(name, m.group("base").strip(" ,(-"), parents=parents, clause="subsidiary")
    return Parsed(name, text)
