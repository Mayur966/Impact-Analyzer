import os
import sys
import time

from google import genai
from google.genai import errors

MODEL = "gemini-2.5-flash"    # free-tier-eligible Gemini model
RETRY_WAIT_SECONDS = 20       # brief wait before one retry on a rate-limit (HTTP 429)


class MissingAPIKey(Exception):
    """Raised when no Gemini API key is available in the environment."""


def get_client():
    """Return a Gemini client, or raise MissingAPIKey if no key is set (library-friendly)."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise MissingAPIKey("no API key found (set GEMINI_API_KEY)")
    return genai.Client(api_key=api_key)


def make_client():
    """CLI helper: like get_client() but prints a friendly message and exits on no key."""
    try:
        return get_client()
    except MissingAPIKey:
        print("Error: no API key found.")
        print("Set your key first, then re-run:  export GEMINI_API_KEY=...")
        sys.exit(1)


def generate_json(client, prompt, config):
    """Call Gemini once, retrying a single time if the free tier rate-limits us (429)."""
    for attempt in range(2):
        try:
            return client.models.generate_content(model=MODEL, contents=prompt, config=config)
        except errors.APIError as e:
            if getattr(e, "code", None) == 429 and attempt == 0:
                print(f"  (rate limited — waiting {RETRY_WAIT_SECONDS}s and retrying once...)")
                time.sleep(RETRY_WAIT_SECONDS)
                continue
            raise


def token_counts(usage):
    """Return (input_tokens, output_tokens) from a Gemini usage_metadata object."""
    tin = getattr(usage, "prompt_token_count", 0) or 0
    tout = (getattr(usage, "candidates_token_count", 0) or 0) + (
        getattr(usage, "thoughts_token_count", 0) or 0
    )
    return tin, tout


def is_auth_error(e):
    """True if an APIError looks like a bad/missing key or permission problem."""
    msg = (getattr(e, "message", None) or str(e)).lower()
    return getattr(e, "code", None) in (400, 401, 403) and (
        "api key" in msg or "api_key" in msg or "permission" in msg or "credential" in msg
    )


def describe_api_error(e):
    """Turn a Gemini API exception into a short, human-readable reason.

    Used so a failed assessment shows *why* it failed (bad key, rate limit, wrong
    model, …) instead of a generic 'assessment failed' that hides the real cause.
    """
    code = getattr(e, "code", None)
    if is_auth_error(e):
        return "authentication failed — the GEMINI_API_KEY was rejected (check it's valid)"
    if code == 429:
        return "rate limited — the free-tier quota was exceeded, try again shortly"
    if code == 404:
        return f"model not available ({MODEL})"
    msg = (getattr(e, "message", None) or str(e)).strip()
    return f"API error {code}: {msg}".strip().rstrip(":") if (code or msg) else "unknown API error"
