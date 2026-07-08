# Code Impact Analyzer

Given a proposed change to a function, this tool traces every other function that
depends on it — directly or indirectly — and uses an LLM to assess the risk that
each dependent will break. It prints a ranked "blast radius" report.

The AI layer uses the **Google Gemini API (free tier)**. A Claude/Anthropic
implementation is preserved in `risk_analysis_anthropic.py` — see
[Switching providers](#switching-providers).

## How it works

1. **Static analysis** (`detect_functions.py`) — parses each `.py` file with Python's
   `ast` module (no code is executed), finds every function, the calls it makes, and
   resolves imports to build a cross-file dependency map: *who calls whom*.
2. **Blast radius** (`blast_radius.py`) — reverses that map and walks outward from the
   changed function to find everything affected (direct and indirect).
3. **AI risk analysis** (`risk_analysis.py`) — sends the changed function's code, the
   change description, and each affected function's code to Gemini, which returns a
   structured verdict (will it break? / risk level / one-line reason).

## Setup

```bash
python3 -m venv venv
venv/bin/pip install google-genai
export GEMINI_API_KEY=...        # get one free at https://aistudio.google.com/apikey
```

The key is read from the `GEMINI_API_KEY` environment variable (`GOOGLE_API_KEY` also
works) — it is never hardcoded.

## Usage

```bash
venv/bin/python risk_analysis.py <codebase_folder> <function> "<change description>"
```

Example:

```bash
venv/bin/python risk_analysis.py sample_code add "add() will now return a string"
```

Sample output:

```
Risk report for changing add: "add() will now return a string"

  [HIGH  ] calculate_total (direct) — WILL BREAK
           Summing a string with integers raises a TypeError.

  [HIGH  ] process_order (indirect via calculate_total) — WILL BREAK
           It relies on calculate_total, which now fails.

--- Usage ---
API calls:     2
Input tokens:  412
Output tokens: 138
Est. cost:     $0.00  (gemini-2.5-flash, free tier)
```

## Arguments

| Argument            | Meaning                                             |
| ------------------- | --------------------------------------------------- |
| `codebase_folder`   | Folder of `.py` files to analyze (e.g. `sample_code`) |
| `function`          | Name of the function being changed (e.g. `add`)     |
| `change description`| Plain-English description of the proposed change    |

## Switching providers

The tool ships with two interchangeable AI backends. Both share the same static
analysis (`detect_functions.py`, `blast_radius.py`) and the same `RiskAssessment`
output shape.

| Provider            | File                        | Env var             | Model             |
| ------------------- | --------------------------- | ------------------- | ----------------- |
| Google Gemini (active) | `risk_analysis.py`       | `GEMINI_API_KEY`    | `gemini-2.5-flash`|
| Anthropic Claude    | `risk_analysis_anthropic.py`| `ANTHROPIC_API_KEY` | `claude-opus-4-8` |

To use the Claude backend instead:

```bash
venv/bin/pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
venv/bin/python risk_analysis_anthropic.py sample_code add "add() will now return a string"
```

## Notes & current limitations

- Handles `from module import name` imports; not `import module` or relative imports.
- Methods inside classes are treated as plain functions.
- Built-in / unresolved calls (e.g. `print`) are ignored — only project functions count.
- One Gemini call is made per affected function. On a free-tier rate-limit (HTTP 429)
  the tool waits briefly and retries once, then fails gracefully for that function.
- Gemini free tier means the cost line always reads `$0.00`; token usage is still reported.
