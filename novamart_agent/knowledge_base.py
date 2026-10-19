"""Deterministic local retrieval over NovaMart policy documents (data/policies/*.md).

Each Markdown file is split into sections by `## ` headings. Sections are ranked
with BM25Okapi plus exact-match boosts for identifiers (POL-ELC-002, PROD-303,
ORD-1005) and high-signal domain terms (BOPIS, restocking, hazmat, ...).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from rank_bm25 import BM25Okapi

POLICIES_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"

ID_PATTERN = re.compile(r"\b(?:POL|SOP|PRD|PROD)-[A-Z0-9]+(?:-[A-Z0-9]+)?\b", re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
METADATA_PATTERN = re.compile(r"^\s*-\s*\*\*(?P<key>[^:*]+):\*\*\s*(?P<value>.+?)\s*$")

STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its "
    "may must no not of on or our should that the their them then there these this "
    "to was we what when where which who will with within you your".split()
)

# Domain terms that, when present in both the query and a section, strongly
# indicate the section is the authoritative answer.
BOOST_TERMS = frozenset(
    {
        "bopis", "pickup", "extension", "restocking", "open-box", "lipo", "hazmat",
        "serial", "warranty", "receipt", "credit", "final", "clearance", "hygiene",
        "earbuds", "damaged", "transit", "lost", "delivered", "escalate", "escalation",
        "ticket", "match", "competitor", "adjustment", "marketplace", "defective",
        "gift", "software",
    }
)

ID_DOC_BOOST = 1.5
ID_TITLE_BOOST = 1.0
ID_CONTENT_BOOST = 0.4
TERM_TITLE_BOOST = 0.35
TERM_CONTENT_BOOST = 0.15


def _tokenize(text: str) -> list[str]:
    """Lowercase tokens; compound tokens (pol-elc-002, 48-hour) also emit their parts."""
    tokens: list[str] = []
    for match in TOKEN_PATTERN.findall(text.lower()):
        tokens.append(match)
        if "-" in match:
            tokens.extend(part for part in match.split("-") if part)
    return [t for t in tokens if t not in STOPWORDS]


def _extract_ids(text: str) -> set[str]:
    return {m.upper() for m in ID_PATTERN.findall(text)}


def _parse_document(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    metadata: dict[str, str] = {}
    title = path.stem
    sections: list[dict] = []
    current: dict | None = None

    for line in lines:
        if line.startswith("# ") and not sections and current is None:
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            if current:
                sections.append(current)
            current = {"heading": line[3:].strip(), "lines": []}
            continue
        if current is None:
            meta = METADATA_PATTERN.match(line)
            if meta:
                metadata[meta.group("key").strip().lower()] = meta.group("value")
            continue
        current["lines"].append(line)
    if current:
        sections.append(current)

    policy_id = metadata.get("policy id", path.stem)
    summary = metadata.get("summary", "")
    parsed = [
        {
            "policy_id": policy_id,
            "filename": path.name,
            "title": title,
            "summary": summary,
            "effective_date": metadata.get("effective date", ""),
            "section_number": "0",
            "section_title": "Document Overview",
            "content": f"{title}. {summary}",
        }
    ]
    for section in sections:
        number_match = re.match(r"^(\d+)\.\s*", section["heading"])
        parsed.append(
            {
                "policy_id": policy_id,
                "filename": path.name,
                "title": title,
                "summary": summary,
                "effective_date": metadata.get("effective date", ""),
                "section_number": number_match.group(1) if number_match else "",
                "section_title": section["heading"],
                "content": "\n".join(section["lines"]).strip(),
            }
        )
    return parsed


@lru_cache(maxsize=1)
def _load_index() -> tuple[list[dict], BM25Okapi]:
    sections: list[dict] = []
    for path in sorted(POLICIES_DIR.glob("*.md")):
        sections.extend(_parse_document(path))
    if not sections:
        raise FileNotFoundError(f"No policy documents found in {POLICIES_DIR}")

    for section in sections:
        section["_title_text"] = section["section_title"].lower()
        section["_content_text"] = section["content"].lower()
        section["_ids"] = _extract_ids(f"{section['section_title']}\n{section['content']}")
        section["_title_ids"] = _extract_ids(section["section_title"])

    corpus = [
        _tokenize(f"{s['title']} {s['section_title']} {s['section_title']} {s['content']}")
        for s in sections
    ]
    return sections, BM25Okapi(corpus)


def _boost(section: dict, query_ids: set[str], query_terms: set[str]) -> float:
    score = 0.0
    for qid in query_ids:
        if qid == section["policy_id"]:
            score += ID_DOC_BOOST
        if qid in section["_title_ids"]:
            score += ID_TITLE_BOOST
        elif qid in section["_ids"]:
            score += ID_CONTENT_BOOST
    for term in query_terms:
        if term in section["_title_text"]:
            score += TERM_TITLE_BOOST
        elif term in section["_content_text"]:
            score += TERM_CONTENT_BOOST
    return score


def retrieve_policy_sections(query: str, top_k: int = 4) -> list[dict]:
    """Return the top_k policy sections most relevant to the query.

    Each result contains policy_id, filename, title, section_number,
    section_title, content and a relevance score. Sections with no lexical
    overlap with the query are never returned, so an out-of-scope query yields
    an empty list.
    """
    if not query or not query.strip():
        return []

    sections, bm25 = _load_index()
    query_tokens = _tokenize(query)
    query_ids = _extract_ids(query)
    query_terms = {t for t in query_tokens if t in BOOST_TERMS}

    raw_scores = bm25.get_scores(query_tokens) if query_tokens else [0.0] * len(sections)
    max_raw = float(max((s for s in raw_scores if s > 0), default=0.0))

    scored: list[tuple[float, int]] = []
    for idx, section in enumerate(sections):
        raw = float(raw_scores[idx])
        lexical = raw / max_raw if max_raw > 0 and raw > 0 else 0.0
        boost = _boost(section, query_ids, query_terms)
        total = lexical + boost
        if total > 0:
            scored.append((total, idx))

    # Sort by score desc, then by corpus order for deterministic tie-breaking.
    scored.sort(key=lambda pair: (-pair[0], pair[1]))

    results = []
    for score, idx in scored[:top_k]:
        section = sections[idx]
        results.append(
            {
                "policy_id": section["policy_id"],
                "filename": section["filename"],
                "title": section["title"],
                "section_number": section["section_number"],
                "section_title": section["section_title"],
                "content": section["content"],
                "score": round(score, 4),
            }
        )
    return results
