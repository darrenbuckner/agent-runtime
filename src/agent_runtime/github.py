"""GitHub Issues, used only by the email review gate. File reads and
writes do not go through here any more; they go through the checkout
and one git push per cycle.

Auth is the workflow's GITHUB_TOKEN (passed as GH_TOKEN), scoped by the
workflow's `permissions:` block to contents and issues on this repo.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from .errors import ApiError, ConfigError

API = "https://api.github.com"


class GitHub:
    def __init__(self, repo: str | None = None, token: str | None = None, session: Any | None = None):
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
        if not self.repo or "/" not in self.repo:
            raise ConfigError("GITHUB_REPOSITORY (owner/name) is not set")
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
        if not self.token:
            raise ConfigError("GH_TOKEN is not set (pass secrets.GITHUB_TOKEN from the workflow)")
        self.http = session or requests.Session()
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, ok: tuple[int, ...], **kwargs: Any) -> Any:
        resp = self.http.request(method, f"{API}{path}", headers=self.headers, timeout=30, **kwargs)
        if resp.status_code not in ok:
            raise ApiError(f"GitHub {method} {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else None

    # --- labels -------------------------------------------------------------

    def ensure_label(self, name: str, color: str, description: str) -> None:
        resp = self.http.get(f"{API}/repos/{self.repo}/labels/{name}", headers=self.headers, timeout=30)
        if resp.status_code == 200:
            return
        if resp.status_code != 404:
            raise ApiError(f"GitHub GET label {name} -> {resp.status_code}: {resp.text[:300]}")
        self._request(
            "POST",
            f"/repos/{self.repo}/labels",
            ok=(201,),
            json={"name": name, "color": color, "description": description},
        )

    # --- issues -------------------------------------------------------------

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        data = self._request(
            "POST",
            f"/repos/{self.repo}/issues",
            ok=(201,),
            json={"title": title, "body": body, "labels": labels},
        )
        return int(data["number"])

    def list_open_issues(self, labels: list[str]) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            f"/repos/{self.repo}/issues",
            ok=(200,),
            params={"labels": ",".join(labels), "state": "open", "per_page": 50},
        )
        return [i for i in data if "pull_request" not in i]

    def comment(self, number: int, body: str) -> None:
        self._request("POST", f"/repos/{self.repo}/issues/{number}/comments", ok=(201,), json={"body": body})

    def close(self, number: int) -> None:
        self._request(
            "PATCH",
            f"/repos/{self.repo}/issues/{number}",
            ok=(200,),
            json={"state": "closed", "state_reason": "completed"},
        )
