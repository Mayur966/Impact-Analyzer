import sys

from pydantic import BaseModel
from google.genai import types

from blast_radius import short_name
from diff_functions import get_function_sources
from gemini_common import make_client, generate_json


EXPECTATIONS_SYSTEM_PROMPT = (
    "You analyze code dependencies. Given a function (the 'dependent') and the name "
    "of a function it calls (the 'dependency'), list the dependent's implicit "
    "expectations about the dependency — the properties of its return value or "
    "behavior that the dependent silently relies on. Be specific and concrete."
)


class Expectations(BaseModel):
    expectations: list[str]


def extract_expectations(client, dependency_name, dependent_name, dependent_code):
    """Ask the LLM what `dependent_name` silently assumes about `dependency_name`.

    Looks only at the dependent's code (how it *uses* the dependency), not the
    dependency's implementation — so these stay comparable against the changed
    function's promises. Returns (Expectations, usage_metadata).
    """
    prompt = f"""`{dependent_name}` calls `{dependency_name}`. List the implicit expectations
that `{dependent_name}` has about `{dependency_name}` — the properties of `{dependency_name}`'s
return value or behavior that `{dependent_name}` silently relies on for its own correctness.

Base your answer only on how `{dependent_name}` uses `{dependency_name}` in the code below.
Do not assume anything about `{dependency_name}`'s current implementation.

Code of `{dependent_name}`:
```python
{dependent_code}
```

Return short, specific expectation strings, e.g.
"expects {dependency_name}() to return a number" or
"assumes the return value can be used in arithmetic"."""

    config = types.GenerateContentConfig(
        system_instruction=EXPECTATIONS_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=Expectations,
    )
    response = generate_json(client, prompt, config)

    result = response.parsed
    if not isinstance(result, Expectations):
        try:
            result = Expectations.model_validate_json(response.text or "")
        except Exception:
            result = Expectations(expectations=[])
    return result, response.usage_metadata


PROMISES_SYSTEM_PROMPT = (
    "You analyze functions. Given a function's source, list the promises it makes to "
    "its callers — the guarantees about its return value or observable behavior (return "
    "type, shape, properties such as non-None, sign, range). Base this only on the code shown."
)


class Promises(BaseModel):
    promises: list[str]


def extract_promises(client, function_name, function_code):
    """Ask the LLM what `function_name` guarantees to callers. Returns (Promises, usage)."""
    prompt = f"""What does `{function_name}` promise to its callers? List the guarantees about its
return value and observable behavior that a caller could rely on, based only on the code below.

Code of `{function_name}`:
```python
{function_code}
```

Return short, specific promise strings, e.g. "returns a number" or "never returns None"."""

    config = types.GenerateContentConfig(
        system_instruction=PROMISES_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=Promises,
    )
    response = generate_json(client, prompt, config)

    result = response.parsed
    if not isinstance(result, Promises):
        try:
            result = Promises.model_validate_json(response.text or "")
        except Exception:
            result = Promises(promises=[])
    return result, response.usage_metadata


COMPARE_SYSTEM_PROMPT = (
    "You verify semantic contracts between functions. You are given a dependent "
    "function's expectations about a dependency, and the dependency's promises before "
    "and after a change. Identify only the expectations the AFTER version no longer "
    "satisfies. Never flag an expectation that the AFTER promises still satisfy."
)


class BrokenContract(BaseModel):
    expectation: str   # the expectation that is now violated
    why: str           # how the AFTER behavior violates it
    line: str          # relevant line in the dependent's code, or "" if none


class ContractCheck(BaseModel):
    broken_contracts: list[BrokenContract]


def find_broken_contracts(client, dependent_name, dependent_code, dependency_name,
                          expectations, before_promises, after_promises):
    """Flag each expectation the AFTER promises no longer satisfy. Returns (ContractCheck, usage)."""
    exp = "\n".join(f"- {e}" for e in expectations)
    before = "\n".join(f"- {p}" for p in before_promises)
    after = "\n".join(f"- {p}" for p in after_promises)

    prompt = f"""`{dependent_name}` depends on `{dependency_name}`.

Code of `{dependent_name}`:
```python
{dependent_code}
```

`{dependent_name}`'s expectations about `{dependency_name}`:
{exp}

`{dependency_name}`'s promises BEFORE the change:
{before}

`{dependency_name}`'s promises AFTER the change:
{after}

Identify ONLY the expectations that the AFTER promises no longer satisfy — the broken contracts.
An expectation that the AFTER promises still satisfy (for example "never returns None") must NOT
be included. For each broken contract provide:
- expectation: the specific expectation that is now violated
- why: how the AFTER behavior violates it
- line: the exact line in `{dependent_name}`'s code that relies on it (or "" if none)
If nothing is broken, return an empty list."""

    config = types.GenerateContentConfig(
        system_instruction=COMPARE_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=ContractCheck,
    )
    response = generate_json(client, prompt, config)

    result = response.parsed
    if not isinstance(result, ContractCheck):
        try:
            result = ContractCheck.model_validate_json(response.text or "")
        except Exception:
            result = ContractCheck(broken_contracts=[])
    return result, response.usage_metadata


def _code_for(sources, name):
    for qname, code in sources.items():
        if short_name(qname) == name:
            return code
    print(f"Error: no function named '{name}' found.")
    sys.exit(1)


if __name__ == "__main__":
    before_sources = get_function_sources("sample_code")
    after_sources = get_function_sources("sample_code_v2")
    add_before = _code_for(before_sources, "add")
    add_after = _code_for(after_sources, "add")
    calc_code = _code_for(before_sources, "calculate_total")

    client = make_client()

    # Chain the three pieces: expectations + before/after promises -> comparison.
    expectations, _ = extract_expectations(client, "add", "calculate_total", calc_code)
    before, _ = extract_promises(client, "add", add_before)
    after, _ = extract_promises(client, "add", add_after)

    check, _ = find_broken_contracts(
        client, "calculate_total", calc_code, "add",
        expectations.expectations, before.promises, after.promises,
    )

    print("Broken contracts: calculate_total -> add  (return a + b  =>  return str(a + b))\n")
    if not check.broken_contracts:
        print("  (none — all expectations still satisfied)")
    for bc in check.broken_contracts:
        print(f"  ✗ {bc.expectation}")
        print(f"      why:  {bc.why}")
        if bc.line:
            print(f"      line: {bc.line}")
        print()
