#!/usr/bin/env python3
"""
Enhance all atlas rulings with web-grounded Gemini research.

For each entry in assets/data/ruilings.json this script:
1) Uses Gemini with google_search grounding.
2) Improves legal drafting fields (issue/holding/notes/tags/category info).
3) Adds related legal details and web source URLs per entry.
4) Saves checkpoint updates progressively.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from gemini_client import load_gemini_api_key, parse_json_object
from gemini_config import DEFAULT_GEMINI_MODEL


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "assets" / "data" / "ruilings.json"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

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
    "You are a senior Indian legal research editor for courtroom preparation. "
    "Use web-grounded context to improve one case note for advocate use. "
    "Return ONLY a JSON object with keys: "
    "issue, holding, category, subCategory, stage, court, year, statuteTags, advocateNotes, relatedDetails. "
    "Rules: "
    "1) issue: concise legal question/proposition (max 60 words). "
    "2) holding: practical ratio with procedural/legal context (max 180 words). "
    "3) category must be exactly one of: "
    + ", ".join(ALLOWED_CATEGORIES)
    + ". "
    "4) subCategory: precise and short under 90 chars. "
    "5) stage: short litigation stage phrase under 60 chars. "
    "6) court: infer from citation if possible; else keep reported court style. "
    "7) year: integer or null. "
    "8) statuteTags: list of 1-8 short tags (acts/sections). "
    "9) advocateNotes: list of exactly 3 practical action notes. "
    "10) relatedDetails: list of 2-5 related legal links/topics (doctrines, parallel principles, procedural intersections). "
    "No markdown, no extra keys."
)


def clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def infer_year(case_reference: str) -> int | None:
    match = re.search(r"(19|20)\d{2}", case_reference)
    if not match:
        return None
    year = int(match.group(0))
    if 1900 <= year <= 2100:
        return year
    return None


def safe_str_list(value: Any, max_len: int) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        text = clean(item)
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
    text = clean(value)
    if text in ALLOWED_CATEGORIES:
        return text
    lower = text.lower()
    for category in ALLOWED_CATEGORIES:
        if category.lower() == lower:
            return category
    return "General Litigation Principles"


def build_ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def extract_retry_seconds(text: str) -> float | None:
    candidates = [
        re.search(r"retry in\s+([0-9.]+)s", text, flags=re.IGNORECASE),
        re.search(r'"retryDelay":\s*"([0-9.]+)s"', text, flags=re.IGNORECASE),
    ]
    for match in candidates:
        if not match:
            continue
        try:
            return float(match.group(1))
        except Exception:  # noqa: BLE001
            continue
    return None


def extract_grounding_sources(body: Dict[str, Any]) -> List[Dict[str, str]]:
    candidate = (body.get("candidates") or [{}])[0]
    grounding = candidate.get("groundingMetadata", {})
    chunks = grounding.get("groundingChunks") or []
    out: List[Dict[str, str]] = []
    seen = set()

    for chunk in chunks:
        web = (chunk or {}).get("web", {})
        uri = clean(web.get("uri"))
        title = clean(web.get("title"))
        if not uri or uri in seen:
            continue
        seen.add(uri)
        out.append({"url": uri, "title": title})

    return out


def call_grounded_enhancement(
    api_key: str,
    model: str,
    *,
    case_reference: str,
    issue: str,
    holding: str,
    category: str,
    sub_category: str,
    stage: str,
    court: str,
    year: int | None,
    statute_tags: List[str],
    advocate_notes: List[str],
    max_retries: int = 6,
) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    model_name = quote(clean(model), safe="")
    key_q = quote(clean(api_key), safe="")
    url = f"{GEMINI_BASE_URL}/{model_name}:generateContent?key={key_q}"

    user_prompt = (
        "Enhance this ruling entry with internet-grounded legal detail:\n\n"
        f"Case Reference:\n{case_reference}\n\n"
        f"Current Issue:\n{issue}\n\n"
        f"Current Holding:\n{holding}\n\n"
        f"Current Category/Sub-category:\n{category} / {sub_category}\n\n"
        f"Current Stage/Court/Year:\n{stage} / {court} / {year if year else 'null'}\n\n"
        f"Current Statute Tags:\n{', '.join(statute_tags) if statute_tags else 'None'}\n\n"
        f"Current Advocate Notes:\n{'; '.join(advocate_notes) if advocate_notes else 'None'}\n\n"
        "Output strict JSON only."
    )

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2},
    }

    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ssl_context = build_ssl_context()

    last_error = "Unknown Gemini failure."
    for attempt in range(1, max_retries + 1):
        try:
            with urlopen(req, timeout=90, context=ssl_context) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {details}"
            if exc.code == 429 and attempt < max_retries:
                wait = extract_retry_seconds(details) or min(40.0, 4.0 * attempt)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini enhancement failed ({last_error})") from exc
        except URLError as exc:
            last_error = f"Connection error: {exc.reason}"
            if attempt < max_retries:
                time.sleep(min(18.0, 3.0 * attempt))
                continue
            raise RuntimeError(f"Gemini enhancement failed ({last_error})") from exc

        candidate = (body.get("candidates") or [{}])[0]
        text = ""
        for part in (candidate.get("content", {}).get("parts") or []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text = part["text"]
                break

        parsed = parse_json_object(text)
        if not parsed:
            last_error = "Could not parse JSON object from grounded Gemini response."
            if attempt < max_retries:
                time.sleep(2.0)
                continue
            raise RuntimeError(last_error)

        return parsed, extract_grounding_sources(body)

    raise RuntimeError(f"Gemini enhancement failed ({last_error})")


def merge_entry(
    entry: Dict[str, Any],
    llm_data: Dict[str, Any],
    sources: List[Dict[str, str]],
    model: str,
) -> Dict[str, Any]:
    out = dict(entry)

    case_reference = clean(entry.get("caseReference"))
    fallback_year = infer_year(case_reference) or entry.get("year")

    out["issue"] = clean(llm_data.get("issue")) or clean(entry.get("issue"))
    out["holding"] = clean(llm_data.get("holding")) or clean(entry.get("holding"))
    out["category"] = normalize_category(llm_data.get("category") or entry.get("category"))
    out["subCategory"] = clean(llm_data.get("subCategory")) or clean(entry.get("subCategory")) or "General Litigation Principles"
    out["stage"] = clean(llm_data.get("stage")) or clean(entry.get("stage")) or "General"
    out["court"] = clean(llm_data.get("court")) or clean(entry.get("court")) or "Reported Court (See citation)"

    year_val = llm_data.get("year")
    if isinstance(year_val, int) and 1900 <= year_val <= 2100:
        out["year"] = year_val
    elif isinstance(fallback_year, int):
        out["year"] = fallback_year
    else:
        out["year"] = None

    statute_tags = safe_str_list(llm_data.get("statuteTags"), 8)
    if not statute_tags:
        statute_tags = safe_str_list(entry.get("statuteTags"), 8)
    out["statuteTags"] = statute_tags

    notes = safe_str_list(llm_data.get("advocateNotes"), 4)
    while len(notes) < 3:
        notes.append("Apply the principle only after matching material facts and statutory posture.")
    out["advocateNotes"] = notes[:4]

    related_details = safe_str_list(llm_data.get("relatedDetails"), 6)
    out["relatedDetails"] = related_details

    source_urls = [item["url"] for item in sources if clean(item.get("url"))]
    out["researchSources"] = source_urls[:8]
    out["webEnhancedOn"] = str(date.today())
    out["enhancedByModel"] = clean(model)

    return out


def recompute_meta(entries: List[Dict[str, Any]], source: str) -> Dict[str, Any]:
    by_category: Dict[str, int] = defaultdict(int)
    by_sub: Dict[str, int] = defaultdict(int)

    for entry in entries:
        category = clean(entry.get("category")) or "General Litigation Principles"
        sub = clean(entry.get("subCategory")) or "General Litigation Principles"
        by_category[category] += 1
        by_sub[f"{category}::{sub}"] += 1

    sub_breakdown = []
    for key, count in sorted(by_sub.items(), key=lambda item: (-item[1], item[0])):
        category, sub = key.split("::", 1)
        sub_breakdown.append({"category": category, "subCategory": sub, "count": count})

    category_counts = dict(sorted(by_category.items(), key=lambda item: (-item[1], item[0])))
    return {
        "source": source,
        "totalEntries": len(entries),
        "categoryCounts": category_counts,
        "subCategoryBreakdown": sub_breakdown,
        "generatedOn": str(date.today()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enhance all atlas rulings using Gemini web-grounded research."
    )
    parser.add_argument(
        "--data-file",
        default=str(DEFAULT_DB_PATH),
        help="Path to atlas rulings JSON file.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_GEMINI_MODEL,
        help=f"Gemini model name (default: {DEFAULT_GEMINI_MODEL}).",
    )
    parser.add_argument(
        "--gemini-api-key",
        default="",
        help="Optional Gemini API key (else use config/env/file lookup).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process first N entries only (default: all).",
    )
    parser.add_argument(
        "--start-serial",
        type=int,
        default=1,
        help="Start processing from this serial number (default: 1).",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=5,
        help="Write JSON file every N processed entries (default: 5).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-enhance entries even if already web-enhanced.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print first enhanced entry preview only, no writes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    api_key = load_gemini_api_key(args.gemini_api_key)
    if not api_key:
        print(
            "Gemini API key not found. Set GEMINI_API_KEY or add local scripts/.gemini_api_key (do not commit secrets).",
            file=sys.stderr,
        )
        return 1

    data_path = Path(args.data_file).resolve()
    if not data_path.exists():
        print(f"Data file not found: {data_path}", file=sys.stderr)
        return 1

    raw = json.loads(data_path.read_text(encoding="utf-8"))
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        print("Invalid data file: expected non-empty entries list.", file=sys.stderr)
        return 1

    entries_sorted = sorted(entries, key=lambda item: int(item.get("serial", 0)))
    if args.start_serial > 1:
        entries_sorted = [e for e in entries_sorted if int(e.get("serial", 0)) >= args.start_serial]
    if args.limit and args.limit > 0:
        entries_sorted = entries_sorted[: args.limit]

    if not entries_sorted:
        print("No entries matched the selected range.")
        return 0

    processed = 0
    total = len(entries_sorted)
    by_id = {int(e.get("id") or e.get("serial") or 0): e for e in entries}

    def save_snapshot(label: str) -> None:
        merged_entries = sorted(by_id.values(), key=lambda item: int(item.get("serial", 0)))
        source_name = clean((raw.get("meta") or {}).get("source")) or "Untitled spreadsheet - Sheet1.csv"
        raw["entries"] = merged_entries
        raw["meta"] = recompute_meta(merged_entries, source=source_name)
        if not args.dry_run:
            data_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        print(label)

    for idx, entry in enumerate(entries_sorted, start=1):
        entry_id = int(entry.get("id") or entry.get("serial") or 0)
        serial = int(entry.get("serial") or 0)

        if not args.overwrite and clean(entry.get("webEnhancedOn")) and entry.get("researchSources"):
            print(f"[{idx}/{total}] serial #{serial}: already enhanced, skipped.")
            continue

        try:
            llm_data, sources = call_grounded_enhancement(
                api_key=api_key,
                model=args.model,
                case_reference=clean(entry.get("caseReference")),
                issue=clean(entry.get("issue")),
                holding=clean(entry.get("holding")),
                category=clean(entry.get("category")),
                sub_category=clean(entry.get("subCategory")),
                stage=clean(entry.get("stage")),
                court=clean(entry.get("court")),
                year=entry.get("year") if isinstance(entry.get("year"), int) else None,
                statute_tags=safe_str_list(entry.get("statuteTags"), 8),
                advocate_notes=safe_str_list(entry.get("advocateNotes"), 4),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[{idx}/{total}] serial #{serial}: FAILED -> {exc}", file=sys.stderr)
            if processed and not args.dry_run:
                save_snapshot(f"  checkpoint saved after {processed} updates (before failure).")
            return 1

        updated_entry = merge_entry(entry, llm_data, sources, model=args.model)
        by_id[entry_id] = updated_entry
        processed += 1
        print(
            f"[{idx}/{total}] serial #{serial}: enhanced | "
            f"cat={updated_entry.get('category')} | sub={updated_entry.get('subCategory')} | "
            f"sources={len(updated_entry.get('researchSources') or [])}"
        )

        if args.dry_run:
            print(json.dumps(updated_entry, indent=2, ensure_ascii=False))
            return 0

        if processed and args.checkpoint_every > 0 and processed % args.checkpoint_every == 0:
            save_snapshot(f"  checkpoint saved after {processed} updates.")

    save_snapshot("  final snapshot saved.")

    print(f"Completed. Enhanced entries: {processed}/{total}")
    print(f"Updated file: {data_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
