import time
import requests
from xml.etree import ElementTree
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BlogResearchBot/1.0; +https://github.com)"}


def _fetch(url: str, timeout: int = 8) -> requests.Response | None:
    try:
        r = requests.get(url, timeout=timeout, headers=HEADERS)
        return r if r.status_code == 200 else None
    except Exception:
        return None


def get_sitemap_posts(site_url: str, days_recent: int = 45, limit: int = 20) -> list[dict]:
    """
    Fetch a site's sitemap and return recently added/modified post URLs.
    Tries common sitemap locations; handles sitemap indexes.
    """
    base = site_url.rstrip("/")
    candidates = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/post-sitemap.xml",
        f"{base}/blog-sitemap.xml",
        f"{base}/news-sitemap.xml",
    ]

    xml_text = None
    for url in candidates:
        r = _fetch(url)
        if r and ("xml" in r.headers.get("content-type", "") or r.text.strip().startswith("<")):
            xml_text = r.text
            break

    if not xml_text:
        return [{"error": f"No sitemap found at {site_url}"}]

    try:
        root = ElementTree.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        # Sitemap index — find a blog/post sub-sitemap
        sub_sitemaps = root.findall("sm:sitemap/sm:loc", ns)
        if sub_sitemaps:
            for loc_el in sub_sitemaps:
                loc = loc_el.text or ""
                if any(w in loc for w in ["post", "blog", "article"]):
                    r = _fetch(loc)
                    if r:
                        root = ElementTree.fromstring(r.text)
                    break

        cutoff = datetime.now() - timedelta(days=days_recent)
        posts = []

        for url_el in root.findall("sm:url", ns):
            loc = url_el.findtext("sm:loc", namespaces=ns) or ""
            lastmod = url_el.findtext("sm:lastmod", namespaces=ns) or ""

            if not loc:
                continue
            path = urlparse(loc).path
            if any(skip in path for skip in ["/tag/", "/category/", "/page/", "/author/", "/feed"]):
                continue
            if "?" in loc or loc.rstrip("/") == base:
                continue

            if lastmod:
                try:
                    if datetime.fromisoformat(lastmod[:10]) < cutoff:
                        continue
                except Exception:
                    pass

            posts.append({"url": loc, "lastmod": lastmod[:10] if lastmod else "unknown"})

        return sorted(posts, key=lambda x: x["lastmod"], reverse=True)[:limit]

    except Exception as e:
        return [{"error": str(e)}]


def fetch_post_summary(url: str) -> dict:
    """
    Fetch a blog post and extract: title, H2 sections, word count, meta description.
    Used to understand a competitor's content structure and depth.
    """
    try:
        from bs4 import BeautifulSoup
        r = _fetch(url, timeout=12)
        if not r:
            return {"url": url, "error": "Could not fetch page"}

        soup = BeautifulSoup(r.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        h1 = soup.find("h1")
        title_tag = soup.find("title")
        meta = soup.find("meta", {"name": "description"}) or soup.find("meta", {"property": "og:description"})
        h2s = [h.get_text(strip=True) for h in soup.find_all("h2")][:8]
        word_count = len(soup.get_text().split())

        return {
            "url": url,
            "title": (h1 or title_tag).get_text(strip=True) if (h1 or title_tag) else "Unknown",
            "meta_description": meta.get("content", "") if meta else "",
            "h2_sections": h2s,
            "word_count_approx": word_count,
        }
    except Exception as e:
        return {"url": url, "error": str(e)}
