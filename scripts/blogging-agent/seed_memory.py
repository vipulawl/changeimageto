#!/usr/bin/env python3
"""Seed post memory from existing frontend/blog/*.html files."""
import re
from pathlib import Path

from storage.db import get_all_post_memory, init_db, save_post_memory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BLOG_DIR = PROJECT_ROOT / "frontend" / "blog"


def _extract(html: str, pattern: str) -> str:
    m = re.search(pattern, html, re.I | re.S)
    return m.group(1).strip() if m else ""


def _plain_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    init_db()
    existing = {p["slug"] for p in get_all_post_memory()}
    added = 0

    for path in sorted(BLOG_DIR.glob("*.html")):
        if path.name == "index.html":
            continue
        slug = path.stem
        if slug in existing:
            continue

        html = path.read_text(encoding="utf-8", errors="ignore")
        title = _extract(html, r"<title>([^<]+)</title>") or slug.replace("-", " ").title()
        keyword = slug.replace("-", " ")
        body = _plain_text(html)
        summary = body[:500]
        date = _extract(html, r'<time datetime="([^"]+)"') or None

        save_post_memory(
            slug=slug,
            title=title,
            keyword=keyword,
            tags=[],
            summary=summary,
            semantic_fingerprint=" ".join(sorted(set(re.findall(r"[a-z]{4,}", body.lower())))[:50]),
            published_at=date[:10] if date else None,
            word_count=len(body.split()),
        )
        added += 1
        print(f"  + {slug}")

    print(f"Seeded {added} posts ({len(existing)} already indexed)")


if __name__ == "__main__":
    main()
