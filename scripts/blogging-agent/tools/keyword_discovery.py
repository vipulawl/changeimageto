import time
from urllib.parse import urlparse
from tools.search import web_search

INTENT_PREFIXES = [
    "how to {seed}",
    "best {seed}",
    "what is {seed}",
    "{seed} guide",
    "{seed} strategy",
    "{seed} tips",
    "{seed} for beginners",
    "{seed} examples",
    "{seed} mistakes",
    "{seed} checklist",
    "{seed} vs",
    "{seed} tools",
]


def discover_keywords(seed_keyword: str, max_patterns: int = 6) -> dict:
    """
    Expand a seed keyword using intent-based search patterns via DuckDuckGo.
    Returns the queries used and which domains dominate the SERPs.
    """
    patterns = INTENT_PREFIXES[:max_patterns]
    queries_run = []
    domain_freq: dict[str, dict] = {}

    for pattern in patterns:
        query = pattern.format(seed=seed_keyword)
        queries_run.append(query)
        results = web_search(query, max_results=6)
        for r in results:
            domain = urlparse(r.get("url", "")).netloc
            if domain and "error" not in r:
                domain_freq.setdefault(domain, {"domain": domain, "count": 0, "titles": []})
                domain_freq[domain]["count"] += 1
                if len(domain_freq[domain]["titles"]) < 2:
                    domain_freq[domain]["titles"].append(r.get("title", ""))
        time.sleep(0.4)

    top_domains = sorted(domain_freq.values(), key=lambda x: x["count"], reverse=True)[:8]
    return {"seed_keyword": seed_keyword, "queries_run": queries_run, "top_ranking_domains": top_domains}


def get_google_trends(topic: str) -> dict:
    """
    Fetch rising and top related queries for a topic via pytrends (Google Trends).
    Returns empty lists if pytrends is unavailable or rate-limited.
    """
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        pytrends.build_payload([topic], timeframe="today 3-m")
        related = pytrends.related_queries()

        rising, top = [], []
        if topic in related:
            r_df = related[topic].get("rising")
            t_df = related[topic].get("top")
            if r_df is not None and not r_df.empty:
                rising = r_df.head(10)[["query", "value"]].to_dict("records")
            if t_df is not None and not t_df.empty:
                top = t_df.head(10)[["query", "value"]].to_dict("records")

        return {"topic": topic, "rising_queries": rising, "top_queries": top}
    except Exception as e:
        return {"topic": topic, "rising_queries": [], "top_queries": [], "note": str(e)}
