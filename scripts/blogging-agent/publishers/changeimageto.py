"""
Publish blogging-agent drafts as ChangeImageTo.com static HTML blog posts.
"""
import re
import subprocess
import sys
from datetime import datetime
from html import escape
from pathlib import Path

import markdown

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BLOG_DIR = PROJECT_ROOT / "frontend" / "blog"
INDEX_PATH = BLOG_DIR / "index.html"

BLOG_TEMPLATE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<meta name="description" content="{description}"/>
<link rel="canonical" href="https://www.changeimageto.com/blog/{slug}.html"/>
<script type="application/ld+json">{{"@context": "https://schema.org", "@type": "Article", "headline": "{headline}", "datePublished": "{date_iso}", "author": {{"@type": "Organization", "name": "ChangeImageTo.com Team"}}}}</script>
<link rel="preload" as="style" href="/styles.css?v=20250916-3"/><link rel="stylesheet" href="/styles.css?v=20250916-3"/>
<link rel="stylesheet" href="https://www.changeimageto.com/styles.css?v=20250916-3"/>
<style>
  body, .main, main.container.main, .seo, .seo p, .seo li, .seo h2, .seo h3, .seo details, .seo summary {{ color: #ffffff; }}
  .seo a {{ color: #9ccfff; }}
  .seo a:hover {{ text-decoration: underline; }}
  .seo-links a {{ color: #ffffff; }}
  .header h1 {{ color: #ffffff; }}
</style>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-D3Q582VFCG"></script>
<script>window.dataLayer = window.dataLayer || []; function gtag(){{dataLayer.push(arguments);}} gtag('js', new Date()); gtag('config', 'G-D3Q582VFCG');</script>
</head><body>
<header class="container header"><a href="https://www.changeimageto.com/" class="logo-link"><img src="https://www.changeimageto.com/logo.png?v=20250916-2" alt="ChangeImageTo" class="logo-img"/></a><div style="display:flex;align-items:center;gap:16px;justify-content:space-between;width:100%"><h1 style="margin:0">{h1}</h1><nav class="top-nav"><a href="https://www.changeimageto.com/blog" aria-label="Read our blog">Blog</a></nav></div></header>
<main class="container main">
  <p class="seo" style="margin:0 0 16px"><strong>By:</strong> ChangeImageTo.com Team · <time datetime="{date_iso}">{date_str}</time></p>
  {body}
  <p class="seo" style="margin-top:24px"><a href="https://www.changeimageto.com/blog" style="color:#fff">← Back to blog</a></p>
</main>
<nav class="seo-links"><a href="https://www.changeimageto.com/remove-background-from-image.html">Remove Background from Image</a><a href="https://www.changeimageto.com/change-color-of-image.html">Change color of image online</a><a href="https://www.changeimageto.com/change-image-background.html">Change image background</a><a href="https://www.changeimageto.com/convert-image-format.html">Convert image format</a><a href="https://www.changeimageto.com/upscale-image.html">AI Image Upscaler</a><a href="https://www.changeimageto.com/blur-background.html">Blur Background</a><a href="https://www.changeimageto.com/enhance-image.html">Enhance Image</a></nav>
<footer class="comprehensive-footer"><div class="container"><p style="color:var(--muted);font-size:14px">© ChangeImageTo.com · Free online image tools</p></div></footer>
</body></html>"""

TOOL_LINKS = {
    "remove-background": "/remove-background-from-image.html",
    "change-background": "/change-image-background.html",
    "blur-background": "/blur-background.html",
    "upscale": "/upscale-image.html",
    "enhance": "/enhance-image.html",
    "resize": "/bulk-image-resizer.html",
    "convert": "/convert-image-format.html",
    "change-color": "/change-color-of-image.html",
}


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:80]


def _fix_internal_links(html: str) -> str:
    """Normalize writer links to ChangeImageTo paths."""
    html = re.sub(
        r'href="https?://(?:www\.)?changeimageto\.com(/[^"]*)"',
        r'href="\1"',
        html,
    )
    html = re.sub(
        r'href="/blog/([^"]+?)(?:\.html)?"',
        r'href="/blog/\1.html"',
        html,
    )
    return html


def markdown_to_body_html(content: str) -> str:
    html = markdown.markdown(content or "", extensions=["extra", "sane_lists"])
    html = _fix_internal_links(html)
    parts = re.split(r"(?=<h2\b)", html)
    sections = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        sections.append(f'<section class="seo">{part}</section>')
    return "\n".join(sections) if sections else f'<section class="seo">{html}</section>'


def _extract_h1(title: str) -> str:
    return title.strip()


def render_post_html(draft: dict, published_at: datetime | None = None) -> tuple[str, str, Path]:
    now = published_at or datetime.utcnow()
    slug = draft.get("slug") or _slugify(draft.get("title", "post"))
    title = draft.get("title", slug)
    description = (draft.get("meta_description") or "").replace('"', "&quot;")
    h1 = _extract_h1(title)
    body = markdown_to_body_html(draft.get("content") or "")

    html = BLOG_TEMPLATE.format(
        title=escape(title),
        description=description,
        slug=slug,
        headline=escape(h1).replace('"', "&quot;"),
        date_iso=now.strftime("%Y-%m-%dT%H:%M:%S"),
        date_str=now.strftime("%Y-%m-%d %H:%M UTC"),
        h1=escape(h1),
        body=body,
    )

    filepath = BLOG_DIR / f"{slug}.html"
    return html, slug, filepath


def add_to_blog_index(slug: str, title: str, description: str, published_at: datetime | None = None) -> bool:
    if not INDEX_PATH.exists():
        return False
    content = INDEX_PATH.read_text(encoding="utf-8")
    if f'/blog/{slug}.html' in content:
        return True

    now = published_at or datetime.utcnow()
    rel_url = f"/blog/{slug}.html"
    title_safe = escape(title)
    desc_safe = escape((description[:120] + ("..." if len(description) > 120 else "")))
    card = f'''
      <article class="blog-card">
        <div class="blog-card-content">
          <h2 class="blog-card-title"><a href="{rel_url}">{title_safe}</a></h2>
          <p class="blog-card-snippet">{desc_safe}</p>
          <div class="blog-card-meta">
            <time datetime="{now.strftime('%Y-%m-%d')}">{now.strftime('%Y-%m-%d')}</time>
          </div>
        </div>
      </article>
'''
    marker = re.search(r'<div\s+class=["\']blog-grid["\']\s*>', content)
    if not marker:
        return False
    INDEX_PATH.write_text(content[: marker.end()] + card + content[marker.end() :], encoding="utf-8")
    return True


def _run_post_publish_tasks(slug: str) -> None:
    sitemap_script = PROJECT_ROOT / "scripts" / "generate_sitemap.py"
    indexnow_script = PROJECT_ROOT / "scripts" / "submit_to_indexnow.py"
    if sitemap_script.exists():
        subprocess.run([sys.executable, str(sitemap_script)], cwd=PROJECT_ROOT, check=False)
    if indexnow_script.exists():
        url = f"https://www.changeimageto.com/blog/{slug}.html"
        subprocess.run(
            [sys.executable, str(indexnow_script), url],
            cwd=PROJECT_ROOT,
            check=False,
        )


def publish_post(draft: dict) -> Path:
    """Write HTML post, update blog index, regenerate sitemap, notify IndexNow."""
    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    html, slug, filepath = render_post_html(draft)
    if filepath.exists():
        slug = f"{slug}-{datetime.utcnow().strftime('%Y%m%d')}"
        filepath = BLOG_DIR / f"{slug}.html"
        html, _, filepath = render_post_html({**draft, "slug": slug})

    filepath.write_text(html, encoding="utf-8")
    add_to_blog_index(slug, draft.get("title", slug), draft.get("meta_description", ""))
    _run_post_publish_tasks(slug)
    return filepath


def refresh_post(slug: str, draft: dict, existing_path: Path | None = None) -> Path:
    """Update an existing blog HTML file with refreshed content."""
    path = existing_path or (BLOG_DIR / f"{slug}.html")
    if not path.exists():
        return publish_post(draft)

    published_at = None
    m = re.search(r'datetime="([^"]+)"', path.read_text(encoding="utf-8"))
    if m:
        try:
            published_at = datetime.fromisoformat(m.group(1))
        except ValueError:
            pass

    html, _, _ = render_post_html({**draft, "slug": slug}, published_at=published_at)
    path.write_text(html, encoding="utf-8")
    _run_post_publish_tasks(slug)
    return path


def find_blog_path_for_slug(slug: str) -> Path | None:
    direct = BLOG_DIR / f"{slug}.html"
    if direct.exists():
        return direct
    matches = list(BLOG_DIR.glob(f"{slug}*.html"))
    return matches[0] if matches else None


def load_article_from_html(slug: str) -> dict | None:
    path = find_blog_path_for_slug(slug)
    if not path:
        return None
    html = path.read_text(encoding="utf-8", errors="ignore")
    title = re.search(r"<title>([^<]+)</title>", html, re.I)
    keyword = slug.replace("-", " ")
    date_m = re.search(r'<time datetime="([^"]+)"', html)
    body_m = re.search(r"</header>\s*<main[^>]*>([\s\S]*?)</main>", html, re.I)
    if not body_m:
        body_m = re.search(r'<main[^>]*>([\s\S]*?)</main>', html, re.I)
    content = _plain_text(body_m.group(1)) if body_m else _plain_text(html)
    age_days = 0
    if date_m:
        try:
            age_days = (datetime.utcnow() - datetime.fromisoformat(date_m.group(1)[:19])).days
        except ValueError:
            pass
    return {
        "file_path": str(path),
        "title": title.group(1) if title else slug.replace("-", " ").title(),
        "keyword": keyword,
        "slug": path.stem,
        "meta_description": (re.search(r'<meta name="description" content="([^"]*)"', html, re.I) or [None, ""])[1],
        "date_published": date_m.group(1)[:10] if date_m else "",
        "age_days": age_days,
        "content": content,
    }


def _plain_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def retitle_post(slug: str, new_title: str, new_meta: str) -> Path | None:
    path = find_blog_path_for_slug(slug)
    if not path:
        return None
    html = path.read_text(encoding="utf-8")
    html = re.sub(r"<title>[^<]*</title>", f"<title>{escape(new_title)}</title>", html, count=1)
    if new_meta:
        html = re.sub(
            r'<meta name="description" content="[^"]*"',
            f'<meta name="description" content="{new_meta.replace(chr(34), "&quot;")}"',
            html,
            count=1,
        )
    html = re.sub(
        r'(<h1 style="margin:0">)[^<]*(</h1>)',
        rf"\1{escape(new_title)}\2",
        html,
        count=1,
    )
    html = re.sub(
        r'"headline": "[^"]*"',
        f'"headline": "{new_title.replace(chr(34), "&quot;")}"',
        html,
        count=1,
    )
    path.write_text(html, encoding="utf-8")
    _run_post_publish_tasks(path.stem)
    return path
