from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class GitHubRepoStoreConfig:
    token: str
    owner: str
    repo: str
    branch: str = "main"
    data_path: str = "assets/data/ruilings.json"
    timeout: int = 30


class GitHubRepoStore:
    def __init__(self, config: GitHubRepoStoreConfig):
        self.config = config

    def read_json_file(self, path: str | None = None) -> Tuple[Dict[str, Any], str]:
        file_path = (path or self.config.data_path).strip()
        if not file_path:
            raise ValueError("GitHub file path is required.")

        endpoint = self._contents_endpoint(file_path, with_ref=True)
        payload = self._request_json("GET", endpoint)

        content = payload.get("content")
        sha = str(payload.get("sha") or "").strip()
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("GitHub response missing file content.")
        if not sha:
            raise RuntimeError("GitHub response missing file SHA.")

        normalized_content = content.replace("\n", "")
        try:
            decoded = base64.b64decode(normalized_content).decode("utf-8")
            parsed = json.loads(decoded)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Failed to decode/parse JSON content from GitHub.") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError("GitHub JSON file payload must be an object.")
        return parsed, sha

    def write_json_file(
        self,
        *,
        content: Dict[str, Any],
        sha: str,
        commit_message: str,
        path: str | None = None,
    ) -> Dict[str, Any]:
        file_path = (path or self.config.data_path).strip()
        commit_sha = str(sha or "").strip()
        message = str(commit_message or "").strip()

        if not file_path:
            raise ValueError("GitHub file path is required.")
        if not commit_sha:
            raise ValueError("GitHub file SHA is required for update.")
        if not message:
            raise ValueError("Commit message is required.")

        raw = json.dumps(content, indent=2, ensure_ascii=False).encode("utf-8")
        encoded = base64.b64encode(raw).decode("utf-8")

        payload = {
            "message": message,
            "content": encoded,
            "sha": commit_sha,
            "branch": self.config.branch,
        }
        endpoint = self._contents_endpoint(file_path, with_ref=False)
        return self._request_json("PUT", endpoint, payload=payload)

    def _contents_endpoint(self, file_path: str, *, with_ref: bool) -> str:
        owner = quote(self.config.owner, safe="")
        repo = quote(self.config.repo, safe="")
        path = quote(file_path.lstrip("/"), safe="/")
        endpoint = f"/repos/{owner}/{repo}/contents/{path}"
        if with_ref:
            endpoint = f"{endpoint}?ref={quote(self.config.branch, safe='')}"
        return endpoint

    def _request_json(
        self,
        method: str,
        endpoint: str,
        payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        url = f"https://api.github.com{endpoint}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.config.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "atlas-app-server",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url=url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API HTTP {exc.code}: {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"GitHub API connection error: {exc.reason}") from exc

        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("GitHub API returned non-JSON payload.") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("GitHub API JSON response must be an object.")
        return parsed
