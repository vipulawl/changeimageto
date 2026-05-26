#!/usr/bin/env python3
"""
Configure blogging-agent for GitHub Actions using remback secrets.
Uses separate GSC + GA4 service accounts (same as SEO agent).
"""
import json
import os
from pathlib import Path

AGENT_DIR = Path(__file__).parent
PROJECT_ROOT = AGENT_DIR.parent.parent


def main() -> None:
    ga4_json = (os.environ.get("GA4_CREDENTIALS_JSON") or "").strip()
    gsc_json = (os.environ.get("GSC_CREDENTIALS_JSON") or os.environ.get("GOOGLE_CREDENTIALS_JSON") or "").strip()

    if not gsc_json and not ga4_json:
        raise SystemExit("ERROR: Set GSC_CREDENTIALS_JSON and/or GA4_CREDENTIALS_JSON.")

    gsc_path = AGENT_DIR / "google-credentials-gsc.json"
    ga4_path = AGENT_DIR / "google-credentials-ga4.json"

    if gsc_json:
        json.loads(gsc_json)
        gsc_path.write_text(gsc_json)
    if ga4_json:
        json.loads(ga4_json)
        ga4_path.write_text(ga4_json)

    # Fallback: if only one secret provided, use it for both APIs
    if gsc_json and not ga4_json:
        ga4_path.write_text(gsc_json)
    elif ga4_json and not gsc_json:
        gsc_path.write_text(ga4_json)

    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not openai_key:
        raise SystemExit("ERROR: OPENAI_API_KEY secret is empty or not set.")

    gsc_site = (os.environ.get("GSC_SITE_URL") or "sc-domain:changeimageto.com").strip()
    ga4_property = (os.environ.get("GA4_PROPERTY_ID") or "505035310").strip()

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
        f"GSC_SITE_URL={gsc_site}",
        f"GA4_PROPERTY_ID={ga4_property}",
        f"GOOGLE_GSC_CREDENTIALS_FILE={gsc_path}",
        f"GOOGLE_GA4_CREDENTIALS_FILE={ga4_path}",
        f"CORRECTION_AUTO_MODE={os.environ.get('CORRECTION_AUTO_MODE', 'true')}",
        f"MAX_ARTICLES_PER_DAY={os.environ.get('MAX_ARTICLES_PER_DAY', '1')}",
        f"MIN_QUEUE_SIZE={os.environ.get('MIN_QUEUE_SIZE', '2')}",
    ]
    (AGENT_DIR / ".env").write_text("\n".join(env_lines) + "\n")
    print(f"Configured blogging-agent at {AGENT_DIR}")
    print(f"  GSC_SITE_URL={gsc_site}")
    print(f"  GA4_PROPERTY_ID={ga4_property}")
    print(f"  GSC creds: {'yes' if gsc_path.exists() else 'no'}")
    print(f"  GA4 creds: {'yes' if ga4_path.exists() else 'no'}")


if __name__ == "__main__":
    main()
