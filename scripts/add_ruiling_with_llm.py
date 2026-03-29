#!/usr/bin/env python3
"""
Add a new ruiling entry to assets/data/ruilings.json using LLM enrichment.

Input format follows the same structure used in the source sheet:
1) Sl no. / Case Reference
2) Verdict
3) Impact

The script calls Gemini to infer category/sub-category/stage/tags/notes,
then appends a normalized entry and recomputes metadata counts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from gemini_client import call_gemini_json, load_gemini_api_key
from gemini_config import DEFAULT_GEMINI_MODEL


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "assets" / "data" / "ruilings.json"


def _parse_add_timeout_seconds() -> int:
    raw = str(os.getenv("ADD_LLM_TIMEOUT_SECONDS", "20") or "").strip() or "20"
    try:
        value = int(raw)
    except Exception:  # noqa: BLE001
        value = 20
    return max(5, min(value, 90))


ADD_LLM_TIMEOUT_SECONDS = _parse_add_timeout_seconds()


ALLOWED_CATEGORIES = [
    "Civil Procedure & Adjudication",
    "Evidence Law & Trial Proof",
    "Family & Matrimonial Law",
    "Criminal Procedure & Trial",
    "Property, Land & Tenancy",
    "Sexual Offences & Gender Justice",
    "Constitutional Rights in Litigation",
    "Commercial & Financial Litigation",
    "Legal Method & Professional Conduct",
    "General Litigation Principles",
]


SYSTEM_PROMPT = (
    "You are a legal taxonomy assistant. "
    "Classify one Indian case-law note into concise structured fields for advocate research. "
    "Return strict JSON with keys: category, subCategory, stage, court, year, statuteTags, advocateNotes. "
    "Rules: "
    "1) category must be one of these exact values: "
    + ", ".join(ALLOWED_CATEGORIES)
    + ". "
    "2) subCategory: short, specific, under 80 chars. "
    "3) stage: short litigation stage phrase under 60 chars. "
    "4) court: infer from citation text if possible; else 'Reported Court (See citation)'. "
    "5) year: integer or null. "
    "6) statuteTags: list of 1-6 short tags (sections/acts) if available. "
    "7) advocateNotes: list of exactly 2 concise practical notes. "
    "No markdown, no extra keys."
)


def clean(text: str) -> str:
    text = text or ""
    return re.sub(r"\s+", " ", text).strip()


def parse_year(value: Any) -> int | None:
    if value is None:
        return None
    text = clean(str(value))
    if not text:
        return None
    try:
        year = int(text)
    except Exception:  # noqa: BLE001
        return None
    if 1900 <= year <= 2100:
        return year
    return None


def split_csv_or_lines(value: Any, max_len: int) -> List[str]:
    text = clean(str(value))
    if not text:
        return []
    parts = re.split(r"[,\n\r;|]+", text)
    return safe_str_list(parts, max_len=max_len)


def normalize_case_reference_input(text: str) -> str:
    """
    Accept user input as "Sl no. / Case Reference" and normalize to case reference.
    Example:
      "132 - AIR 2024 SC 123 - X v Y" -> "AIR 2024 SC 123 - X v Y"
    """
    normalized = clean(text)
    if not normalized:
        return ""

    patterns = [
        r"^(?:sl\.?\s*no(?:s)?\.?\s*)?\d+\s*[:.)\-/]\s*",
        r"^(?:sl\.?\s*no(?:s)?\.?\s*)?\d+\s+",
    ]
    for pattern in patterns:
        candidate = re.sub(pattern, "", normalized, count=1, flags=re.IGNORECASE)
        candidate = clean(candidate)
        if candidate and candidate != normalized:
            return candidate

    return normalized


def infer_year(case_reference: str) -> int | None:
    match = re.search(r"(19|20)\d{2}", case_reference)
    if not match:
        return None
    year = int(match.group(0))
    if 1900 <= year <= 2100:
        return year
    return None


def infer_court(case_reference: str) -> str:
    text = clean(case_reference).lower()
    if "sc" in text or "supreme court" in text or "scc" in text:
        return "Supreme Court"
    if "cal" in text or "calcutta" in text:
        return "Calcutta High Court"
    if "high court" in text:
        return "High Court"
    return "Reported Court (See citation)"


def extract_statute_tags(text: str, max_len: int = 6) -> List[str]:
    base = clean(text).lower()
    tags = []
    patterns = [
        r"\bsec(?:tion)?\.?\s*\d+[a-z]?\b",
        r"\barticle\s*\d+[a-z]?\b",
        r"\border\s*[xivlcdm0-9]+\b",
        r"\brule\s*\d+[a-z]?\b",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, base, flags=re.IGNORECASE):
            tag = clean(match)
            if tag and tag.lower() not in {t.lower() for t in tags}:
                tags.append(tag)
            if len(tags) >= max_len:
                return tags
    return tags


def fallback_enrichment(case_reference: str, verdict: str, impact: str) -> Dict[str, Any]:
    combined = f"{verdict} {impact}"
    return {
        "category": "General Litigation Principles",
        "subCategory": "General Litigation Principles",
        "stage": "General",
        "court": infer_court(case_reference),
        "year": infer_year(case_reference),
        "statuteTags": extract_statute_tags(combined, max_len=6),
        "advocateNotes": [
            "Align this proposition with factual matrix before citation.",
            "Use together with latest binding precedent from jurisdiction.",
        ],
    }


def safe_str_list(value: Any, max_len: int) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        text = clean(str(item))
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max_len:
            break
    return out


def normalize_category(value: Any) -> str:
    text = clean(str(value))
    if text in ALLOWED_CATEGORIES:
        return text
    lower = text.lower()
    for category in ALLOWED_CATEGORIES:
        if category.lower() == lower:
            return category
    return "General Litigation Principles"


def llm_enrich(
    api_key: str,
    model: str,
    case_reference: str,
    verdict: str,
    impact: str,
) -> Dict[str, Any]:
    user_prompt = (
        "Sl no. / Case Reference:\n"
        f"{case_reference}\n\n"
        "Verdict:\n"
        f"{verdict}\n\n"
        "Impact:\n"
        f"{impact}\n"
    )
    return call_gemini_json(
        api_key=api_key,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.1,
        timeout=ADD_LLM_TIMEOUT_SECONDS,
    )


def recompute_meta(entries: List[Dict[str, Any]], source: str) -> Dict[str, Any]:
    by_category: Dict[str, int] = defaultdict(int)
    by_sub: Dict[str, int] = defaultdict(int)

    for entry in entries:
        category = clean(entry.get("category", "")) or "General Litigation Principles"
        sub = clean(entry.get("subCategory", "")) or "General Litigation Principles"
        by_category[category] += 1
        by_sub[f"{category}::{sub}"] += 1

    sub_breakdown = []
    for key, count in sorted(by_sub.items(), key=lambda item: (-item[1], item[0])):
        category, sub = key.split("::", 1)
        sub_breakdown.append(
            {"category": category, "subCategory": sub, "count": count}
        )

    category_counts = dict(sorted(by_category.items(), key=lambda item: (-item[1], item[0])))

    return {
        "source": source,
        "totalEntries": len(entries),
        "categoryCounts": category_counts,
        "subCategoryBreakdown": sub_breakdown,
        "generatedOn": str(date.today()),
    }


def build_entry(
    llm_data: Dict[str, Any],
    serial: int,
    case_reference: str,
    verdict: str,
    impact: str,
) -> Dict[str, Any]:
    category = normalize_category(llm_data.get("category"))
    sub_category = clean(str(llm_data.get("subCategory", ""))) or "General Litigation Principles"
    stage = clean(str(llm_data.get("stage", ""))) or "General"
    court = clean(str(llm_data.get("court", ""))) or "Reported Court (See citation)"

    year_val = llm_data.get("year")
    year: int | None
    if isinstance(year_val, int) and 1900 <= year_val <= 2100:
        year = year_val
    else:
        year = infer_year(case_reference)

    statute_tags = safe_str_list(llm_data.get("statuteTags"), max_len=6)
    if not statute_tags:
        statute_tags = []

    notes = safe_str_list(llm_data.get("advocateNotes"), max_len=3)
    while len(notes) < 2:
        notes.append("Apply this citation only after matching facts and statutory context.")

    return {
        "id": serial,
        "serial": serial,
        "caseReference": case_reference,
        "issue": verdict,
        "holding": impact,
        "category": category,
        "subCategory": sub_category,
        "court": court,
        "year": year,
        "stage": stage,
        "statuteTags": statute_tags,
        "advocateNotes": notes[:3],
    }


def apply_optional_overrides(entry: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(entry)

    category = clean(overrides.get("category", ""))
    if category:
        out["category"] = normalize_category(category)

    sub_category = clean(overrides.get("subCategory", ""))
    if sub_category:
        out["subCategory"] = sub_category

    stage = clean(overrides.get("stage", ""))
    if stage:
        out["stage"] = stage

    court = clean(overrides.get("court", ""))
    if court:
        out["court"] = court

    year = parse_year(overrides.get("year"))
    if year is not None:
        out["year"] = year

    statute_tags = overrides.get("statuteTags")
    if isinstance(statute_tags, list):
        parsed_tags = safe_str_list(statute_tags, max_len=8)
    else:
        parsed_tags = split_csv_or_lines(statute_tags, max_len=8)
    if parsed_tags:
        out["statuteTags"] = parsed_tags

    advocate_notes = overrides.get("advocateNotes")
    if isinstance(advocate_notes, list):
        parsed_notes = safe_str_list(advocate_notes, max_len=5)
    else:
        parsed_notes = split_csv_or_lines(advocate_notes, max_len=5)
    if parsed_notes:
        out["advocateNotes"] = parsed_notes

    related_details = overrides.get("relatedDetails")
    if isinstance(related_details, list):
        parsed_related = safe_str_list(related_details, max_len=8)
    else:
        parsed_related = split_csv_or_lines(related_details, max_len=8)
    if parsed_related:
        out["relatedDetails"] = parsed_related

    return out


def _validate_db_payload(db_payload: Dict[str, Any]) -> tuple[list[Dict[str, Any]], str]:
    if not isinstance(db_payload, dict):
        raise ValueError("Invalid database payload: object expected.")

    entries = db_payload.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("Invalid database payload: `entries` must be a list.")

    source = db_payload.get("meta", {}).get("source", "Untitled spreadsheet - Sheet1.csv")
    source = clean(source) or "Untitled spreadsheet - Sheet1.csv"
    return entries, source


def add_ruiling_to_payload(
    *,
    db_payload: Dict[str, Any],
    case_reference: str,
    verdict: str,
    impact: str,
    model: str,
    gemini_api_key: str = "",
    optional_fields: Dict[str, Any] | None = None,
    allow_llm_fallback: bool = True,
) -> Dict[str, Any]:
    case_reference = normalize_case_reference_input(case_reference)
    verdict = clean(verdict)
    impact = clean(impact)

    if not case_reference or not verdict or not impact:
        raise ValueError("All required fields are mandatory: case_reference, verdict, impact.")

    entries, source = _validate_db_payload(db_payload)
    serial = max((int(item.get("serial", 0)) for item in entries), default=0) + 1

    api_key = load_gemini_api_key(gemini_api_key)
    if not api_key:
        raise ValueError(
            "Gemini API key not found. Set GEMINI_API_KEY or add local scripts/.gemini_api_key (do not commit secrets)."
        )

    llm_failed = False
    try:
        llm_data = llm_enrich(
            api_key=api_key,
            model=model,
            case_reference=case_reference,
            verdict=verdict,
            impact=impact,
        )
    except Exception:
        if not allow_llm_fallback:
            raise
        llm_data = fallback_enrichment(case_reference, verdict, impact)
        llm_failed = True

    entry = build_entry(
        llm_data=llm_data,
        serial=serial,
        case_reference=case_reference,
        verdict=verdict,
        impact=impact,
    )

    if optional_fields:
        entry = apply_optional_overrides(entry, optional_fields)
    if llm_failed:
        entry["llmFallbackUsed"] = True

    updated_entries = [*entries, entry]
    meta = recompute_meta(updated_entries, source=source)
    updated_db = dict(db_payload)
    updated_db["meta"] = meta
    updated_db["entries"] = updated_entries

    return {
        "db": updated_db,
        "entry": entry,
        "serial": serial,
        "meta": meta,
        "totalEntries": len(updated_entries),
    }


def update_ruiling_in_payload(
    *,
    db_payload: Dict[str, Any],
    entry_id: int,
    case_reference: str,
    verdict: str,
    impact: str,
    optional_fields: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    case_reference = normalize_case_reference_input(case_reference)
    verdict = clean(verdict)
    impact = clean(impact)

    if entry_id <= 0:
        raise ValueError("entry_id must be a positive integer.")
    if not case_reference or not verdict or not impact:
        raise ValueError("All required fields are mandatory: case_reference, verdict, impact.")

    entries, source = _validate_db_payload(db_payload)

    match_index = -1
    for idx, item in enumerate(entries):
        item_id = int(item.get("id") or item.get("serial") or 0)
        if item_id == entry_id:
            match_index = idx
            break

    if match_index < 0:
        raise ValueError(f"Entry not found for id/serial: {entry_id}")

    existing = dict(entries[match_index])
    serial = int(existing.get("serial") or existing.get("id") or entry_id)

    updated: Dict[str, Any] = {
        **existing,
        "id": serial,
        "serial": serial,
        "caseReference": case_reference,
        "issue": verdict,
        "holding": impact,
    }

    if optional_fields:
        updated = apply_optional_overrides(updated, optional_fields)

    updated["category"] = normalize_category(updated.get("category"))
    updated["subCategory"] = clean(str(updated.get("subCategory", ""))) or "General Litigation Principles"
    updated["stage"] = clean(str(updated.get("stage", ""))) or "General"
    updated["court"] = clean(str(updated.get("court", ""))) or infer_court(case_reference)

    parsed_year = parse_year(updated.get("year"))
    updated["year"] = parsed_year if parsed_year is not None else infer_year(case_reference)
    updated["statuteTags"] = safe_str_list(updated.get("statuteTags"), max_len=8)

    notes = safe_str_list(updated.get("advocateNotes"), max_len=5)
    while len(notes) < 2:
        notes.append("Apply this citation only after matching facts and statutory context.")
    updated["advocateNotes"] = notes[:5]
    updated["relatedDetails"] = safe_str_list(updated.get("relatedDetails"), max_len=8)

    updated_entries = list(entries)
    updated_entries[match_index] = updated
    meta = recompute_meta(updated_entries, source=source)

    updated_db = dict(db_payload)
    updated_db["meta"] = meta
    updated_db["entries"] = updated_entries

    return {
        "db": updated_db,
        "entry": updated,
        "serial": serial,
        "meta": meta,
        "totalEntries": len(updated_entries),
    }


def delete_ruiling_from_payload(
    *,
    db_payload: Dict[str, Any],
    entry_id: int,
) -> Dict[str, Any]:
    if entry_id <= 0:
        raise ValueError("entry_id must be a positive integer.")

    entries, source = _validate_db_payload(db_payload)
    if not entries:
        raise ValueError("No entries available to delete.")

    match_index = -1
    for idx, item in enumerate(entries):
        item_id = int(item.get("id") or item.get("serial") or 0)
        if item_id == entry_id:
            match_index = idx
            break

    if match_index < 0:
        raise ValueError(f"Entry not found for id/serial: {entry_id}")

    removed_entry = dict(entries[match_index])
    serial = int(removed_entry.get("serial") or removed_entry.get("id") or entry_id)
    updated_entries = [item for idx, item in enumerate(entries) if idx != match_index]

    meta = recompute_meta(updated_entries, source=source)
    updated_db = dict(db_payload)
    updated_db["meta"] = meta
    updated_db["entries"] = updated_entries

    return {
        "db": updated_db,
        "entry": removed_entry,
        "serial": serial,
        "meta": meta,
        "totalEntries": len(updated_entries),
    }


def add_ruiling_to_db(
    *,
    case_reference: str,
    verdict: str,
    impact: str,
    model: str,
    data_file: str | Path = DEFAULT_DB_PATH,
    gemini_api_key: str = "",
    optional_fields: Dict[str, Any] | None = None,
    allow_llm_fallback: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    data_path = Path(data_file).resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Database file not found: {data_path}")

    raw = json.loads(data_path.read_text(encoding="utf-8"))
    result = add_ruiling_to_payload(
        db_payload=raw,
        case_reference=case_reference,
        verdict=verdict,
        impact=impact,
        model=model,
        gemini_api_key=gemini_api_key,
        optional_fields=optional_fields,
        allow_llm_fallback=allow_llm_fallback,
    )
    response = {
        "entry": result["entry"],
        "serial": result["serial"],
        "path": str(data_path),
    }

    if dry_run:
        return response

    data_path.write_text(json.dumps(result["db"], indent=2, ensure_ascii=False), encoding="utf-8")
    response["meta"] = result["meta"]
    response["totalEntries"] = result["totalEntries"]
    return response


def update_ruiling_in_db(
    *,
    entry_id: int,
    case_reference: str,
    verdict: str,
    impact: str,
    data_file: str | Path = DEFAULT_DB_PATH,
    optional_fields: Dict[str, Any] | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    data_path = Path(data_file).resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Database file not found: {data_path}")

    raw = json.loads(data_path.read_text(encoding="utf-8"))
    result = update_ruiling_in_payload(
        db_payload=raw,
        entry_id=entry_id,
        case_reference=case_reference,
        verdict=verdict,
        impact=impact,
        optional_fields=optional_fields,
    )
    response = {
        "entry": result["entry"],
        "serial": result["serial"],
        "path": str(data_path),
    }

    if dry_run:
        return response

    data_path.write_text(json.dumps(result["db"], indent=2, ensure_ascii=False), encoding="utf-8")
    response["meta"] = result["meta"]
    response["totalEntries"] = result["totalEntries"]
    return response


def delete_ruiling_from_db(
    *,
    entry_id: int,
    data_file: str | Path = DEFAULT_DB_PATH,
    dry_run: bool = False,
) -> Dict[str, Any]:
    data_path = Path(data_file).resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Database file not found: {data_path}")

    raw = json.loads(data_path.read_text(encoding="utf-8"))
    result = delete_ruiling_from_payload(
        db_payload=raw,
        entry_id=entry_id,
    )
    response = {
        "entry": result["entry"],
        "serial": result["serial"],
        "path": str(data_path),
    }

    if dry_run:
        return response

    data_path.write_text(json.dumps(result["db"], indent=2, ensure_ascii=False), encoding="utf-8")
    response["meta"] = result["meta"]
    response["totalEntries"] = result["totalEntries"]
    return response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add a new ruiling entry using LLM enrichment."
    )
    parser.add_argument("--case-reference", required=True, help="Sl no. / Case Reference")
    parser.add_argument("--verdict", required=True, help="Verdict")
    parser.add_argument("--impact", required=True, help="Impact")
    parser.add_argument(
        "--data-file",
        default=str(DEFAULT_DB_PATH),
        help="Path to ruilings JSON database file.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_GEMINI_MODEL,
        help=f"Gemini model for enrichment (default: {DEFAULT_GEMINI_MODEL}).",
    )
    parser.add_argument(
        "--gemini-api-key",
        default="",
        help="Optional Gemini API key (else use config/env/file lookup).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print would-be entry without writing to disk.",
    )
    parser.add_argument("--category", default="", help="Optional category override.")
    parser.add_argument("--sub-category", default="", help="Optional sub-category override.")
    parser.add_argument("--stage", default="", help="Optional stage override.")
    parser.add_argument("--court", default="", help="Optional court override.")
    parser.add_argument("--year", default="", help="Optional year override.")
    parser.add_argument(
        "--statute-tags",
        default="",
        help="Optional statute tags (comma/newline separated).",
    )
    parser.add_argument(
        "--advocate-notes",
        default="",
        help="Optional advocate notes (comma/newline separated).",
    )
    parser.add_argument(
        "--related-details",
        default="",
        help="Optional related legal details (comma/newline separated).",
    )
    parser.add_argument(
        "--no-llm-fallback",
        action="store_true",
        help="Fail instead of adding with heuristic fallback when Gemini is unavailable.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {
        "category": args.category,
        "subCategory": args.sub_category,
        "stage": args.stage,
        "court": args.court,
        "year": args.year,
        "statuteTags": args.statute_tags,
        "advocateNotes": args.advocate_notes,
        "relatedDetails": args.related_details,
    }

    try:
        result = add_ruiling_to_db(
            case_reference=args.case_reference,
            verdict=args.verdict,
            impact=args.impact,
            model=args.model,
            data_file=args.data_file,
            gemini_api_key=args.gemini_api_key,
            optional_fields=overrides,
            allow_llm_fallback=not args.no_llm_fallback,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    entry = result["entry"]
    if args.dry_run:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0

    print(f"Added entry serial #{result['serial']} to {result['path']}")
    print(f"Category: {entry['category']} | Sub-category: {entry['subCategory']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
