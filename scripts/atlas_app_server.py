#!/usr/bin/env python3
"""
Local Atlas app server:
- Serves project files
- Exposes GET /health for health checks
- Exposes GET /warmup to prime cache and dependencies
- Exposes POST /api/ruilings/add for one-click ruiling add from UI
- Exposes POST /api/ruilings/edit for in-place ruiling edits from UI
- Exposes POST /api/ruilings/delete for deletion from UI
- Exposes POST /api/ruilings/search for Gemini-powered semantic search
- Persists via GitHub Contents API when GITHUB_* env vars are configured
"""

from __future__ import annotations

import json
import threading
import argparse
import re
import os
import time
from collections import OrderedDict
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from add_ruiling_with_llm import (
    DEFAULT_DB_PATH,
    add_ruiling_to_payload,
    delete_ruiling_from_payload,
    update_ruiling_in_payload,
)
from gemini_client import call_gemini_json, load_gemini_api_key
from gemini_config import DEFAULT_GEMINI_MODEL
from github_repo_store import GitHubRepoStore, GitHubRepoStoreConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_LOCK = threading.Lock()
SEARCH_SYSTEM_PROMPT = (
    "You are a legal research ranking assistant for advocates. "
    "Given a query and compact case-note entries, return strict JSON with key `rankedIds` only. "
    "Rules: "
    "1) rankedIds must be a list of entry ids sorted by legal relevance (most relevant first). "
    "2) Prefer direct issue-match, section/statute overlap, procedural stage fit, and practical utility in court argument. "
    "3) Include only ids present in supplied entries. "
    "4) No markdown and no extra keys."
)


def parse_allowed_origins() -> set[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "")
    out = set()
    for item in raw.split(","):
        cleaned = str(item or "").strip().rstrip("/")
        if cleaned:
            out.add(cleaned)
    return out


ALLOWED_ORIGINS = parse_allowed_origins()


def parse_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except Exception:  # noqa: BLE001
        value = default
    return max(minimum, min(value, maximum))


def parse_env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def clean(value: Any) -> str:
    return str(value or "").strip()


def load_repo_store_from_env() -> GitHubRepoStore | None:
    token = str(os.getenv("GITHUB_TOKEN", "") or "").strip()
    owner = str(os.getenv("GITHUB_OWNER", "") or "").strip()
    repo = str(os.getenv("GITHUB_REPO", "") or "").strip()
    branch = str(os.getenv("GITHUB_BRANCH", "main") or "").strip() or "main"
    data_path = str(os.getenv("GITHUB_DATA_PATH", "assets/data/ruilings.json") or "").strip()
    data_path = data_path.lstrip("/") or "assets/data/ruilings.json"

    filled = [bool(token), bool(owner), bool(repo)]
    if any(filled) and not all(filled):
        raise RuntimeError(
            "Partial GitHub persistence configuration. Set GITHUB_TOKEN, GITHUB_OWNER, and GITHUB_REPO together."
        )
    if not all(filled):
        return None

    timeout_raw = str(os.getenv("GITHUB_TIMEOUT_SECONDS", "30") or "").strip() or "30"
    try:
        timeout = int(timeout_raw)
    except Exception:  # noqa: BLE001
        timeout = 30
    timeout = max(5, min(timeout, 120))

    return GitHubRepoStore(
        GitHubRepoStoreConfig(
            token=token,
            owner=owner,
            repo=repo,
            branch=branch,
            data_path=data_path,
            timeout=timeout,
        )
    )


REPO_STORE = load_repo_store_from_env()
PERSISTENCE_MODE = "github" if REPO_STORE else "local_file"
PERSISTENCE_DATA_PATH = REPO_STORE.config.data_path if REPO_STORE else str(DEFAULT_DB_PATH)

DB_CACHE_TTL_SECONDS = parse_env_int("DB_CACHE_TTL_SECONDS", 90, minimum=5, maximum=600)
SEARCH_LLM_TIMEOUT_SECONDS = parse_env_int("SEARCH_LLM_TIMEOUT_SECONDS", 18, minimum=4, maximum=60)
SEARCH_LLM_CANDIDATE_LIMIT = parse_env_int("SEARCH_LLM_CANDIDATE_LIMIT", 42, minimum=10, maximum=120)
SEARCH_CACHE_TTL_SECONDS = parse_env_int("SEARCH_CACHE_TTL_SECONDS", 600, minimum=30, maximum=3600)
SEARCH_CACHE_MAX_ENTRIES = parse_env_int("SEARCH_CACHE_MAX_ENTRIES", 120, minimum=20, maximum=500)
SEARCH_DISABLE_LLM = parse_env_bool("SEARCH_DISABLE_LLM", default=False)

_DB_CACHE_PAYLOAD: Dict[str, Any] | None = None
_DB_CACHE_SHA: str | None = None
_DB_CACHE_AT = 0.0
_SEARCH_CACHE: OrderedDict[str, tuple[float, List[int], bool, bool]] = OrderedDict()


def clear_search_cache() -> None:
    _SEARCH_CACHE.clear()


def get_search_cache(cache_key: str) -> tuple[List[int], bool, bool] | None:
    cached = _SEARCH_CACHE.get(cache_key)
    if not cached:
        return None

    stored_at, ranked_ids, llm_fallback_used, llm_attempted = cached
    if time.time() - stored_at > SEARCH_CACHE_TTL_SECONDS:
        _SEARCH_CACHE.pop(cache_key, None)
        return None

    _SEARCH_CACHE.move_to_end(cache_key)
    return list(ranked_ids), bool(llm_fallback_used), bool(llm_attempted)


def put_search_cache(cache_key: str, ranked_ids: List[int], llm_fallback_used: bool, llm_attempted: bool) -> None:
    _SEARCH_CACHE[cache_key] = (
        time.time(),
        list(ranked_ids),
        bool(llm_fallback_used),
        bool(llm_attempted),
    )
    _SEARCH_CACHE.move_to_end(cache_key)
    while len(_SEARCH_CACHE) > SEARCH_CACHE_MAX_ENTRIES:
        _SEARCH_CACHE.popitem(last=False)


def _read_local_db_payload() -> Dict[str, Any]:
    path = Path(DEFAULT_DB_PATH)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Invalid database structure: root JSON object missing.")
    return raw


def _read_repo_db_payload() -> tuple[Dict[str, Any], str | None]:
    payload, sha = REPO_STORE.read_json_file()
    if not isinstance(payload, dict):
        raise ValueError("Invalid database structure: root JSON object missing.")
    return payload, sha


def load_db_payload(*, force_refresh: bool = False) -> tuple[Dict[str, Any], str | None]:
    global _DB_CACHE_PAYLOAD, _DB_CACHE_SHA, _DB_CACHE_AT

    if REPO_STORE:
        cache_is_fresh = (
            not force_refresh
            and _DB_CACHE_PAYLOAD is not None
            and (time.time() - _DB_CACHE_AT) <= DB_CACHE_TTL_SECONDS
        )
        if cache_is_fresh:
            return _DB_CACHE_PAYLOAD, _DB_CACHE_SHA

        payload, sha = _read_repo_db_payload()
        _DB_CACHE_PAYLOAD = payload
        _DB_CACHE_SHA = sha
        _DB_CACHE_AT = time.time()
        return payload, sha

    payload = _read_local_db_payload()
    return payload, None


def persist_db_payload(payload: Dict[str, Any], *, sha: str | None, commit_message: str) -> None:
    global _DB_CACHE_PAYLOAD, _DB_CACHE_SHA, _DB_CACHE_AT

    if REPO_STORE:
        if not sha:
            raise RuntimeError("Missing GitHub file SHA for repository update.")
        response = REPO_STORE.write_json_file(content=payload, sha=sha, commit_message=commit_message)
        content_meta = response.get("content") if isinstance(response, dict) else {}
        next_sha = clean(content_meta.get("sha")) if isinstance(content_meta, dict) else ""
        _DB_CACHE_PAYLOAD = payload
        _DB_CACHE_SHA = next_sha or sha
        _DB_CACHE_AT = time.time()
        clear_search_cache()
        return

    path = Path(DEFAULT_DB_PATH)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    clear_search_cache()


def build_commit_message(action: str, serial: int, case_reference: str) -> str:
    head = clean(action) or "Update"
    ref = clean(case_reference)
    if len(ref) > 96:
        ref = f"{ref[:93].rstrip()}..."
    if ref:
        return f"{head} ruiling #{serial}: {ref}"
    return f"{head} ruiling #{serial}"


def clean_list(values: Any, *, max_items: int = 6, max_len: int = 70) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    seen = set()
    for value in values:
        text = clean(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text[:max_len])
        if len(out) >= max_items:
            break
    return out


def clip_text(value: Any, max_len: int) -> str:
    text = clean(value)
    if len(text) <= max_len:
        return text
    return f"{text[:max_len - 3].rstrip()}..."


def compact_entry_for_search(entry: Dict[str, Any]) -> Dict[str, Any]:
    entry_id = int(entry.get("id") or entry.get("serial") or 0)
    return {
        "id": entry_id,
        "serial": int(entry.get("serial") or entry_id),
        "caseReference": clip_text(entry.get("caseReference"), 220),
        "issue": clip_text(entry.get("issue"), 240),
        "holding": clip_text(entry.get("holding"), 320),
        "category": clip_text(entry.get("category"), 100),
        "subCategory": clip_text(entry.get("subCategory"), 100),
        "court": clip_text(entry.get("court"), 90),
        "stage": clip_text(entry.get("stage"), 70),
        "statuteTags": clean_list(entry.get("statuteTags"), max_items=6, max_len=60),
    }


def parse_top_k(value: Any, default: int = 80) -> int:
    try:
        top_k = int(value)
    except Exception:  # noqa: BLE001
        return default
    return max(5, min(top_k, 120))


def parse_ranked_ids(payload: Dict[str, Any], valid_ids: set[int], top_k: int) -> List[int]:
    ranked = payload.get("rankedIds")
    if not isinstance(ranked, list):
        return []

    out: List[int] = []
    seen = set()
    for raw in ranked:
        try:
            value = int(raw)
        except Exception:  # noqa: BLE001
            continue
        if value not in valid_ids or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= top_k:
            break
    return out


def merge_ranked_ids(primary: List[int], secondary: List[int], top_k: int) -> List[int]:
    out: List[int] = []
    seen = set()
    for bucket in (primary, secondary):
        for value in bucket:
            if value in seen:
                continue
            seen.add(value)
            out.append(value)
            if len(out) >= top_k:
                return out
    return out


def keyword_rank_ids(query: str, entries: List[Dict[str, Any]], top_k: int) -> List[int]:
    normalized_query = clean(query).lower()
    terms = [term for term in re.split(r"[^a-z0-9]+", normalized_query) if len(term) >= 2]
    if not terms and not normalized_query:
        return []

    scored = []
    for entry in entries:
        case_ref = clean(entry.get("caseReference")).lower()
        issue = clean(entry.get("issue")).lower()
        holding = clean(entry.get("holding")).lower()
        category = clean(entry.get("category")).lower()
        sub_category = clean(entry.get("subCategory")).lower()
        court = clean(entry.get("court")).lower()
        stage = clean(entry.get("stage")).lower()
        tags = " ".join(clean_list(entry.get("statuteTags"), max_items=8, max_len=80)).lower()

        score = 0.0
        if normalized_query:
            if normalized_query in case_ref:
                score += 10
            if normalized_query in issue:
                score += 8
            if normalized_query in holding:
                score += 7
            if normalized_query in tags:
                score += 7

        for term in terms:
            if term in case_ref:
                score += 2.8
            if term in issue:
                score += 2.2
            if term in holding:
                score += 1.7
            if term in tags:
                score += 1.9
            if term in category or term in sub_category:
                score += 1.2
            if term in stage or term in court:
                score += 0.8

        if score <= 0:
            continue

        scored.append((score, int(entry.get("id", 0)), int(entry.get("serial", 0))))

    scored.sort(key=lambda item: (-item[0], item[2], item[1]))
    return [item[1] for item in scored[:top_k]]


class AtlasHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/warmup":
            self._handle_warmup()
            return
        if parsed.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "atlas-app-server",
                    "status": "healthy",
                    "persistenceMode": PERSISTENCE_MODE,
                    "dataPath": PERSISTENCE_DATA_PATH,
                    "githubConfigured": bool(REPO_STORE),
                    "dbCacheTtlSeconds": DB_CACHE_TTL_SECONDS,
                    "searchLlmTimeoutSeconds": SEARCH_LLM_TIMEOUT_SECONDS,
                },
            )
            return
        super().do_GET()

    def do_OPTIONS(self) -> None:  # noqa: N802
        origin = clean(self.headers.get("Origin"))
        if not self._origin_is_allowed(origin):
            self._send_error_json(
                HTTPStatus.FORBIDDEN,
                "Origin is not allowed by CORS policy.",
                code="forbidden_origin",
            )
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_common_headers(content_type="application/json", origin=origin)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        origin = clean(self.headers.get("Origin"))
        if not self._origin_is_allowed(origin):
            self._send_error_json(
                HTTPStatus.FORBIDDEN,
                "Origin is not allowed by CORS policy.",
                code="forbidden_origin",
            )
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/ruilings/add":
            self._handle_add_ruiling()
            return
        if parsed.path == "/api/ruilings/edit":
            self._handle_edit_ruiling()
            return
        if parsed.path == "/api/ruilings/delete":
            self._handle_delete_ruiling()
            return
        if parsed.path == "/api/ruilings/search":
            self._handle_semantic_search()
            return
        self._send_error_json(
            HTTPStatus.NOT_FOUND,
            f"Endpoint not found: {parsed.path}",
            code="endpoint_not_found",
        )

    def _handle_warmup(self) -> None:
        started = time.perf_counter()
        warmed = {
            "dbLoaded": False,
            "entryCount": 0,
            "geminiKeyConfigured": bool(load_gemini_api_key("")),
        }

        try:
            with DB_LOCK:
                payload, _ = load_db_payload()
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            warmed["dbLoaded"] = True
            warmed["entryCount"] = len(entries) if isinstance(entries, list) else 0
            status = HTTPStatus.OK
        except Exception as exc:  # noqa: BLE001
            warmed["error"] = clean(str(exc)) or "Warmup failed."
            status = HTTPStatus.BAD_GATEWAY if REPO_STORE else HTTPStatus.INTERNAL_SERVER_ERROR

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        self._send_json(
            status,
            {
                "ok": status == HTTPStatus.OK,
                "service": "atlas-app-server",
                "status": "warmed" if status == HTTPStatus.OK else "warmup_failed",
                "persistenceMode": PERSISTENCE_MODE,
                "dataPath": PERSISTENCE_DATA_PATH,
                "elapsedMs": elapsed_ms,
                "warmed": warmed,
            },
        )

    def _handle_add_ruiling(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_json_payload")
            return

        case_reference = clean(payload.get("caseReference"))
        verdict = clean(payload.get("verdict"))
        impact = clean(payload.get("impact"))

        if not case_reference or not verdict or not impact:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "Required fields missing: caseReference, verdict, impact.",
                code="missing_required_fields",
            )
            return

        optional_fields: Dict[str, Any] = {
            "category": payload.get("category", ""),
            "subCategory": payload.get("subCategory", ""),
            "stage": payload.get("stage", ""),
            "court": payload.get("court", ""),
            "year": payload.get("year", ""),
            "statuteTags": payload.get("statuteTags", []),
            "advocateNotes": payload.get("advocateNotes", []),
            "relatedDetails": payload.get("relatedDetails", []),
        }

        model = clean(payload.get("model")) or DEFAULT_GEMINI_MODEL
        gemini_api_key = clean(payload.get("geminiApiKey"))
        dry_run = bool(payload.get("dryRun"))
        allow_llm_fallback = not bool(payload.get("noLlmFallback"))

        try:
            with DB_LOCK:
                db_payload, db_sha = load_db_payload()
                result = add_ruiling_to_payload(
                    db_payload=db_payload,
                    case_reference=case_reference,
                    verdict=verdict,
                    impact=impact,
                    model=model,
                    gemini_api_key=gemini_api_key,
                    optional_fields=optional_fields,
                    allow_llm_fallback=allow_llm_fallback,
                )
                if not dry_run:
                    commit_message = build_commit_message(
                        "Add",
                        int(result.get("serial") or 0),
                        case_reference,
                    )
                    persist_db_payload(
                        result["db"],
                        sha=db_sha,
                        commit_message=commit_message,
                    )
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_add_request")
            return
        except FileNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc), code="db_file_not_found")
            return
        except RuntimeError as exc:
            status = HTTPStatus.BAD_GATEWAY if REPO_STORE else HTTPStatus.INTERNAL_SERVER_ERROR
            code = "github_persistence_failed" if REPO_STORE else "add_ruiling_failed"
            self._send_error_json(status, str(exc), code=code)
            return
        except Exception as exc:  # noqa: BLE001
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc), code="add_ruiling_failed")
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "entry": result.get("entry"),
                "serial": result.get("serial"),
                "totalEntries": result.get("totalEntries"),
                "meta": result.get("meta"),
            },
        )

    def _handle_edit_ruiling(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_json_payload")
            return

        try:
            entry_id = int(payload.get("id") or payload.get("serial") or 0)
        except Exception:  # noqa: BLE001
            entry_id = 0

        case_reference = clean(payload.get("caseReference"))
        verdict = clean(payload.get("verdict"))
        impact = clean(payload.get("impact"))

        if entry_id <= 0:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "Required field missing: id.",
                code="missing_entry_id",
            )
            return

        if not case_reference or not verdict or not impact:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "Required fields missing: caseReference, verdict, impact.",
                code="missing_required_fields",
            )
            return

        optional_fields: Dict[str, Any] = {
            "category": payload.get("category", ""),
            "subCategory": payload.get("subCategory", ""),
            "stage": payload.get("stage", ""),
            "court": payload.get("court", ""),
            "year": payload.get("year", ""),
            "statuteTags": payload.get("statuteTags", []),
            "advocateNotes": payload.get("advocateNotes", []),
            "relatedDetails": payload.get("relatedDetails", []),
        }

        dry_run = bool(payload.get("dryRun"))

        try:
            with DB_LOCK:
                db_payload, db_sha = load_db_payload()
                result = update_ruiling_in_payload(
                    db_payload=db_payload,
                    entry_id=entry_id,
                    case_reference=case_reference,
                    verdict=verdict,
                    impact=impact,
                    optional_fields=optional_fields,
                )
                if not dry_run:
                    commit_message = build_commit_message(
                        "Edit",
                        int(result.get("serial") or entry_id),
                        case_reference,
                    )
                    persist_db_payload(
                        result["db"],
                        sha=db_sha,
                        commit_message=commit_message,
                    )
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_edit_request")
            return
        except FileNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc), code="db_file_not_found")
            return
        except RuntimeError as exc:
            status = HTTPStatus.BAD_GATEWAY if REPO_STORE else HTTPStatus.INTERNAL_SERVER_ERROR
            code = "github_persistence_failed" if REPO_STORE else "edit_ruiling_failed"
            self._send_error_json(status, str(exc), code=code)
            return
        except Exception as exc:  # noqa: BLE001
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc), code="edit_ruiling_failed")
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "entry": result.get("entry"),
                "serial": result.get("serial"),
                "totalEntries": result.get("totalEntries"),
                "meta": result.get("meta"),
            },
        )

    def _handle_delete_ruiling(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_json_payload")
            return

        try:
            entry_id = int(payload.get("id") or payload.get("serial") or 0)
        except Exception:  # noqa: BLE001
            entry_id = 0

        if entry_id <= 0:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "Required field missing: id.",
                code="missing_entry_id",
            )
            return

        dry_run = bool(payload.get("dryRun"))

        try:
            with DB_LOCK:
                db_payload, db_sha = load_db_payload()
                result = delete_ruiling_from_payload(
                    db_payload=db_payload,
                    entry_id=entry_id,
                )
                if not dry_run:
                    removed_entry = result.get("entry") or {}
                    case_reference = clean(removed_entry.get("caseReference")) or f"entry {entry_id}"
                    commit_message = build_commit_message(
                        "Delete",
                        int(result.get("serial") or entry_id),
                        case_reference,
                    )
                    persist_db_payload(
                        result["db"],
                        sha=db_sha,
                        commit_message=commit_message,
                    )
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_delete_request")
            return
        except FileNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc), code="db_file_not_found")
            return
        except RuntimeError as exc:
            status = HTTPStatus.BAD_GATEWAY if REPO_STORE else HTTPStatus.INTERNAL_SERVER_ERROR
            code = "github_persistence_failed" if REPO_STORE else "delete_ruiling_failed"
            self._send_error_json(status, str(exc), code=code)
            return
        except Exception as exc:  # noqa: BLE001
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc), code="delete_ruiling_failed")
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "deletedEntry": result.get("entry"),
                "serial": result.get("serial"),
                "totalEntries": result.get("totalEntries"),
                "meta": result.get("meta"),
            },
        )

    def _handle_semantic_search(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc), code="invalid_json_payload")
            return

        query = clean(payload.get("query"))
        if len(query) < 2:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "Query must contain at least 2 characters.",
                code="invalid_query",
            )
            return

        top_k = parse_top_k(payload.get("topK"), default=80)
        model = clean(payload.get("model")) or DEFAULT_GEMINI_MODEL
        gemini_api_key = clean(payload.get("geminiApiKey"))
        keyword_only = bool(payload.get("keywordOnly")) or SEARCH_DISABLE_LLM
        force_refresh = bool(payload.get("refresh"))
        cache_key = f"{query.lower()}::{top_k}::{model}::{1 if keyword_only else 0}"

        try:
            with DB_LOCK:
                if not force_refresh:
                    cached = get_search_cache(cache_key)
                    if cached:
                        cached_ids, cached_fallback, cached_llm_attempted = cached
                        self._send_json(
                            HTTPStatus.OK,
                            {
                                "ok": True,
                                "query": query,
                                "rankedIds": cached_ids,
                                "llmFallbackUsed": bool(cached_fallback),
                                "llmAttempted": bool(cached_llm_attempted),
                                "cacheHit": True,
                            },
                        )
                        return
                raw, _ = load_db_payload()
        except Exception as exc:  # noqa: BLE001
            status = HTTPStatus.BAD_GATEWAY if REPO_STORE else HTTPStatus.INTERNAL_SERVER_ERROR
            code = "github_read_failed" if REPO_STORE else "db_read_failed"
            self._send_error_json(status, str(exc), code=code)
            return

        entries = raw.get("entries", [])
        if not isinstance(entries, list):
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Invalid database structure: entries list missing.",
                code="invalid_db_structure",
            )
            return

        compact_entries = [
            compact_entry_for_search(entry)
            for entry in entries
            if int(entry.get("id") or entry.get("serial") or 0) > 0
        ]
        valid_ids = {int(item["id"]) for item in compact_entries if int(item["id"]) > 0}
        keyword_rank_cap = max(top_k, SEARCH_LLM_CANDIDATE_LIMIT)
        keyword_ids = keyword_rank_ids(query, compact_entries, keyword_rank_cap)

        ranked_ids: List[int] = keyword_ids[:top_k]
        llm_fallback_used = True
        llm_attempted = False

        if not keyword_only and valid_ids:
            api_key = load_gemini_api_key(gemini_api_key)
            if api_key:
                llm_attempted = True

                candidate_ids = keyword_ids[:SEARCH_LLM_CANDIDATE_LIMIT]
                if not candidate_ids:
                    candidate_ids = [int(item["id"]) for item in compact_entries[:SEARCH_LLM_CANDIDATE_LIMIT]]

                candidate_set = set(candidate_ids)
                candidate_entries = [
                    item for item in compact_entries if int(item.get("id") or 0) in candidate_set
                ]
                candidate_valid_ids = {int(item["id"]) for item in candidate_entries if int(item["id"]) > 0}

                user_prompt = (
                    f"User query:\n{query}\n\n"
                    f"Maximum ids to return: {top_k}\n\n"
                    "Entries JSON:\n"
                    f"{json.dumps(candidate_entries, ensure_ascii=False)}\n\n"
                    "Return JSON object with key rankedIds."
                )
                try:
                    llm_result = call_gemini_json(
                        api_key=api_key,
                        model=model,
                        system_prompt=SEARCH_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        temperature=0.1,
                        timeout=SEARCH_LLM_TIMEOUT_SECONDS,
                    )
                    llm_ids = parse_ranked_ids(llm_result, candidate_valid_ids, top_k)
                    if llm_ids:
                        ranked_ids = merge_ranked_ids(llm_ids, keyword_ids, top_k)
                        llm_fallback_used = False
                except Exception:  # noqa: BLE001
                    llm_fallback_used = True
            else:
                llm_fallback_used = True
                llm_attempted = False

        with DB_LOCK:
            put_search_cache(cache_key, ranked_ids, llm_fallback_used, llm_attempted)

        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "query": query,
                "rankedIds": ranked_ids,
                "llmFallbackUsed": llm_fallback_used,
                "llmAttempted": llm_attempted,
                "cacheHit": False,
            },
        )

    def _read_json_body(self) -> Dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            raise ValueError("Empty request body.")
        try:
            length = int(length_header)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Invalid Content-Length.") from exc
        if length <= 0:
            raise ValueError("Empty request body.")

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Invalid JSON payload.") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object.")
        return payload

    def _origin_is_allowed(self, origin: str) -> bool:
        normalized_origin = clean(origin).rstrip("/")
        if not normalized_origin:
            # Allow non-browser or same-origin requests without Origin header.
            return True
        if not ALLOWED_ORIGINS:
            # Local default when no explicit allowlist configured.
            return True
        return normalized_origin in ALLOWED_ORIGINS

    def _allowed_origin_value(self, origin: str) -> str | None:
        normalized_origin = clean(origin).rstrip("/")
        if normalized_origin and self._origin_is_allowed(normalized_origin):
            return normalized_origin
        if not ALLOWED_ORIGINS:
            return "*"
        return None

    def _send_common_headers(self, *, content_type: str, origin: str = "") -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        allowed_origin = self._allowed_origin_value(origin)
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

    def _send_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        origin = clean(self.headers.get("Origin"))
        self.send_response(status)
        self._send_common_headers(content_type="application/json; charset=utf-8", origin=origin)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus, message: str, *, code: str = "request_error") -> None:
        self._send_json(
            status,
            {
                "ok": False,
                "error": clean(message) or "Request failed.",
                "code": code,
                "status": int(status),
            },
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local Atlas app server.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=4173, help="Port to bind (default: 4173)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    host = args.host
    port = args.port
    server = ThreadingHTTPServer((host, port), AtlasHandler)
    print(f"Atlas app server running at http://{host}:{port}")
    print(f"Open http://{host}:{port}/ruilings")
    print(f"Health check: http://{host}:{port}/health")
    print(f"Warmup endpoint: http://{host}:{port}/warmup")
    print(f"Persistence mode: {PERSISTENCE_MODE} ({PERSISTENCE_DATA_PATH})")
    print(
        "Runtime tuning:"
        f" DB_CACHE_TTL_SECONDS={DB_CACHE_TTL_SECONDS},"
        f" SEARCH_LLM_TIMEOUT_SECONDS={SEARCH_LLM_TIMEOUT_SECONDS},"
        f" SEARCH_LLM_CANDIDATE_LIMIT={SEARCH_LLM_CANDIDATE_LIMIT},"
        f" SEARCH_CACHE_TTL_SECONDS={SEARCH_CACHE_TTL_SECONDS},"
        f" SEARCH_CACHE_MAX_ENTRIES={SEARCH_CACHE_MAX_ENTRIES},"
        f" SEARCH_DISABLE_LLM={SEARCH_DISABLE_LLM}"
    )
    if ALLOWED_ORIGINS:
        print(f"CORS allowlist active: {', '.join(sorted(ALLOWED_ORIGINS))}")
    else:
        print("CORS allowlist not set (ALLOWED_ORIGINS empty) -> allowing all origins.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
