import argparse
import ast
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Literal

from google.genai import errors, types
from pydantic import BaseModel

from detect_functions import build_dependency_map
from blast_radius import build_reverse_map, find_blast_radius, resolve_target, short_name
from diff_functions import diff_functions
from contracts import extract_expectations, extract_promises, find_broken_contracts
from gemini_common import (
    MODEL, MissingAPIKey, make_client, get_client, generate_json, token_counts, describe_api_error,
)
from github_source import fetch_branches, GitError
from report import diff_result_to_markdown, write_report
from github_api import GitHubError, get_token, get_pull_request, post_pr_comment


RANK = {"high": 0, "medium": 1, "low": 2}

SYSTEM_PROMPT = (
    "You are a code-impact risk analyzer. Given a proposed change to one function "
    "and the source of a function that depends on it, judge whether the dependent "
    "function will break or misbehave. Be concrete and concise."
)


class RiskAssessment(BaseModel):
    will_break: bool
    risk_level: Literal["high", "medium", "low"]
    reason: str


# --- Structured result types (for library / web use) ---

@dataclass
class BrokenContractInfo:
    expectation: str
    why: str
    line: str


@dataclass
class AffectedFunction:
    name: str                       # qualified, e.g. "math_utils.calculate_total"
    relation: str                   # "direct" or "indirect via ..."
    risk_level: str                 # "high" | "medium" | "low" | "unknown"
    will_break: bool
    reason: str
    broken_contracts: list = field(default_factory=list)   # list[BrokenContractInfo]


@dataclass
class AnalysisResult:
    target: str                     # qualified name of the changed function
    change: str                     # the change description
    affected: list = field(default_factory=list)           # list[AffectedFunction]
    usage: dict = field(default_factory=dict)              # {calls, input_tokens, output_tokens}


@dataclass
class ChangedFunction:
    name: str                       # qualified, e.g. "store.unit_price"
    diff: str                       # unified diff of before -> after
    affected: list = field(default_factory=list)           # list[AffectedFunction]


@dataclass
class DiffAnalysisResult:
    added: list = field(default_factory=list)              # short names only in "after"
    removed: list = field(default_factory=list)            # short names only in "before"
    modified: list = field(default_factory=list)           # short names whose source changed
    changed: list = field(default_factory=list)            # list[ChangedFunction], per modified fn
    usage: dict = field(default_factory=dict)
    label: str = ""


def get_function_sources(folder):
    """Map every qualified function name to its exact source code."""
    sources = {}
    for filename in sorted(os.listdir(folder)):
        if not filename.endswith(".py"):
            continue
        module = filename[:-3]
        path = os.path.join(folder, filename)
        with open(path, "r") as f:
            text = f.read()
        tree = ast.parse(text, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sources[f"{module}.{node.name}"] = ast.get_source_segment(text, node)
    return sources


def build_prompt(change_description, changed_name, changed_code, affected_name, affected_code):
    return f"""A function named `{changed_name}` is being changed.

Proposed change: {change_description}

Current code of `{changed_name}`:
```python
{changed_code}
```

`{affected_name}` depends on `{changed_name}` (directly or transitively). Here is its code:
```python
{affected_code}
```

Will this change break or cause incorrect behavior in `{affected_name}`? Reply with:
- will_break: true if it is likely to break or produce wrong results
- risk_level: "high", "medium", or "low"
- reason: one concise sentence explaining your judgement."""


def assess_risk(client, change_description, changed_name, changed_code, affected_name, affected_code):
    prompt = build_prompt(change_description, changed_name, changed_code, affected_name, affected_code)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",   # ask Gemini for JSON only
        response_schema=RiskAssessment,          # constrained to our Pydantic shape
    )
    response = generate_json(client, prompt, config)

    assessment = response.parsed
    if not isinstance(assessment, RiskAssessment):
        try:
            assessment = RiskAssessment.model_validate_json(response.text or "")
        except Exception:
            assessment = None
    return assessment, response.usage_metadata


def analyze_target(client, target, change_description, changed_code, reverse_map,
                   affected_sources, before_promises=None, after_promises=None):
    """Trace `target`'s blast radius and AI-assess each affected function.

    When before/after promises are supplied (diff mode), also run the semantic
    contract check between each affected function and `target`.

    Returns (affected, input_tokens, output_tokens, calls) where `affected` is a
    ranked list of AffectedFunction.
    """
    do_contracts = before_promises is not None and after_promises is not None
    nodes = find_blast_radius(target, reverse_map)
    affected = []
    total_input = total_output = calls = 0
    for affected_name, relation in nodes:
        broken = []
        try:
            assessment, usage = assess_risk(
                client, change_description,
                short_name(target), changed_code,
                short_name(affected_name), affected_sources[affected_name],
            )
            tin, tout = token_counts(usage)
            total_input += tin
            total_output += tout
            calls += 1

            if do_contracts:
                exp, u_exp = extract_expectations(
                    client, short_name(target), short_name(affected_name),
                    affected_sources[affected_name],
                )
                tin, tout = token_counts(u_exp)
                total_input += tin
                total_output += tout
                calls += 1

                check, u_cmp = find_broken_contracts(
                    client, short_name(affected_name), affected_sources[affected_name],
                    short_name(target), exp.expectations, before_promises, after_promises,
                )
                tin, tout = token_counts(u_cmp)
                total_input += tin
                total_output += tout
                calls += 1
                broken = [BrokenContractInfo(bc.expectation, bc.why, bc.line)
                          for bc in check.broken_contracts]
        except errors.APIError as e:
            reason = describe_api_error(e)
            print(f"  ! Could not assess {short_name(affected_name)}: {reason}")
            affected.append(AffectedFunction(affected_name, relation, "unknown", False,
                                             reason, []))
            continue

        if assessment is None:
            affected.append(AffectedFunction(affected_name, relation, "unknown", False,
                                             "model returned an unparseable response "
                                             "(no structured risk assessment)", broken))
        else:
            affected.append(AffectedFunction(affected_name, relation, assessment.risk_level,
                                             assessment.will_break, assessment.reason, broken))

    affected.sort(key=lambda f: RANK.get(f.risk_level, 3))
    return affected, total_input, total_output, calls


def analyze_change(codebase_folder, function_name, change_description):
    """Run the typed-description analysis and RETURN structured data (no printing).

    Returns an AnalysisResult. Raises ValueError for a bad folder or unknown function,
    and MissingAPIKey if no GEMINI_API_KEY is set (only when there is work to do). This
    is the library entry point the CLI and the future web UI both call.
    """
    if not os.path.isdir(codebase_folder):
        raise ValueError(f"'{codebase_folder}' is not a directory")

    dependency_map = build_dependency_map(codebase_folder)
    sources = get_function_sources(codebase_folder)
    reverse_map = build_reverse_map(dependency_map)

    targets = resolve_target(function_name, dependency_map)
    if not targets:
        raise ValueError(f"no function named '{function_name}' found in '{codebase_folder}'")
    target = targets[0]

    # No dependents → nothing to assess, and no API key needed.
    if not find_blast_radius(target, reverse_map):
        return AnalysisResult(target=target, change=change_description, affected=[],
                              usage={"calls": 0, "input_tokens": 0, "output_tokens": 0})

    client = get_client()   # raises MissingAPIKey if no key
    affected, tin, tout, calls = analyze_target(
        client, target, change_description, sources[target], reverse_map, sources
    )
    return AnalysisResult(
        target=target, change=change_description, affected=affected,
        usage={"calls": calls, "input_tokens": tin, "output_tokens": tout},
    )


def analyze_diff(before, after, label=None):
    """Compare two folders and RETURN structured results (no printing).

    Same pipeline the CLI diff mode uses (diff -> blast radius -> risk -> contracts),
    packaged for programmatic/web callers. Raises ValueError for a bad folder and
    MissingAPIKey if a key is needed (there are modified functions) but absent.
    """
    for folder in (before, after):
        if not os.path.isdir(folder):
            raise ValueError(f"'{folder}' is not a directory")

    added, removed, modified, diffs = diff_functions(before, after)
    result = DiffAnalysisResult(
        added=[short_name(n) for n in added],
        removed=[short_name(n) for n in removed],
        modified=[short_name(n) for n in modified],
        usage={"calls": 0, "input_tokens": 0, "output_tokens": 0},
        label=label or f"Comparing {before}/ -> {after}/",
    )
    if not modified:
        return result

    before_sources = get_function_sources(before)
    after_sources = get_function_sources(after)
    reverse_map = build_reverse_map(build_dependency_map(after))

    client = get_client()   # raises MissingAPIKey if no key
    gin = gout = gcalls = 0
    for changed in modified:
        changed_after = after_sources.get(changed, "")
        changed_before = before_sources.get(changed, "")

        # The changed function's before/after promises (skip contracts if this fails).
        before_promises = after_promises = None
        try:
            bp, u_bp = extract_promises(client, short_name(changed), changed_before)
            ap, u_ap = extract_promises(client, short_name(changed), changed_after)
            for u in (u_bp, u_ap):
                tin, tout = token_counts(u)
                gin += tin
                gout += tout
                gcalls += 1
            before_promises, after_promises = bp.promises, ap.promises
        except errors.APIError:
            before_promises = after_promises = None

        affected, tin, tout, calls = analyze_target(
            client, changed, diffs[changed], changed_after, reverse_map, after_sources,
            before_promises=before_promises, after_promises=after_promises,
        )
        gin += tin
        gout += tout
        gcalls += calls
        result.changed.append(ChangedFunction(changed, diffs[changed], affected))

    result.usage = {"calls": gcalls, "input_tokens": gin, "output_tokens": gout}
    return result


def analyze_repo(repo_url, base_branch, compare_branch):
    """GitHub mode as a library call: fetch two branches, analyze the diff, clean up.

    Returns a DiffAnalysisResult. Raises GitError (bad URL / missing repo or branch)
    or MissingAPIKey. The fetched code is removed before returning.
    """
    base_dir, compare_dir = fetch_branches(repo_url, base_branch, compare_branch)
    out_dir = os.path.dirname(base_dir)
    label = f"{repo_url}  ({base_branch} → {compare_branch})"
    try:
        return analyze_diff(base_dir, compare_dir, label=label)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def print_report(affected):
    if not affected:
        print("  (nothing depends on this function — no risk)")
        return
    for f in affected:
        if f.risk_level not in RANK:
            print(f"  [  ?   ] {short_name(f.name)} ({f.relation}) — {f.reason}")
            print()
            continue
        verdict = "WILL BREAK" if f.will_break else "may be affected"
        print(f"  [{f.risk_level.upper():6}] {short_name(f.name)} ({f.relation}) — {verdict}")
        print(f"           {f.reason}")
        for bc in f.broken_contracts:
            print(f"           ⚠ broken contract: {bc.expectation}")
            print(f"               why:  {bc.why}")
            if bc.line:
                print(f"               line: {bc.line}")
        print()


def print_usage(calls, total_input, total_output):
    print("--- Usage ---")
    print(f"API calls:     {calls}")
    print(f"Input tokens:  {total_input}")
    print(f"Output tokens: {total_output}")
    print(f"Est. cost:     $0.00  ({MODEL}, free tier)")


def run_typed_mode(folder, function, change):
    """Old CLI behavior: now just calls analyze_change() and prints the result."""
    try:
        result = analyze_change(folder, function, change)
    except ValueError as e:
        print(f"Error: {e}.")
        sys.exit(1)
    except SyntaxError as e:
        print(f"Error: could not parse a Python file in '{folder}' ({e}).")
        sys.exit(1)
    except OSError as e:
        print(f"Error reading '{folder}': {e}")
        sys.exit(1)
    except MissingAPIKey:
        print("Error: no API key found.")
        print("Set your key first, then re-run:  export GEMINI_API_KEY=...")
        sys.exit(1)

    if not result.affected:
        print(f"Changing {short_name(result.target)} affects nothing — no risk to assess.")
        return

    print(f'\nRisk report for changing {short_name(result.target)}: "{change}"\n')
    print_report(result.affected)
    u = result.usage
    print_usage(u["calls"], u["input_tokens"], u["output_tokens"])


def emit_diff_result(result, md_path=None):
    """Print a DiffAnalysisResult in the CLI format, and optionally write a Markdown report."""
    print(result.label)
    print(f"  added:    {', '.join(result.added) or 'none'}")
    print(f"  removed:  {', '.join(result.removed) or 'none'}")
    print(f"  modified: {', '.join(result.modified) or 'none'}")

    if not result.modified:
        print("\nNo modified functions — nothing to assess.")
    else:
        for cf in result.changed:
            print("\n" + "=" * 70)
            print(f"Changed function: {short_name(cf.name)}   ({cf.name})")
            print("-" * 70)
            print(cf.diff)
            print("-" * 70)
            print(f"\nRisk report (caused by changing {short_name(cf.name)}):\n")
            print_report(cf.affected)
        u = result.usage
        print_usage(u["calls"], u["input_tokens"], u["output_tokens"])

    if md_path:
        write_report(result, md_path)
        print(f"\nMarkdown report written to: {md_path}")


def run_diff_mode(before, after, label=None, md_path=None):
    """Compare two folders, print the report, and optionally write Markdown."""
    try:
        result = analyze_diff(before, after, label=label)
    except ValueError as e:
        print(f"Error: {e}.")
        sys.exit(1)
    except SyntaxError as e:
        print(f"Error: could not parse a Python file ({e}).")
        sys.exit(1)
    except OSError as e:
        print(f"Error reading a folder: {e}")
        sys.exit(1)
    except MissingAPIKey:
        print("Error: no API key found.")
        print("Set your key first, then re-run:  export GEMINI_API_KEY=...")
        sys.exit(1)
    emit_diff_result(result, md_path)


def run_repo_mode(repo_url, base_branch, compare_branch, md_path=None):
    """GitHub mode: fetch two branches, analyze the diff, print + optional Markdown."""
    try:
        result = analyze_repo(repo_url, base_branch, compare_branch)
    except GitError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except MissingAPIKey:
        print("Error: no API key found.")
        print("Set your key first, then re-run:  export GEMINI_API_KEY=...")
        sys.exit(1)
    emit_diff_result(result, md_path)


def run_pr_mode(repo_url, pr_number, md_path=None):
    """PR mode: read a PR's branches via the GitHub API, analyze, and post the report as a comment."""
    try:
        token = get_token()
    except GitHubError:
        print("Error: no GitHub token found.")
        print("Set it first, then re-run:  export GITHUB_TOKEN=...")
        sys.exit(1)

    try:
        pr = get_pull_request(repo_url, pr_number, token)
    except GitHubError as e:
        print(f"Error: {e}")
        sys.exit(1)

    base_ref = pr["base"]["ref"]
    head_ref = pr["head"]["ref"]
    base_clone = pr["base"]["repo"]["clone_url"]
    base_full = pr["base"]["repo"]["full_name"]
    head_repo = pr["head"]["repo"]
    head_full = head_repo["full_name"] if head_repo else None
    if head_full and head_full != base_full:
        print(f"Error: PR #{pr_number} is from a fork ({head_full}); "
              "only same-repo PRs are supported for now.")
        sys.exit(1)

    print(f"PR #{pr_number}: {pr.get('title', '')}")
    print(f"  base = {base_ref}   head = {head_ref}\n")

    try:
        result = analyze_repo(base_clone, base_ref, head_ref)
    except GitError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except MissingAPIKey:
        print("Error: no API key found.")
        print("Set your key first, then re-run:  export GEMINI_API_KEY=...")
        sys.exit(1)

    emit_diff_result(result, md_path)

    try:
        comment = post_pr_comment(repo_url, pr_number, diff_result_to_markdown(result), token)
    except GitHubError as e:
        print(f"\nError posting comment: {e}")
        sys.exit(1)
    print(f"\n✓ Posted analysis to PR #{pr_number}: {comment.get('html_url', '')}")


def main():
    parser = argparse.ArgumentParser(
        description="Trace the blast radius of a function change and assess its risk with Gemini."
    )
    parser.add_argument("codebase", nargs="?", help="(typed mode) folder of Python files to analyze")
    parser.add_argument("function", nargs="?", help="(typed mode) name of the function being changed")
    parser.add_argument("change", nargs="?", help="(typed mode) short description of the change")
    parser.add_argument("--before", help="(diff mode) path to the OLD version of the codebase")
    parser.add_argument("--after", help="(diff mode) path to the NEW version of the codebase")
    parser.add_argument("--repo", help="(GitHub/PR mode) public repo URL")
    parser.add_argument("--base", help="(GitHub mode) base branch")
    parser.add_argument("--compare", help="(GitHub mode) compare branch")
    parser.add_argument("--pr", type=int, help="(PR mode) pull request number to analyze and comment on")
    parser.add_argument("--md", help="also write the Markdown report to this file (diff/GitHub/PR modes)")
    args = parser.parse_args()

    if args.pr is not None:
        if not args.repo:
            parser.error("PR mode needs --repo and --pr")
        run_pr_mode(args.repo, args.pr, md_path=args.md)
    elif args.repo or args.base or args.compare:
        if not (args.repo and args.base and args.compare):
            parser.error("GitHub mode needs --repo, --base, and --compare")
        run_repo_mode(args.repo, args.base, args.compare, md_path=args.md)
    elif args.before or args.after:
        if not (args.before and args.after):
            parser.error("diff mode needs both --before and --after")
        run_diff_mode(args.before, args.after, md_path=args.md)
    elif args.codebase and args.function and args.change:
        run_typed_mode(args.codebase, args.function, args.change)
    else:
        parser.error(
            "provide  <codebase> <function> <change>,  "
            "or  --before <old> --after <new>,  "
            "or  --repo <url> --base <branch> --compare <branch>,  "
            "or  --repo <url> --pr <number>"
        )


if __name__ == "__main__":
    main()
