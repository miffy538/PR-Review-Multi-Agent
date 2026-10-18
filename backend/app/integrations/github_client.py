"""Thin async GitHub REST wrapper: PR metadata, unified diff, file contents."""
import asyncio
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

PR_URL_RE = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)(?:[/?#].*)?$")


@dataclass(frozen=True)
class PRRef:
    owner: str
    repo: str
    number: int

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_pr_url(url: str) -> PRRef:
    m = PR_URL_RE.match(url.strip())
    if not m:
        raise ValueError("Expected a URL like https://github.com/<owner>/<repo>/pull/<number>")
    return PRRef(m.group(1), m.group(2), int(m.group(3)))


class GitHubError(Exception):
    """Raised with a human-readable message that is safe to show to the user."""


class GitHubClient:
    def __init__(self, token: str = "", base_url: str = "https://api.github.com",
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0):
        headers = {"User-Agent": "pr-review-agent", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(base_url=base_url, headers=headers,
                                       transport=transport, timeout=timeout)

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _get(self, path: str, accept: str, params: dict | None = None) -> httpx.Response:
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = await self._http.get(path, headers={"Accept": accept}, params=params)
            except httpx.TransportError as e:
                last_err = e
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code in (502, 503, 504) and attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            return r
        raise GitHubError(f"Network error talking to GitHub: {last_err}")

    @staticmethod
    def _check(r: httpx.Response, what: str) -> None:
        if r.status_code < 400:
            return
        if r.status_code == 404:
            raise GitHubError(f"{what} not found. If the repo is private, set GITHUB_TOKEN.")
        if r.status_code == 401:
            raise GitHubError("GitHub rejected the token (401). Check GITHUB_TOKEN.")
        if r.status_code in (403, 429):
            if r.headers.get("x-ratelimit-remaining") == "0":
                raise GitHubError("GitHub rate limit reached. Set GITHUB_TOKEN or wait.")
            raise GitHubError(f"GitHub denied access to {what} (403).")
        if r.status_code in (406, 422):
            raise GitHubError(f"{what} is too large for GitHub to return.")
        raise GitHubError(f"GitHub returned {r.status_code} for {what}.")

    async def get_pr(self, ref: PRRef) -> dict:
        r = await self._get(f"/repos/{ref.full_name}/pulls/{ref.number}", "application/vnd.github+json")
        self._check(r, f"Pull request {ref.full_name}#{ref.number}")
        return r.json()

    async def get_diff(self, ref: PRRef) -> str:
        r = await self._get(f"/repos/{ref.full_name}/pulls/{ref.number}", "application/vnd.github.diff")
        self._check(r, f"Diff for {ref.full_name}#{ref.number}")
        return r.text

    async def get_file(self, ref: PRRef, path: str, sha: str) -> str | None:
        """Full file content at a commit, or None if it doesn't exist there."""
        r = await self._get(f"/repos/{ref.full_name}/contents/{quote(path)}",
                            "application/vnd.github.raw+json", params={"ref": sha})
        if r.status_code == 404:
            return None
        self._check(r, f"File {path}")
        return r.text
