"""Claim-level verification for the chat tab (docs/claim-verification-design.md). No model calls here.

The search readers and the local-document readers both produce ``EvidenceCard``s. A leader model (the same
Ternary-Bonsai-2-27B process as the critic) splits the question into claims and judges each claim against the
cards only; the synthesizer writes from the supported and partial claims; the final text is audited once more
and the sentences that lost their support are deleted (``drop_sentences``), never rewritten.

The model's verdict is only advice. The gate that counts is here, in the orchestrator:

- ``status`` must be one of the five values, ``supported`` needs cited cards that exist;
- a ``supported`` claim must share a 20-character run with a cited card's quote, or (for Japanese claims that
  paraphrase an English quote) contain an anchor of the card: a number, a date or a proper noun;
- a claim with neither a number nor a proper noun can only be ``partial``; a number that no cited card has makes
  the claim ``unsupported`` (no invented figures);
- cards whose quote could not be found in the source are at most ``partial`` evidence.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

STATUSES = ("supported", "partial", "contradicted", "unsupported", "opinion")
KEEP = ("supported", "partial")
DROP = ("contradicted", "unsupported")
STATUS_LABELS = {"supported": "支持", "partial": "一部", "contradicted": "矛盾", "unsupported": "出典なし",
                 "opinion": "意見"}
RUN = 20            # characters of a claim that must occur verbatim in a cited quote (§5.5)
NOTE_CHARS = 80
CLAIM_CHARS = 120


# --- schemas (what the models return; loose, the orchestrator checks every field) ---------------------------------


class ExtractedClaim(BaseModel):
    claim_id: str = ""
    text: str = ""


class Extracted(BaseModel):
    claims: list[ExtractedClaim] = Field(default_factory=list)


class Verdict(BaseModel):
    claim_id: str = ""
    status: str = "unsupported"
    evidence_ids: list[str] = Field(default_factory=list)
    contradict_ids: list[str] = Field(default_factory=list)
    quote: str = ""
    note: str = ""


class Verdicts(BaseModel):
    claims: list[Verdict] = Field(default_factory=list)


class DocCard(BaseModel):
    quote: str = ""
    note: str = ""


class DocCards(BaseModel):
    cards: list[DocCard] = Field(default_factory=list)


class Cover(BaseModel):
    next: list[str] = Field(default_factory=list)
    stop: bool = False


class DocPlan(BaseModel):
    chunks: list[str] = Field(default_factory=list)


Status = Literal["supported", "partial", "contradicted", "unsupported", "opinion"]


# --- normalisation and anchors -------------------------------------------------------------------------------------


def norm(text: str) -> str:
    """NFKC, lower case, without spaces and punctuation (the same idea as search_agent._norm)."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\s　「」『』\"'“”‘’、。,.・:：;；()（）\[\]…\-*_`#>|]+", "", text)


_CITATION = re.compile(r"\[\d+\]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{2,}")
_KATAKANA = re.compile(r"[ァ-ヿ]{3,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "has", "have", "not", "but",
         "its", "into", "than", "then", "also", "will", "can", "may", "per", "via", "url", "http", "https", "www"}


def numbers(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text or "")
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def names(text: str) -> set[str]:
    """Proper-noun-like tokens: ASCII words of 3+ characters (product names, people, places in Latin letters)
    and katakana runs of 3+ characters."""
    text = unicodedata.normalize("NFKC", text or "")
    words = {w.lower().strip(".-") for w in _ASCII_WORD.findall(text)}
    return {w for w in words if w not in _STOP and len(w) >= 3} | set(_KATAKANA.findall(text))


def anchors(text: str) -> set[str]:
    return numbers(text) | names(text)


def shares_run(a: str, b: str, run: int = RUN) -> bool:
    """True when ``a`` and ``b`` (normalised) share ``run`` consecutive characters."""
    a, b = norm(a), norm(b)
    if len(a) < run or len(b) < run:
        return False
    return any(a[i:i + run] in b for i in range(len(a) - run + 1))


# --- evidence cards ------------------------------------------------------------------------------------------------


def _card(evidence_id: str, n: int, source_type: str, locator: str, title: str, quote: str, note: str,
          verified: bool, quote_chars: int) -> dict:
    return {"evidence_id": evidence_id, "n": n, "source_type": source_type, "locator": locator,
            "title": (title or locator)[:200], "quote": (quote or "")[:quote_chars], "note": (note or "")[:NOTE_CHARS],
            "verified": verified}


def evidence_from_search(cards: list[dict], refs: list[dict], quote_chars: int = 400, limit: int = 24) -> list[dict]:
    """Search fact cards -> EvidenceCards. ``n`` is the reference number of the card's URL (refs), fixed here;
    the synthesizer never renumbers. One card per (URL, claim); URLs without a reference number are left out."""
    number = {r["url"]: r for r in refs}
    out: list[dict] = []
    ranked = sorted(cards, key=lambda c: (number[c["url"]]["n"] if c.get("url") in number else 99))
    for card in ranked:
        ref = number.get(card.get("url"))
        if ref is None:
            continue
        extra = []
        if card.get("date"):
            extra.append(f"日付 {card['date']}")
        extra += [f"反証: {c}" for c in card.get("conflicts") or []]
        for claim in card.get("claims") or []:
            note = "／".join([claim.get("claim", ""), *extra])
            out.append(_card(f"e{len(out) + 1}", ref["n"], "web", card["url"], ref.get("title", ""),
                             claim.get("quote", ""), note, bool(claim.get("quote_ok")), quote_chars))
            if len(out) >= limit:
                return out
    return out


def number_doc_cards(cards: list[dict], quote_chars: int = 400, limit: int = 24) -> list[dict]:
    """Local-document cards -> EvidenceCards numbered 1..n in reading order (one [n] per card)."""
    out = []
    for card in cards[:limit]:
        n = len(out) + 1
        out.append({**_card(f"e{n}", n, "file", card["locator"], card.get("title") or card["locator"],
                            card.get("quote", ""), card.get("note", ""), bool(card.get("verified")), quote_chars),
                    "chunk_id": card.get("chunk_id", "")})
    return out


def evidence_block(evidence: list[dict]) -> str:
    """Cards for the verifier: id, number, locator, quote and note. The text is data, fenced."""
    lines = []
    for card in evidence:
        flag = "" if card.get("verified") else "（引用未確認）"
        lines.append(f"- {card['evidence_id']} [{card['n']}] {card['locator']}{flag}\n"
                     f"  quote: {card['quote']}\n  note: {card['note']}")
    body = "\n".join(lines).replace("```", "'''") or "（カードなし）"
    return "```text\n" + body + "\n```"


# --- claims --------------------------------------------------------------------------------------------------------


def extracted_claims(extracted: Extracted | None, max_claims: int) -> tuple[list[dict], bool]:
    """(claims c1..cN with their text, truncated). Empty texts and duplicates are dropped; past ``max_claims``
    the rest is cut and reported (§5.5)."""
    out, seen = [], set()
    items = extracted.claims if extracted else []
    for item in items:
        text = " ".join((item.text or "").split())[:CLAIM_CHARS]
        key = norm(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"claim_id": f"c{len(out) + 1}", "text": text, "status": "unsupported", "evidence_ids": [],
                    "contradict_ids": [], "sentence_index": -1, "note": "", "quote": ""})
    return out[:max_claims], len(out) > max_claims


def claims_block(claims: list[dict]) -> str:
    body = "\n".join(f"- {c['claim_id']}: {c['text']}" for c in claims).replace("```", "'''")
    return "```text\n" + (body or "（主張なし）") + "\n```"


def gate(claim: dict, by_id: dict[str, dict]) -> tuple[str, str]:
    """The orchestrator's own check of one model verdict -> (status, reason). See the module docstring."""
    status = claim["status"]
    text = _CITATION.sub("", claim["text"])
    if status not in STATUSES:
        return "unsupported", "判定が列挙外"
    if status == "opinion":
        # A sentence with figures is a factual claim, whatever the model calls it.
        return ("unsupported", "数値を含む意見") if numbers(text) else ("opinion", "")
    if status not in KEEP:
        if status == "contradicted" and not claim["contradict_ids"] and not claim["evidence_ids"]:
            return "unsupported", "矛盾の出典が無い"
        return status, ""
    cited = [by_id[e] for e in claim["evidence_ids"] if e in by_id]
    if not cited:
        return "unsupported", "根拠カードが無い"
    source = " ".join(f"{c['quote']} {c['note']}" for c in cited)
    claim_numbers = numbers(text)
    missing = claim_numbers - numbers(source)
    if missing:
        return "unsupported", f"出典に無い数値 {', '.join(sorted(missing))[:40]}"
    verbatim = shares_run(text, source) or (claim.get("quote") and shares_run(claim["quote"], source))
    claim_anchors = anchors(text)
    anchored = bool(claim_anchors & anchors(source))
    if not verbatim and not anchored:
        return "unsupported", "出典の語句・数値が主張に無い"
    if status == "supported":
        if not claim_anchors and not verbatim:
            return "partial", "固有名詞も数値も無い"
        if not any(c.get("verified") for c in cited):
            return "partial", "引用を原文で確認できないカード"
    return status, ""


def apply_verdicts(claims: list[dict], verdicts: Verdicts | None, evidence: list[dict]) -> list[dict]:
    """Merge the verifier's verdicts into the extracted claims and run the gate. A claim the verifier skipped
    stays ``unsupported``."""
    by_id = {c["evidence_id"]: c for c in evidence}
    got = {v.claim_id: v for v in (verdicts.claims if verdicts else [])}
    out = []
    for claim in claims:
        v = got.get(claim["claim_id"])
        merged = dict(claim)
        if v is not None:
            merged.update({"status": (v.status or "").strip().lower(),
                           "evidence_ids": [e for e in v.evidence_ids if e in by_id][:6],
                           "contradict_ids": [e for e in v.contradict_ids if e in by_id][:6],
                           "quote": (v.quote or "")[:400], "note": (v.note or "")[:NOTE_CHARS]})
        else:
            merged["note"] = "判定が返らなかった"
        status, reason = gate(merged, by_id)
        if reason:
            merged["note"] = reason
        merged["status"] = status
        out.append(merged)
    return out


def numbers_of(claim: dict, evidence: list[dict]) -> list[int]:
    by_id = {c["evidence_id"]: c for c in evidence}
    return sorted({by_id[e]["n"] for e in claim.get("evidence_ids") or [] if e in by_id})


def verified_block(claims: list[dict], evidence: list[dict]) -> str:
    """The synthesizer's input: supported and partial claims only, with their [n] (§5.6)."""
    lines = []
    for claim in claims:
        if claim["status"] not in KEEP:
            continue
        refs = " ".join(f"[{n}]" for n in numbers_of(claim, evidence))
        hedge = "（一部のみ確認。断定しない）" if claim["status"] == "partial" else ""
        lines.append(f"- {claim['claim_id']} {refs} {claim['text']}{hedge}")
    return ("```text\n" + "\n".join(lines).replace("```", "'''") + "\n```") if lines else ""


def table(claims: list[dict], evidence: list[dict]) -> list[dict]:
    """Rows of the progress table (UI: claim_trace)."""
    return [{"claim_id": c["claim_id"], "status": c["status"], "text": c["text"][:CLAIM_CHARS],
             "n": numbers_of(c, evidence), "note": c.get("note", "")[:NOTE_CHARS]} for c in claims]


def table_text(claims: list[dict], evidence: list[dict]) -> str:
    rows = []
    for row in table(claims, evidence):
        refs = " ".join(f"[{n}]" for n in row["n"])
        why = f"（{row['note']}）" if row["note"] and row["status"] not in KEEP else ""
        rows.append(f"{row['claim_id']:<4} {row['status']:<12} {refs:<8} {row['text'][:60]}{why}")
    return "```text\n" + "\n".join(rows).replace("```", "'''") + "\n```" if rows else ""


# --- the final text: sentences, audit and deletion ---------------------------------------------------------------


_SENTENCE_END = re.compile(r"(?<=[。！？!?])")
_CONNECTIVE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)?(したがって|そのため|このため|よって|つまり|ゆえに|従って|以上から|以上より)")
_HEDGE = re.compile(r"確認できません|確認できなかった|出典に(は)?無|出典には書かれて|分かりません|わかりません|不明です|見つかりません")
_STRUCTURE = re.compile(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*\s*$|[-*]\s*$|\|)")


def split_sentences(text: str) -> list[dict]:
    """The final text as sentences: {index, line, text}. Headings, table rows and empty lines are not
    sentences (they are kept as they are)."""
    out = []
    for line_no, line in enumerate((text or "").split("\n")):
        if not line.strip() or _STRUCTURE.match(line):
            continue
        for piece in _SENTENCE_END.split(line):
            if piece.strip():
                out.append({"index": len(out), "line": line_no, "text": piece})
    return out


def audit_claims(sentences: list[dict]) -> list[dict]:
    """Audit input: one claim per sentence, ids s0.., sentence_index set. Hedges ("確認できませんでした") are
    statements about the sources, not facts, and are not audited."""
    out = []
    for s in sentences:
        text = " ".join(s["text"].split())
        if _HEDGE.search(text) or not norm(re.sub(r"\[\d+\]", "", text)):
            continue
        out.append({"claim_id": f"s{s['index']}", "text": text[:CLAIM_CHARS * 2], "status": "unsupported",
                    "evidence_ids": [], "contradict_ids": [], "sentence_index": s["index"], "note": "", "quote": ""})
    return out


def drop_sentences(text: str, drop: set[int]) -> tuple[str, list[str]]:
    """Delete the sentences in ``drop`` (by index from split_sentences) and the ones that lost their ground: a
    sentence that starts with 「したがって」 etc. right after a deleted one. Returns (text, deleted sentences).
    Lines left without any sentence are removed; a heading left without a body below it is removed too."""
    sentences = split_sentences(text)
    deleted: set[int] = set()
    for s in sentences:
        if s["index"] in drop or (s["index"] - 1 in deleted and _CONNECTIVE.match(s["text"])):
            deleted.add(s["index"])
    if not deleted:
        return text, []
    by_line: dict[int, list[dict]] = {}
    for s in sentences:
        by_line.setdefault(s["line"], []).append(s)
    lines = text.split("\n")
    out_lines: list[str | None] = []
    for line_no, line in enumerate(lines):
        pieces = by_line.get(line_no)
        if not pieces:
            out_lines.append(line)
            continue
        kept = [p["text"] for p in pieces if p["index"] not in deleted]
        if not kept:
            out_lines.append(None)
            continue
        rebuilt = "".join(kept)
        prefix = re.match(r"^\s*(?:[-*]\s+|\d+\.\s+)", line)
        if prefix and not rebuilt.lstrip().startswith(prefix.group(0).strip()):
            rebuilt = prefix.group(0) + rebuilt.lstrip()
        out_lines.append(rebuilt)
    # A heading whose section lost every line goes too.
    result: list[str] = []
    for i, line in enumerate(out_lines):
        if line is None:
            continue
        if re.match(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*\s*$)", line):
            rest = [x for x in out_lines[i + 1:] if x is None or x.strip()]
            nxt = next((x for x in rest if x is not None), None)
            removed_below = any(x is None for x in out_lines[i + 1:i + 3])
            if removed_below and (nxt is None or re.match(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*\s*$)", nxt)):
                continue
        result.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(result)).strip()
    return cleaned, [s["text"].strip() for s in sentences if s["index"] in deleted]


def excerpts_text(evidence: list[dict], limit: int = 12) -> str:
    """The excerpts list returned when verification failed and CLAIM_VERIFY_FAIL_OPEN=0 (no unaudited answer)."""
    lines = [f"- [{c['n']}] {c['note'] or c['quote'][:120]}" for c in evidence[:limit]]
    return "\n".join(lines)
