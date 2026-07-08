# ============================================================================
# PRESERVED: Anthropic (Claude) implementation of the AI risk layer.
#
# The ACTIVE tool is risk_analysis.py, which now uses the Google Gemini API.
# This file is kept unchanged so the tool can switch back to Anthropic:
#     venv/bin/pip install anthropic
#     export ANTHROPIC_API_KEY=sk-ant-...
#     venv/bin/python risk_analysis_anthropic.py sample_code add "add() will now return a string"
# ============================================================================

import argparse
import ast
import os
import sys
from typing import Literal

import anthropic
from pydantic import BaseModel

from detect_functions import build_dependency_map
from blast_radius import build_reverse_map, find_blast_radius, resolve_target, short_name


MODEL = "claude-opus-4-8"
INPUT_COST_PER_1M = 5.00    # claude-opus-4-8 pricing, USD per 1M tokens
OUTPUT_COST_PER_1M = 25.00

SYSTEM_PROMPT = (
    "You are a code-impact risk analyzer. Given a proposed change to one function "
    "and the source of a function that depends on it, judge whether the dependent "
    "function will break or misbehave. Be concrete and concise."
)


class RiskAssessment(BaseModel):
    will_break: bool
    risk_level: Literal["high", "medium", "low"]
    reason: str


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
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        output_format=RiskAssessment,
    )
    return response.parsed_output, response.usage


def main():
    parser = argparse.ArgumentParser(
        description="Trace the blast radius of a proposed function change and assess its risk with AI."
    )
    parser.add_argument("codebase", help="path to the folder of Python files to analyze")
    parser.add_argument("function", help="name of the function being changed")
    parser.add_argument("change", help="short description of the proposed change")
    args = parser.parse_args()

    folder = args.codebase
    if not os.path.isdir(folder):
        print(f"Error: '{folder}' is not a directory.")
        sys.exit(1)

    try:
        dependency_map = build_dependency_map(folder)
        sources = get_function_sources(folder)
    except SyntaxError as e:
        print(f"Error: could not parse a Python file in '{folder}' ({e}).")
        sys.exit(1)
    except OSError as e:
        print(f"Error reading '{folder}': {e}")
        sys.exit(1)

    reverse_map = build_reverse_map(dependency_map)

    targets = resolve_target(args.function, dependency_map)
    if not targets:
        print(f"Error: no function named '{args.function}' found in '{folder}'.")
        sys.exit(1)
    target = targets[0]

    affected = find_blast_radius(target, reverse_map)
    if not affected:
        print(f"Changing {short_name(target)} affects nothing — no risk to assess.")
        return

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("Error: no API key found.")
        print("Set your key first, then re-run:  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    client = anthropic.Anthropic()

    results = []
    total_input = total_output = calls = 0
    for affected_name, relation in affected:
        try:
            assessment, usage = assess_risk(
                client, args.change,
                short_name(target), sources[target],
                short_name(affected_name), sources[affected_name],
            )
        except anthropic.AuthenticationError:
            print("Error: authentication failed — check your ANTHROPIC_API_KEY.")
            sys.exit(1)
        except anthropic.APIError as e:
            print(f"  ! Could not assess {short_name(affected_name)}: {e}")
            results.append((affected_name, relation, None))
            continue
        total_input += usage.input_tokens
        total_output += usage.output_tokens
        calls += 1
        results.append((affected_name, relation, assessment))

    rank = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda r: rank.get(r[2].risk_level, 3) if r[2] else 4)

    print(f'\nRisk report for changing {short_name(target)}: "{args.change}"\n')
    for affected_name, relation, a in results:
        if a is None:
            print(f"  [  ?   ] {short_name(affected_name)} ({relation}) — assessment failed\n")
            continue
        verdict = "WILL BREAK" if a.will_break else "may be affected"
        print(f"  [{a.risk_level.upper():6}] {short_name(affected_name)} ({relation}) — {verdict}")
        print(f"           {a.reason}\n")

    cost = total_input / 1_000_000 * INPUT_COST_PER_1M + total_output / 1_000_000 * OUTPUT_COST_PER_1M
    print("--- Usage ---")
    print(f"API calls:     {calls}")
    print(f"Input tokens:  {total_input}")
    print(f"Output tokens: {total_output}")
    print(f"Est. cost:     ${cost:.4f}  ({MODEL} @ ${INPUT_COST_PER_1M:.0f}/${OUTPUT_COST_PER_1M:.0f} per 1M tokens)")


if __name__ == "__main__":
    main()
