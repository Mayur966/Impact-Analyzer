"""Minimal GitHub REST client (stdlib only) for reading a PR and posting a comment.

Reads the token from GITHUB_TOKEN (or GH_TOKEN) — never hardcoded, mirroring how the
Gemini key is read from the environment.
"""
import json
import os
import urllib.error
import urllib.request

API = "https://api.github.com"


class GitHubError(Exception):
    """Raised for a missing token, a bad PR/repo, permission problems, or API failures."""


def get_token():
    """Return the GitHub token from the environment, or raise GitHubError if absent."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise GitHubError("no GitHub token found (set GITHUB_TOKEN)")
    return token


def parse_owner_repo(repo_url):
    """Extract (owner, repo) from a GitHub URL (https or ssh form)."""
    url = repo_url.strip().removesuffix(".git")
    if url.startswith("git@"):
        path = url.split(":", 1)[-1]
    elif "github.com/" in url:
        path = url.split("github.com/", 1)[1]
    else:
        raise GitHubError(f"could not parse owner/repo from '{repo_url}'")
    bits = path.strip("/").split("/")
    if len(bits) < 2 or not bits[0] or not bits[1]:
        raise GitHubError(f"could not parse owner/repo from '{repo_url}'")
    return bits[0], bits[1]


def _request(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "impact-analyzer")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            message = json.loads(detail).get("message", detail)
        except Exception:
            message = detail
        raise GitHubError(f"GitHub API {e.code}: {message}")
    except urllib.error.URLError as e:
        raise GitHubError(f"could not reach GitHub: {e.reason}")


def get_pull_request(repo_url, pr_number, token):
    """Fetch a PR object (has base/head refs and repos)."""
    owner, repo = parse_owner_repo(repo_url)
    return _request("GET", f"{API}/repos/{owner}/{repo}/pulls/{pr_number}", token)


def post_pr_comment(repo_url, pr_number, body, token):
    """Post `body` as a comment on the PR (PR comments are issue comments)."""
    owner, repo = parse_owner_repo(repo_url)
    url = f"{API}/repos/{owner}/{repo}/issues/{pr_number}/comments"
    return _request("POST", url, token, body={"body": body})
