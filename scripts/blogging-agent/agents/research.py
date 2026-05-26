import json
import config
from .base import BaseAgent
from tools.search import web_search
from tools.gsc import get_top_queries, get_rising_queries
from tools.ga4 import get_top_pages, get_declining_pages
from tools.serp import analyze_serp
from tools.competitors import get_sitemap_posts, fetch_post_summary
from tools.keyword_discovery import discover_keywords, get_google_trends
from storage.db import save_topic, get_active_strategy, save_competitor_posts

TOOLS = [
    {
        "name": "web_search",
        "description": "Search DuckDuckGo for trending topics, questions people ask, and competitor content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_gsc_queries",
        "description": "GSC: queries where you already appear — find low-CTR or position 5-20 opportunities. Returns [] if not configured.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 28}},
        },
    },
    {
        "name": "get_gsc_rising",
        "description": "GSC: queries gaining impressions recently. Returns [] if not configured.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 14}},
        },
    },
    {
        "name": "get_ga4_top_pages",
        "description": "GA4: top pages by sessions — understand what content format/topic already works. Returns [] if not configured.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 28},
                "limit": {"type": "integer", "default": 15},
            },
        },
    },
    {
        "name": "get_ga4_declining",
        "description": "GA4: pages losing traffic — candidates for refresh articles. Returns [] if not configured.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 28}},
        },
    },
    {
        "name": "check_competitor_new_posts",
        "description": "Check a competitor's sitemap for posts published in the last N days that you haven't seen before. Use for each competitor in your strategy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string"},
                "days_recent": {"type": "integer", "default": 14},
            },
            "required": ["site_url"],
        },
    },
    {
        "name": "analyze_post",
        "description": "Fetch a competitor post and extract title, H2 structure, word count. Use to understand what angle they took on a topic.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "analyze_serp",
        "description": "Check who ranks top-10 for a keyword. Use to gauge competition before recommending a topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "max_results": {"type": "integer", "default": 8},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "discover_keywords",
        "description": "Expand a seed keyword with intent-based patterns (how to X, best X, X guide…) to find related keyword clusters not in GSC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seed_keyword": {"type": "string"},
                "max_patterns": {"type": "integer", "default": 5},
            },
            "required": ["seed_keyword"],
        },
    },
    {
        "name": "get_google_trends",
        "description": "Rising and top related queries from Google Trends for a topic.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "save_topic",
        "description": "Save a topic to the writing queue. Call for each of your 3-5 final recommendations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Specific, SEO-friendly article title"},
                "keyword": {"type": "string", "description": "Primary keyword"},
                "research_brief": {
                    "type": "string",
                    "description": "Brief for the writer: angle/hook, 4-6 subtopics, target reader pain point, competitor insight, suggested word count",
                },
                "source": {"type": "string", "default": "web_search"},
                "priority_score": {"type": "number", "default": 0.5},
            },
            "required": ["title", "keyword", "research_brief"],
        },
    },
]

SYSTEM = """You are an expert SEO research agent. Your job is to find 3-5 high-potential blog topics for this run.

Blog niche: {niche}
Target audience: {audience}

Active content strategy:
{strategy_context}

Research process for this run:
1. Check GSC (get_gsc_queries + get_gsc_rising) — find queries with impressions but low CTR or position 5-20
2. Check GA4 (get_ga4_top_pages + get_ga4_declining) — understand what's working and what needs a refresh
3. Check each competitor in the strategy for NEW posts (check_competitor_new_posts) — if they just published something in your content pillars, either counter it or find the angle they missed
4. For 1-2 competitor posts that look interesting, analyze_post to understand their angle and structure
5. For keywords NOT in GSC (i.e. topics you're not indexed for), use discover_keywords on pillar seed keywords
6. Use get_google_trends on 1-2 pillar topics to catch rising queries
7. Use analyze_serp to check competition level before committing to a topic
8. Save 3-5 topics with detailed research briefs

Topic selection priority:
- Competitor just posted on a pillar topic → counter with a better/different angle
- GSC: high impressions, position 5-20, low CTR → strong signal
- Content gap from strategy + low SERP competition → good long-term bet
- Rising Google Trends query in your pillar → early mover advantage
- GA4 declining page → refresh opportunity (easier than new content)

For each research_brief include:
- Main angle/hook (what makes this different from what competitors already wrote)
- 4-6 subtopics/sections
- Target reader pain point
- Competitive insight (who ranks, their weakness)
- Suggested word count (800-2500)"""


class ResearchAgent(BaseAgent):
    def run_research(self) -> None:
        strategy = get_active_strategy()
        strategy_context = _format_strategy(strategy)

        system = SYSTEM.format(
            niche=config.BLOG_NICHE or "general topics",
            audience=config.TARGET_AUDIENCE,
            strategy_context=strategy_context,
        )
        prompt = (
            f"Research and find 3-5 high-potential blog topics for a '{config.BLOG_NICHE or 'general'}' blog. "
            f"Check competitors for new posts, use keyword discovery for un-indexed topics, "
            f"and validate competition level before saving each topic."
        )
        self.run(prompt, system, TOOLS, max_iterations=25)

    def _execute_tool(self, name: str, inputs: dict):
        if name == "web_search":
            return web_search(inputs["query"], inputs.get("max_results", 8))
        elif name == "get_gsc_queries":
            return get_top_queries(inputs.get("days", 28))
        elif name == "get_gsc_rising":
            return get_rising_queries(inputs.get("days", 14))
        elif name == "get_ga4_top_pages":
            return get_top_pages(inputs.get("days", 28), inputs.get("limit", 15))
        elif name == "get_ga4_declining":
            return get_declining_pages(inputs.get("days", 28))
        elif name == "check_competitor_new_posts":
            site_url = inputs["site_url"]
            posts = get_sitemap_posts(site_url, inputs.get("days_recent", 14))
            if posts and "error" not in posts[0]:
                new = save_competitor_posts(site_url, posts)
                return {"new_posts": new, "total_recent": len(posts), "new_count": len(new)}
            return posts
        elif name == "analyze_post":
            return fetch_post_summary(inputs["url"])
        elif name == "analyze_serp":
            return analyze_serp(inputs["keyword"], inputs.get("max_results", 8))
        elif name == "discover_keywords":
            return discover_keywords(inputs["seed_keyword"], inputs.get("max_patterns", 5))
        elif name == "get_google_trends":
            return get_google_trends(inputs["topic"])
        elif name == "save_topic":
            from dedup import DedupChecker
            is_dup, reason, match = DedupChecker().check(inputs["title"], inputs["keyword"])
            if is_dup:
                return {
                    "skipped": True,
                    "reason": f"Duplicate detected: {reason}",
                    "nearest_match": match.get("title") if match else None,
                }
            topic_id = save_topic(
                title=inputs["title"],
                keyword=inputs["keyword"],
                research_brief=inputs["research_brief"],
                source=inputs.get("source", "web_search"),
                priority_score=inputs.get("priority_score", 0.5),
            )
            return {"success": True, "topic_id": topic_id, "saved": inputs["title"]}
        return {"error": f"Unknown tool: {name}"}


def _format_strategy(strategy: dict | None) -> str:
    if not strategy:
        return "No strategy set yet. Run: python main.py strategy\nProceeding with general research."

    pillars = strategy.get("content_pillars", [])
    competitors = strategy.get("competitors", [])
    quick_wins = strategy.get("quick_wins", [])
    gaps = strategy.get("content_gaps", [])

    lines = []
    if pillars:
        lines.append("Content pillars:")
        for p in pillars:
            kws = ", ".join(p.get("target_keywords", [])[:4])
            lines.append(f"  - {p['name']}: {kws}")
    if competitors:
        lines.append("\nCompetitors to monitor:")
        for c in competitors:
            lines.append(f"  - {c.get('url', '')} ({c.get('focus', '')})")
    if quick_wins:
        lines.append(f"\nQuick win keywords: {', '.join(quick_wins[:6])}")
    if gaps:
        lines.append(f"\nContent gaps to fill: {', '.join(gaps[:5])}")
    if strategy.get("strategic_summary"):
        lines.append(f"\nStrategy: {strategy['strategic_summary']}")

    return "\n".join(lines)
