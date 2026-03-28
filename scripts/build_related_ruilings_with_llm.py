#!/usr/bin/env python3
"""
Build an LLM-ranked related-rulings map for assets/data/ruilings.json.

Output format:
{
  "meta": {...},
  "related": {
    "1": [5, 22, 19, 9],
    "2": [7, 40, 3, 11]
  }
}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from gemini_client import call_gemini_json, load_gemini_api_key
from gemini_config import DEFAULT_GEMINI_MODEL


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_PATH = PROJECT_ROOT / "assets" / "data" / "ruilings.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "assets" / "data" / "ruilings_related_llm.json"


SYSTEM_PROMPT = (
    "You are assisting legal research. "
    "Given one target ruling and candidate rulings, pick the most practically related rulings. "
    "Prioritize same legal proposition, same statutory context, similar procedural posture, "
    "and usefulness in legal argument drafting. "
    "Return strict JSON with one key: related (array of candidate IDs). "
    "Rules: only choose IDs from candidate list, do not include target ID, no extra keys."
)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def compact_text(text: str, limit: int) -> str:
    value = clean(text)
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3].rstrip()}..."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate LLM-ranked related-rulings map."
    )
    parser.add_argument(
        "--data-file",
        default=str(DEFAULT_DATA_PATH),
        help="Path to source ruilings JSON file.",
    )
    parser.add_argument(
        "--output-file",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path to write related map JSON.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_GEMINI_MODEL,
        help=f"Gemini model to use (default: {DEFAULT_GEMINI_MODEL}).",
    )
    parser.add_argument(
        "--gemini-api-key",
        default="",
        help="Optional Gemini API key (else use config/env/file lookup).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=4,
        help="How many related IDs to keep per ruling (default: 4).",
    )
    parser.add_argument(
        "--candidate-k",
        type=int,
        default=14,
        help="How many locally ranked candidates to pass to LLM (default: 14).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process first N entries (default: all).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Recompute all entries even if output file already has values.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print one sample LLM payload and exit without writing file.",
    )
    return parser.parse_args()


def build_keyword_set(text: str) -> Set[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "from",
        "this",
        "into",
        "under",
        "while",
        "where",
        "which",
        "there",
        "shall",
        "would",
        "after",
        "before",
        "against",
        "case",
        "cases",
        "court",
        "section",
        "sections",
        "code",
        "act",
    }
    words = re.sub(r"[^a-z0-9\s]", " ", clean(text).lower()).split()
    return {w for w in words if len(w) > 2 and w not in stop_words}


def intersection_size(a: Set[str], b: Set[str]) -> int:
    if not a or not b:
        return 0
    return len(a.intersection(b))


def local_score(base: Dict[str, Any], candidate: Dict[str, Any]) -> float:
    score = 0.0
    if base.get("category") == candidate.get("category"):
        score += 6
    if base.get("subCategory") == candidate.get("subCategory"):
        score += 4
    if base.get("stage") == candidate.get("stage"):
        score += 2
    if base.get("court") == candidate.get("court"):
        score += 1

    base_tags = {clean(tag).lower() for tag in base.get("statuteTags", []) if clean(tag)}
    cand_tags = {
        clean(tag).lower() for tag in candidate.get("statuteTags", []) if clean(tag)
    }
    score += intersection_size(base_tags, cand_tags) * 2

    base_kw = build_keyword_set(
        " ".join(
            [
                clean(base.get("issue", "")),
                clean(base.get("holding", "")),
                clean(base.get("category", "")),
                clean(base.get("subCategory", "")),
            ]
        )
    )
    cand_kw = build_keyword_set(
        " ".join(
            [
                clean(candidate.get("issue", "")),
                clean(candidate.get("holding", "")),
                clean(candidate.get("category", "")),
                clean(candidate.get("subCategory", "")),
            ]
        )
    )
    score += intersection_size(base_kw, cand_kw) * 0.35
    return score


def shortlist_candidates(
    entries: List[Dict[str, Any]],
    target: Dict[str, Any],
    candidate_k: int,
) -> List[Dict[str, Any]]:
    target_id = int(target.get("id") or target.get("serial") or 0)
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for entry in entries:
        entry_id = int(entry.get("id") or entry.get("serial") or 0)
        if entry_id == target_id:
            continue
        score = local_score(target, entry)
        if score <= 0:
            continue
        scored.append((score, entry))

    scored.sort(
        key=lambda item: (-item[0], int(item[1].get("serial", 0)), int(item[1].get("id", 0)))
    )
    return [item[1] for item in scored[:candidate_k]]


def make_prompt(target: Dict[str, Any], candidates: List[Dict[str, Any]], top_k: int) -> str:
    target_id = int(target.get("id") or target.get("serial") or 0)
    lines: List[str] = []
    lines.append(f"Target ID: {target_id}")
    lines.append(f"Target citation: {clean(target.get('caseReference', ''))}")
    lines.append(f"Target category: {clean(target.get('category', ''))}")
    lines.append(f"Target sub-category: {clean(target.get('subCategory', ''))}")
    lines.append(f"Target stage: {clean(target.get('stage', ''))}")
    lines.append(f"Target verdict: {compact_text(target.get('issue', ''), 320)}")
    lines.append(f"Target impact: {compact_text(target.get('holding', ''), 480)}")
    lines.append("")
    lines.append(f"Pick exactly {top_k} related candidate IDs from this list:")

    for entry in candidates:
        entry_id = int(entry.get("id") or entry.get("serial") or 0)
        lines.append(
            f"- ID {entry_id} | {clean(entry.get('caseReference', ''))} | "
            f"{clean(entry.get('category', ''))} -> {clean(entry.get('subCategory', ''))} | "
            f"Stage: {clean(entry.get('stage', ''))}"
        )
        lines.append(f"  Verdict: {compact_text(entry.get('issue', ''), 180)}")
        lines.append(f"  Impact: {compact_text(entry.get('holding', ''), 220)}")

    return "\n".join(lines)


def call_llm_related_ids(
    api_key: str,
    model: str,
    target: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    top_k: int,
) -> List[int]:
    parsed = call_gemini_json(
        api_key=api_key,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=make_prompt(target, candidates, top_k),
        temperature=0.1,
        timeout=90,
    )

    raw_related = parsed.get("related")
    if not isinstance(raw_related, list):
        return []

    candidate_ids = {
        int(entry.get("id") or entry.get("serial") or 0)
        for entry in candidates
        if int(entry.get("id") or entry.get("serial") or 0) > 0
    }
    target_id = int(target.get("id") or target.get("serial") or 0)
    seen: Set[int] = set()
    out: List[int] = []

    for item in raw_related:
        try:
            value = int(item)
        except Exception:  # noqa: BLE001
            continue
        if value == target_id or value not in candidate_ids or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= top_k:
            break

    return out


def load_existing_related(path: Path) -> Dict[str, List[int]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    related = raw.get("related", {})
    if not isinstance(related, dict):
        return {}
    normalized: Dict[str, List[int]] = {}
    for key, value in related.items():
        try:
            key_num = int(key)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(value, list):
            continue
        ids = []
        for item in value:
            try:
                ids.append(int(item))
            except Exception:  # noqa: BLE001
                continue
        normalized[str(key_num)] = ids
    return normalized


def write_output(
    output_file: Path,
    related: Dict[str, List[int]],
    data_source_name: str,
    model: str,
    top_k: int,
    candidate_k: int,
    total_entries: int,
) -> None:
    payload = {
        "meta": {
            "source": data_source_name,
            "generatedOn": str(date.today()),
            "model": model,
            "totalEntries": total_entries,
            "topK": top_k,
            "candidateK": candidate_k,
        },
        "related": dict(sorted(related.items(), key=lambda item: int(item[0]))),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main() -> int:
    args = parse_args()
    if args.top_k < 1 or args.candidate_k < args.top_k:
        print("Invalid parameters: candidate-k must be >= top-k and top-k >= 1.", file=sys.stderr)
        return 1

    data_file = Path(args.data_file).resolve()
    output_file = Path(args.output_file).resolve()
    if not data_file.exists():
        print(f"Data file not found: {data_file}", file=sys.stderr)
        return 1

    raw = json.loads(data_file.read_text(encoding="utf-8"))
    entries = raw.get("entries", [])
    if not isinstance(entries, list) or not entries:
        print("Invalid data file format: non-empty `entries` list required.", file=sys.stderr)
        return 1

    entries = [entry for entry in entries if int(entry.get("id") or entry.get("serial") or 0) > 0]
    if not entries:
        print("No valid entries with id/serial found.", file=sys.stderr)
        return 1

    existing = {} if args.overwrite else load_existing_related(output_file)

    api_key = load_gemini_api_key(args.gemini_api_key)
    if not api_key:
        print(
            "Gemini API key not found. Set GEMINI_API_KEY or add local scripts/.gemini_api_key (do not commit secrets).",
            file=sys.stderr,
        )
        return 1

    process_entries = entries
    if args.limit and args.limit > 0:
        process_entries = entries[: args.limit]

    if args.dry_run:
        target = process_entries[0]
        candidates = shortlist_candidates(entries, target, args.candidate_k)
        print(make_prompt(target, candidates, args.top_k))
        return 0

    related: Dict[str, List[int]] = dict(existing)
    total = len(process_entries)

    for idx, target in enumerate(process_entries, start=1):
        target_id = int(target.get("id") or target.get("serial") or 0)
        target_key = str(target_id)
        if target_key in related and related[target_key] and not args.overwrite:
            print(f"[{idx}/{total}] ID {target_id}: kept existing mapping.")
            continue

        candidates = shortlist_candidates(entries, target, args.candidate_k)
        if not candidates:
            related[target_key] = []
            print(f"[{idx}/{total}] ID {target_id}: no candidates found.")
            continue

        try:
            llm_ids = call_llm_related_ids(
                api_key=api_key,
                model=args.model,
                target=target,
                candidates=candidates,
                top_k=args.top_k,
            )
        except RuntimeError as exc:
            print(f"[{idx}/{total}] ID {target_id}: LLM error -> {exc}", file=sys.stderr)
            return 1

        if not llm_ids:
            llm_ids = [
                int(entry.get("id") or entry.get("serial") or 0)
                for entry in candidates[: args.top_k]
                if int(entry.get("id") or entry.get("serial") or 0) > 0
            ]

        related[target_key] = llm_ids
        print(f"[{idx}/{total}] ID {target_id}: {llm_ids}")

        if idx % 10 == 0:
            write_output(
                output_file=output_file,
                related=related,
                data_source_name=data_file.name,
                model=args.model,
                top_k=args.top_k,
                candidate_k=args.candidate_k,
                total_entries=len(entries),
            )

    write_output(
        output_file=output_file,
        related=related,
        data_source_name=data_file.name,
        model=args.model,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        total_entries=len(entries),
    )
    print(f"Wrote related map: {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
