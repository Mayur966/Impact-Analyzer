import io
import os
import tempfile
import zipfile

import streamlit as st

from risk_analysis import analyze_change, analyze_repo
from gemini_common import MissingAPIKey, MODEL
from github_source import GitError


RISK_COLOR = {"high": "red", "medium": "orange", "low": "green"}

MISSING_KEY_MSG = (
    "No Gemini API key found. On Streamlit Cloud, add it under "
    "**⚙️ Settings → Secrets** as `GEMINI_API_KEY = \"your-key\"`. "
    "Running locally? `export GEMINI_API_KEY=…` before launching."
)


def load_api_key():
    """Make the Gemini key available as an env var, wherever we're running.

    Locally you `export GEMINI_API_KEY=…` and that wins. On Streamlit Cloud there's
    no shell to export in, so we read the key from the app's Secrets and copy it into
    os.environ — exactly where get_client() already looks. If neither is set we do
    nothing, and get_client() raises MissingAPIKey with a clear message.
    """
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return  # a local export already set it — leave it alone
    try:
        key = st.secrets["GEMINI_API_KEY"]  # raises if no secrets file / key on cloud
    except Exception:
        return  # no secret configured — let get_client() report the missing key
    if key:
        os.environ["GEMINI_API_KEY"] = key


def find_code_dir(root):
    """Return the directory under `root` holding the most .py files (or None if none)."""
    best_dir, best_count = None, 0
    for dirpath, _dirnames, filenames in os.walk(root):
        if "__MACOSX" in dirpath.split(os.sep):
            continue
        count = sum(1 for f in filenames if f.endswith(".py") and not f.startswith("."))
        if count > best_count:
            best_dir, best_count = dirpath, count
    return best_dir


def render_affected(f):
    """Render one affected function (risk badge, verdict, reason, broken contracts)."""
    color = RISK_COLOR.get(f.risk_level, "gray")
    verdict = "WILL BREAK" if f.will_break else "may be affected"
    short = f.name.split(".")[-1]
    with st.container(border=True):
        st.markdown(f"**{short}**  ·  :{color}[{f.risk_level.upper()}]  ·  {verdict}")
        st.caption(f"{f.name}  ({f.relation})")
        st.write(f.reason)
        for bc in f.broken_contracts:
            st.markdown(f"⚠️ **Broken contract:** {bc.expectation}")
            line = f"  ·  line: {bc.line}" if bc.line else ""
            st.caption(f"why: {bc.why}{line}")


def render_usage(u):
    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("API calls", u.get("calls", 0))
    c2.metric("Input tokens", u.get("input_tokens", 0))
    c3.metric("Output tokens", u.get("output_tokens", 0))
    c4.metric("Est. cost", "$0.00")
    st.caption(f"Model: {MODEL} (free tier)")


st.set_page_config(page_title="Code Impact Analyzer", page_icon="🧩")
load_api_key()   # pull the key from Secrets (cloud) or the environment (local)
st.title("🧩 Code Impact Analyzer")
st.write("See which functions are at risk from a change — from a zip you upload or two branches of a GitHub repo.")

mode = st.radio("Input source", ["Upload a .zip", "Analyze a GitHub repo"], horizontal=True)


# ---------------- Zip upload mode (unchanged behavior) ----------------
if mode == "Upload a .zip":
    uploaded = st.file_uploader("Upload your codebase (.zip)", type=["zip"])
    function_name = st.text_input("Function being changed", placeholder="add")
    change = st.text_input("Change description", placeholder="add() will now return a string")

    if st.button("Analyze", type="primary", key="zip_btn"):
        if uploaded is None:
            st.warning("Please upload a .zip of your codebase.")
            st.stop()
        if not function_name.strip():
            st.warning("Please enter the function name being changed.")
            st.stop()
        if not change.strip():
            st.warning("Please describe the change.")
            st.stop()

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                with zipfile.ZipFile(io.BytesIO(uploaded.getvalue())) as zf:
                    zf.extractall(tmpdir)
            except zipfile.BadZipFile:
                st.error("That doesn't look like a valid .zip file.")
                st.stop()

            for dirpath, _dirnames, filenames in os.walk(tmpdir):
                for name in filenames:
                    if name.startswith("._"):
                        os.remove(os.path.join(dirpath, name))

            code_dir = find_code_dir(tmpdir)
            if code_dir is None:
                st.error("No .py files found in the zip.")
                st.stop()

            try:
                with st.spinner("Analyzing…"):
                    result = analyze_change(code_dir, function_name.strip(), change.strip())
            except MissingAPIKey:
                st.error(MISSING_KEY_MSG)
                st.stop()
            except ValueError as e:
                st.error(str(e))
                st.stop()
            except SystemExit:
                st.error("Analysis stopped — check that your GEMINI_API_KEY is valid.")
                st.stop()
            except Exception as e:
                st.error(f"Unexpected error: {e}")
                st.stop()

        st.divider()
        st.subheader(f"Changing `{result.target}`")
        if not result.affected:
            st.success("Nothing depends on this function — no blast radius.")
        else:
            st.write(f"**{len(result.affected)}** function(s) in the blast radius:")
            for f in result.affected:
                render_affected(f)
        render_usage(result.usage)


# ---------------- GitHub repo mode (new) ----------------
else:
    repo_url = st.text_input("Repo URL", placeholder="https://github.com/owner/repo")
    base = st.text_input("Base branch", placeholder="main")
    compare = st.text_input("Compare branch", placeholder="feature-branch")

    if st.button("Analyze", type="primary", key="gh_btn"):
        if not (repo_url.strip() and base.strip() and compare.strip()):
            st.warning("Please fill in the repo URL, base branch, and compare branch.")
            st.stop()

        try:
            with st.spinner("Cloning repo and analyzing the branch diff…"):
                result = analyze_repo(repo_url.strip(), base.strip(), compare.strip())
        except GitError as e:
            st.error(f"GitHub error: {e}")
            st.stop()
        except MissingAPIKey:
            st.error(MISSING_KEY_MSG)
            st.stop()
        except ValueError as e:
            st.error(str(e))
            st.stop()
        except SystemExit:
            st.error("Analysis stopped — check that your GEMINI_API_KEY is valid.")
            st.stop()
        except Exception as e:
            st.error(f"Unexpected error: {e}")
            st.stop()

        st.divider()
        st.caption(result.label)
        st.write(
            f"**Modified:** {', '.join(result.modified) or 'none'}  ·  "
            f"**Added:** {', '.join(result.added) or 'none'}  ·  "
            f"**Removed:** {', '.join(result.removed) or 'none'}"
        )

        if not result.changed:
            st.info("No modified Python functions between these branches — nothing to assess.")
        for cf in result.changed:
            st.subheader(f"Changed: `{cf.name.split('.')[-1]}`")
            st.code(cf.diff, language="diff")
            if not cf.affected:
                st.write("_(nothing depends on this function)_")
            for f in cf.affected:
                render_affected(f)

        render_usage(result.usage)
