"""GitHub connector — explicit actions only (no generic API passthrough)."""
from __future__ import annotations

import json
import os
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

import requests

from atlas_connectors.base import Connector, connector_action
from atlas_logging import get_logger
from atlas_policy import RiskClass

log = get_logger("connectors.github")

_GITHUB_AUTH = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
_GITHUB_API = "https://api.github.com"


class GitHubConnector(Connector):
    connector_id = "github"
    display_name = "GitHub"

    @property
    def scopes_requested(self) -> list[str]:
        return ["repo:read", "repo:write"]

    def scope_boundary_text(self) -> str:
        return (
            "Atlas can read your GitHub repositories and issues, create repositories and issues, "
            "and push local folders via git. "
            "It cannot delete repositories without typed confirmation, change billing, "
            "or access organization admin settings."
        )

    def connect(self) -> tuple[bool, str]:
        client_id = (os.environ.get("GITHUB_CLIENT_ID") or "").strip()
        client_secret = (os.environ.get("GITHUB_CLIENT_SECRET") or "").strip()
        if not client_id:
            return False, "Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in .env for OAuth."

        state = os.urandom(8).hex()
        redirect_uri = "http://127.0.0.1:8765/github/callback"
        params = urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.scopes_requested),
            "state": state,
        })
        auth_url = f"{_GITHUB_AUTH}?{params}"

        result: dict[str, Any] = {"code": None, "error": None}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                if qs.get("state", [""])[0] != state:
                    result["error"] = "OAuth state mismatch"
                else:
                    result["code"] = qs.get("code", [None])[0]
                    result["error"] = qs.get("error_description", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>You can close this window.</body></html>")

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 8765), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        webbrowser.open(auth_url)
        thread.join(timeout=120.0)
        server.server_close()

        code = result.get("code")
        if not code:
            return False, str(result.get("error") or "OAuth cancelled or timed out.")

        try:
            resp = requests.post(
                _GITHUB_TOKEN,
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            if not token:
                return False, data.get("error_description") or "No access token returned."
            self._token_store.save_token(
                self.connector_id,
                {"access_token": token, "token_type": data.get("token_type", "bearer")},
                scopes=self.scopes_requested,
            )
            return True, "GitHub connected."
        except Exception as exc:
            log.exception("GitHub OAuth failed")
            return False, str(exc)

    def disconnect(self) -> tuple[bool, str]:
        tok = self._token_store.load_token(self.connector_id)
        if tok and tok.get("access_token"):
            try:
                requests.delete(
                    f"{_GITHUB_API}/applications/{os.environ.get('GITHUB_CLIENT_ID', '')}/token",
                    auth=(os.environ.get("GITHUB_CLIENT_ID", ""), os.environ.get("GITHUB_CLIENT_SECRET", "")),
                    json={"access_token": tok["access_token"]},
                    timeout=15,
                )
            except Exception:
                pass
        self._token_store.delete_token(self.connector_id)
        return True, "GitHub disconnected."

    def _headers(self) -> dict[str, str]:
        tok = self._token_store.load_token(self.connector_id)
        if not tok or not tok.get("access_token"):
            raise RuntimeError("GitHub not connected")
        return {
            "Authorization": f"Bearer {tok['access_token']}",
            "Accept": "application/vnd.github+json",
        }

    def _api_post(self, path: str, payload: dict) -> dict:
        r = requests.post(f"{_GITHUB_API}{path}", headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def _api_get(self, path: str) -> dict:
        r = requests.get(f"{_GITHUB_API}{path}", headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    @connector_action("list_issues", RiskClass.READ_ONLY, description="List open issues in a repo")
    def list_issues(self, repo: str, *, state: str = "open") -> dict:
        owner, name = repo.split("/", 1)
        data = self._api_get(f"/repos/{owner}/{name}/issues?state={state}&per_page=20")
        return {"issues": data}

    @connector_action("create_issue", RiskClass.WRITE_SCOPED, description="Create a GitHub issue")
    def create_issue(self, repo: str, title: str, body: str = "") -> dict:
        owner, name = repo.split("/", 1)
        return self._api_post(
            f"/repos/{owner}/{name}/issues",
            {"title": title, "body": body},
        )

    @connector_action("list_repos", RiskClass.READ_ONLY, description="List your GitHub repositories")
    def list_repos(self, *, per_page: int = 30) -> dict:
        data = self._api_get(f"/user/repos?per_page={max(1, min(int(per_page), 100))}&sort=updated")
        repos = []
        for row in data if isinstance(data, list) else []:
            repos.append({
                "full_name": row.get("full_name"),
                "private": bool(row.get("private")),
                "html_url": row.get("html_url"),
            })
        return {"repos": repos}

    @connector_action("create_repo", RiskClass.WRITE_SCOPED, description="Create a new GitHub repository")
    def create_repo(
        self,
        name: str,
        *,
        private: bool = False,
        description: str = "",
    ) -> dict:
        payload = {
            "name": (name or "").strip(),
            "private": bool(private),
            "description": (description or "")[:350],
            "auto_init": False,
        }
        if not payload["name"]:
            raise ValueError("Repository name is required")
        return self._api_post("/user/repos", payload)

    @connector_action("push_folder", RiskClass.SHELL_DANGEROUS, description="Git init/add/commit/push a folder")
    def push_folder(
        self,
        folder_path: str,
        repo: str,
        *,
        commit_message: str = "Atlas commit",
        branch: str = "main",
    ) -> dict:
        from pathlib import Path

        from atlas_shell import shell_runner

        folder = Path(folder_path).expanduser().resolve()
        if not folder.is_dir():
            raise RuntimeError(f"Folder not found: {folder}")

        tok = self._token_store.load_token(self.connector_id)
        if not tok or not tok.get("access_token"):
            raise RuntimeError("GitHub not connected")
        token = str(tok["access_token"])
        repo = (repo or "").strip().strip("/")
        if "/" not in repo:
            raise ValueError("repo must be owner/name")

        remote = f"https://github.com/{repo}.git"
        msg = (commit_message or "Atlas commit").replace('"', "'")[:200]
        steps: list[tuple[str, str]] = []

        if not (folder / ".git").exists():
            steps.extend([
                ("git init", "git init"),
                (f"git branch -M {branch}", f"git branch -M {branch}"),
                (f'git remote add origin "{remote}"', f"git remote add origin {repo}"),
            ])
        else:
            steps.append(
                (f'git remote set-url origin "{remote}"', f"git remote set-url origin {repo}")
            )

        steps.extend([
            ("git add -A", "git add -A"),
            (f'git commit -m "{msg}"', f'git commit -m "{msg}"'),
        ])
        push_cmd = (
            f'git -c http.extraHeader="Authorization: Bearer {token}" '
            f"push -u origin {branch}"
        )
        steps.append((push_cmd, f"git push -u origin {branch} ({repo})"))

        outputs: list[str] = []
        for cmd, audit_detail in steps:
            result = shell_runner.run(cmd, cwd=str(folder), audit_detail=audit_detail)
            if result.get("denied"):
                raise PermissionError(result.get("reason") or "git command denied by policy")
            if not result.get("ok"):
                err = result.get("error") or (result.get("stderr") or "").strip()
                if "nothing to commit" in (result.get("stdout") or "").lower() + err.lower():
                    if "commit" in cmd:
                        continue
                if result.get("returncode") not in (0, None) and "commit" not in cmd:
                    raise RuntimeError(err or f"git step failed: {audit_detail}")
            out = (result.get("stdout") or "").strip()
            if out:
                outputs.append(out[:500])

        return {
            "repo": repo,
            "folder": str(folder),
            "branch": branch,
            "output": "\n".join(outputs)[-2000:],
        }

    @connector_action("delete_repo", RiskClass.IRREVERSIBLE, description="Delete a repository")
    def delete_repo(self, repo: str) -> dict:
        owner, name = repo.split("/", 1)
        r = requests.delete(
            f"{_GITHUB_API}/repos/{owner}/{name}",
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        return {"deleted": True, "repo": repo}
