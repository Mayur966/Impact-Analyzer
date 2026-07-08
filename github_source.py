"""Fetch two branches of a public GitHub repo as local 'before'/'after' folders.

Isolated for now — not wired into the engine or web page yet. No API key needed.
Requires the `git` command-line tool to be installed.
"""
import os
import shutil
import subprocess
import tempfile


class GitError(Exception):
    """Raised when a git operation fails (bad URL, missing repo/branch, git absent)."""


def _run_git(args, cwd=None):
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH (try: brew install git)")
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def _checkout_into(clone_dir, branch, dest):
    # Confirm the branch exists first, so we can give a clean "branch not found".
    try:
        _run_git(["rev-parse", "--verify", f"origin/{branch}"], cwd=clone_dir)
    except GitError:
        raise GitError(f"branch '{branch}' not found in the repository")
    _run_git(["checkout", "--quiet", branch], cwd=clone_dir)
    # Copy the working tree, minus git's own metadata.
    shutil.copytree(clone_dir, dest, ignore=shutil.ignore_patterns(".git"))


def fetch_branches(repo_url, base_branch, compare_branch, out_dir=None):
    """Clone `repo_url` and materialize two branches as folders.

    Returns (base_dir, compare_dir): base_dir holds the code from `base_branch`,
    compare_dir from `compare_branch` — ready to feed to the diff engine as
    "before"/"after". The intermediate git clone is removed before returning.

    If `out_dir` is given, the two folders are created inside it; otherwise a fresh
    temp dir is used (the caller is then responsible for deleting the returned
    folders). Raises GitError on a bad URL, missing repo, or missing branch.
    """
    if not repo_url or not (repo_url.startswith("http") or repo_url.startswith("git@")):
        raise GitError(f"'{repo_url}' does not look like a valid repository URL")

    owns_out = out_dir is None
    out_dir = out_dir or tempfile.mkdtemp(prefix="impact_gh_")
    os.makedirs(out_dir, exist_ok=True)
    base_dir = os.path.join(out_dir, "base")
    compare_dir = os.path.join(out_dir, "compare")

    clone_dir = tempfile.mkdtemp(prefix="impact_clone_")
    try:
        _run_git(["clone", "--quiet", repo_url, clone_dir])
        _checkout_into(clone_dir, base_branch, base_dir)
        _checkout_into(clone_dir, compare_branch, compare_dir)
    except Exception:
        if owns_out:
            shutil.rmtree(out_dir, ignore_errors=True)   # don't leave a half-built output
        raise
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)     # always drop the clone

    return base_dir, compare_dir


if __name__ == "__main__":
    import filecmp
    import sys

    # Default demo: octocat's tiny public repo; `change-the-title` edits index.html vs `main`.
    repo = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/octocat/Spoon-Knife"
    base = sys.argv[2] if len(sys.argv) > 2 else "main"
    compare = sys.argv[3] if len(sys.argv) > 3 else "change-the-title"

    print(f"Cloning {repo}\n  base = {base}   compare = {compare}\n")
    try:
        base_dir, compare_dir = fetch_branches(repo, base, compare)
    except GitError as e:
        print(f"Error: {e}")
        sys.exit(1)

    out_dir = os.path.dirname(base_dir)
    try:
        print(f"base    ({base}) -> {base_dir}")
        for name in sorted(os.listdir(base_dir)):
            print(f"    {name}")
        print(f"\ncompare ({compare}) -> {compare_dir}")
        for name in sorted(os.listdir(compare_dir)):
            print(f"    {name}")

        diff = filecmp.dircmp(base_dir, compare_dir).diff_files
        print(f"\nfiles that differ between the branches: {sorted(diff) or '(none)'}")
        for name in sorted(diff):
            print(f"\n--- {name} on {base} ---")
            print(open(os.path.join(base_dir, name)).read().strip()[:300])
            print(f"--- {name} on {compare} ---")
            print(open(os.path.join(compare_dir, name)).read().strip()[:300])
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
        print(f"\n(cleaned up {out_dir})")
