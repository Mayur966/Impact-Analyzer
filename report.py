"""Render a DiffAnalysisResult (from risk_analysis) as GitHub-flavored Markdown."""
import os

RISK_EMOJI = {"high": "🔴", "medium": "🟠", "low": "🟢"}


def _names(items):
    return ", ".join(f"`{x}`" for x in items) if items else "_none_"


def diff_result_to_markdown(result):
    """Return a Markdown string summarizing a DiffAnalysisResult."""
    out = []
    out.append("## 🧩 Code Impact Analysis")
    out.append("")
    out.append(
        "_This report traces a code change through the codebase: which functions depend "
        "on what changed, how risky each one is, and which expectations (\"contracts\") "
        "between functions the new version no longer satisfies._"
    )
    out.append("")
    if result.label:
        out.append(f"**Compared:** {result.label}")
        out.append("")
    out.append(
        f"**Modified:** {_names(result.modified)} · "
        f"**Added:** {_names(result.added)} · "
        f"**Removed:** {_names(result.removed)}"
    )
    out.append("")

    if not result.changed:
        out.append("No modified Python functions were detected — nothing to assess. ✅")
        return "\n".join(out) + "\n"

    for cf in result.changed:
        short = cf.name.split(".")[-1]
        out.append(f"### Changed function: `{short}`")
        out.append("")
        out.append("```diff")
        out.append(cf.diff)
        out.append("```")
        out.append("")
        if not cf.affected:
            out.append("_Nothing depends on this function._")
            out.append("")
            continue
        out.append(f"**{len(cf.affected)} function(s) in the blast radius:**")
        out.append("")
        for f in cf.affected:
            emoji = RISK_EMOJI.get(f.risk_level, "⚪")
            verdict = "**WILL BREAK**" if f.will_break else "may be affected"
            afshort = f.name.split(".")[-1]
            out.append(f"#### {emoji} `{afshort}` — {f.risk_level.upper()} — {verdict}")
            out.append(f"_{f.relation}_")
            out.append("")
            out.append(f.reason)
            out.append("")
            for bc in f.broken_contracts:
                loc = f" (line: `{bc.line}`)" if bc.line else ""
                out.append(f"> ⚠️ **Broken contract:** {bc.expectation}{loc}  ")
                out.append(f"> {bc.why}")
                out.append("")

    out.append("---")
    u = result.usage
    out.append(
        f"<sub>Analysis: {u.get('calls', 0)} API calls · "
        f"{u.get('input_tokens', 0)} in / {u.get('output_tokens', 0)} out tokens · "
        f"$0.00 (Gemini free tier)</sub>"
    )
    return "\n".join(out) + "\n"


def write_report(result, path):
    """Write the Markdown report to `path`. Returns the path."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(diff_result_to_markdown(result))
    return path
