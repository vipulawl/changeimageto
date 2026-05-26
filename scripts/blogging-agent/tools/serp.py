import time
from urllib.parse import urlparse
from tools.search import web_search

GENERIC_DOMAINS = {
    "en.wikipedia.org", "www.reddit.com", "www.youtube.com", "www.quora.com",
    "medium.com", "www.forbes.com", "www.inc.com", "www.entrepreneur.com",
    "www.hubspot.com", "www.wordstream.com", "www.searchenginejournal.com",
}


def analyze_serp(keyword: str, max_results: int = 10) -> list[dict]:
    """Return structured top-N results for a keyword from DuckDuckGo."""
    results = web_search(keyword, max_results=max_results)
    return [
        {
            "position": i + 1,
            "domain": urlparse(r.get("url", "")).netloc,
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("snippet", ""),
        }
        for i, r in enumerate(results)
        if "error" not in r and r.get("url")
    ]


def find_competitors_for_niche(niche: str, num_seed_queries: int = 5) -> list[dict]:
    """
    Search niche-related queries and score domains by how often they appear.
    Returns potential SEO competitors sorted by frequency.
    """
    seeds = [
        f"{niche} blog",
        f"best {niche} guide",
        f"{niche} tips",
        f"how to {niche}",
        f"{niche} strategy",
    ][:num_seed_queries]

    domain_data: dict[str, dict] = {}

    for query in seeds:
        results = analyze_serp(query, max_results=8)
        for r in results:
            domain = r["domain"]
            if not domain or domain in GENERIC_DOMAINS:
                continue
            if domain not in domain_data:
                domain_data[domain] = {"domain": domain, "appearances": 0, "sample_titles": [], "sample_url": r["url"]}
            domain_data[domain]["appearances"] += 1
            if len(domain_data[domain]["sample_titles"]) < 3:
                domain_data[domain]["sample_titles"].append(r["title"])
        time.sleep(0.4)

    ranked = sorted(domain_data.values(), key=lambda x: x["appearances"], reverse=True)
    return ranked[:12]
