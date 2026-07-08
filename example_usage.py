"""Example: call the analyzer as a library (no CLI) and consume the structured result.

Run it (with GEMINI_API_KEY set):
    venv/bin/python example_usage.py
"""
import json
from dataclasses import asdict

from risk_analysis import analyze_change

# One call does the whole typed-description analysis and returns structured data.
result = analyze_change("sample_code", "add", "add() will now return a string")

# --- Attribute access (what a Streamlit page would loop over) ---
print(f"Changed function : {result.target}")
print(f"Affected functions: {len(result.affected)}")
print(f"Usage            : {result.usage}\n")

for f in result.affected:
    print(f"- {f.name}  [{f.risk_level}]  will_break={f.will_break}")
    print(f"    reason: {f.reason}")
    for bc in f.broken_contracts:
        print(f"    broken contract: {bc.expectation} — {bc.why}")

# --- Same data as a plain dict / JSON (e.g. an API response) ---
print("\nAs JSON:")
print(json.dumps(asdict(result), indent=2))
