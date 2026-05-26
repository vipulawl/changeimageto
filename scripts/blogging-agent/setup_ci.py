#!/usr/bin/env python3
"""
Configure blogging-agent for GitHub Actions using remback secrets.
Reuses GA4/GSC credentials from the SEO agent setup.
"""
import json
import os
from pathlib import Path

AGENT_DIR = Path(__file__).parent
PROJECT_ROOT = AGENT_DIR.parent.parent


def main() -> None:
    ga4_json = (os.environ.get("GA4_CREDENTIALS_JSON") or os.environ.get("GOOGLE_CREDENTIALS_JSON") or "").strip()
    gsc_json = (os.environ.get("GSC_CREDENTIALS_JSON") or os.environ.get("GOOGLE_CREDENTIALS_JSON") or "").strip()

    if not ga4_json and not gsc_json:
        raise SystemExit("ERROR: Set GA4_CREDENTIALS_JSON and/or GSC_CREDENTIALS_JSON (or GOOGLE_CREDENTIALS_JSON).")

    cred_path = AGENT_DIR / "google-credentials.json"
    cred_json = ga4_json or gsc_json
    if ga4_json and gsc_json and ga4_json != gsc_json:
        # Prefer GA4 creds; both service accounts should have GSC+GA4 access
        cred_json = ga4_json

    json.loads(cred_json)
    cred_path.write_text(cred_json)

    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not openai_key:
        raise SystemExit("ERROR: OPENAI_API_KEY secret is empty or not set. Add it in Settings → Secrets.")

    env_lines = [
        f"PROVIDER={os.environ.get('BLOG_PROVIDER', 'openai')}",
        f"OPENAI_API_KEY={openai_key}",
        f"OPENAI_MODEL={os.environ.get('OPENAI_MODEL', 'gpt-4o')}",
        f"ANTHROPIC_API_KEY={os.environ.get('ANTHROPIC_API_KEY', '')}",
        f"BLOG_NICHE={os.environ.get('BLOG_NICHE', 'free online image editing tools')}",
        f"TARGET_AUDIENCE={os.environ.get('TARGET_AUDIENCE', 'ecommerce sellers, designers, and content creators')}",
        f"BLOG_TONE={os.environ.get('BLOG_TONE', 'practical, helpful, and SEO-focused')}",
        f"BLOG_LANGUAGE={os.environ.get('BLOG_LANGUAGE', 'English')}",
        f"APPROVAL_MODE={os.environ.get('BLOG_APPROVAL_MODE', 'auto')}",
        f"PUBLISHER=changeimageto",
        f"REPO_DIR={PROJECT_ROOT}",
        f"CONTENT_DIR=frontend/blog",
        f"GSC_SITE_URL={os.environ.get('GSC_SITE_URL', 'sc-domain:changeimageto.com')}",
        f"GA4_PROPERTY_ID={os.environ.get('GA4_PROPERTY_ID', '505035310')}",
        f"GOOGLE_CREDENTIALS_FILE={cred_path}",
        f"CORRECTION_AUTO_MODE={os.environ.get('CORRECTION_AUTO_MODE', 'true')}",
        f"MAX_ARTICLES_PER_DAY={os.environ.get('MAX_ARTICLES_PER_DAY', '1')}",
        f"MIN_QUEUE_SIZE={os.environ.get('MIN_QUEUE_SIZE', '2')}",
    ]
    (AGENT_DIR / ".env").write_text("\n".join(env_lines) + "\n")
    print(f"Configured blogging-agent at {AGENT_DIR}")


if __name__ == "__main__":
    main()
