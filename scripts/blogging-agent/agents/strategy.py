import json
from .base import BaseAgent
from tools.search import web_search
from tools.serp import analyze_serp, find_competitors_for_niche
from tools.competitors import get_sitemap_posts, fetch_post_summary
from tools.keyword_discovery import discover_keywords, get_google_trends
from storage.db import save_strategy

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for niche context, competitor background, or industry insights.",
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
        "name": "find_serp_competitors",
        "description": "Search DuckDuckGo for niche-related queries and find which domains appear most often. These are your real SEO competitors.",
        "input_schema": {
            "type": "object",
            "properties": {
                "niche": {"type": "string", "description": "The blog niche (e.g. 'B2B SaaS marketing')"},
                "num_queries": {"type": "integer", "default": 5},
            },
            "required": ["niche"],
        },
    },
    {
        "name": "analyze_serp",
        "description": "Check who ranks top-10 for a specific keyword. Use to map the competitive landscape for key pillar topics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_competitor_posts",
        "description": "Fetch a competitor's sitemap to see their recent content. Reveals topic focus and publishing cadence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string", "description": "Competitor's base URL, e.g. https://example.com"},
                "days_recent": {"type": "integer", "default": 45},
            },
            "required": ["site_url"],
        },
    },
    {
        "name": "analyze_post",
        "description": "Fetch one competitor post and extract: title, H2 structure, word count. Use to understand content depth and structure.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "discover_keywords",
        "description": "Expand a seed keyword using intent-based search patterns (how to X, best X, X guide, etc.) via DuckDuckGo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seed_keyword": {"type": "string"},
                "max_patterns": {"type": "integer", "default": 6},
            },
            "required": ["seed_keyword"],
        },
    },
    {
        "name": "get_google_trends",
        "description": "Fetch rising and top related queries for a topic via Google Trends. Good for spotting emerging keyword opportunities.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "save_strategy",
        "description": "Save the finalized content strategy. Call this LAST, after all research is complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content_pillars": {
                    "type": "array",
                    "description": "4-6 pillar topic clusters. Each has name, description, and 5-8 target keywords.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "target_keywords": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "competitors": {
                    "type": "array",
                    "description": "5-8 competitor sites to monitor.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "name": {"type": "string"},
                            "focus": {"type": "string", "description": "What they mostly write about"},
                            "why_relevant": {"type": "string"},
                        },
                    },
                },
                "content_gaps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Topics competitors rank for that this blog doesn't cover yet.",
                },
                "quick_wins": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Low-competition keywords or topics to go after first.",
                },
                "avoid_topics": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "strategic_summary": {
                    "type": "string",
                    "description": "2-3 sentence strategic rationale: why these pillars, why these competitors, what the content angle should be.",
                },
            },
            "required": ["content_pillars", "competitors", "content_gaps", "quick_wins", "strategic_summary"],
        },
    },
]

SYSTEM = """You are a senior content strategist building a 6-month blogging strategy from scratch.

You have the following context from the blog owner:

{interview_context}

Your research process:
1. Call find_serp_competitors to discover who dominates the SERPs for this niche (these are the real competitors)
2. For the top 3-4 competitors, call get_competitor_posts to see what they're publishing
3. Analyze 1-2 posts from each to understand content depth and structure
4. Call analyze_serp for 3-5 important keywords to map the competitive landscape
5. Call discover_keywords for 2-3 seed keywords to find keyword clusters the blog hasn't targeted yet
6. Call get_google_trends to spot rising opportunities
7. Synthesize everything into a strategy with clear content pillars, a focused competitor list, gaps to exploit, and quick wins

Strategy quality bar:
- Content pillars must be specific, not generic: "SaaS onboarding that reduces churn" > "SaaS tips"
- Each pillar needs real keywords with search intent (not just vague topics)
- Competitors must be actual SEO competitors (rank for same terms), not just well-known blogs
- Content gaps = topics competitors rank for top 3 that your site doesn't have ANY content on
- Quick wins = keywords with likely low competition (long-tail, specific, not dominated by domain authority giants)

Think like someone who has to achieve results in 90 days, not build a 5-year brand."""


class StrategyAgent(BaseAgent):
    def build_strategy(self, interview: dict) -> None:
        context_lines = [
            f"- Blog niche: {interview['niche']}",
            f"- Primary goal: {interview['goal']}",
            f"- Target reader: {interview['target_reader']}",
            f"- Desired reader action: {interview['desired_action']}",
            f"- Publishing frequency: {interview['frequency']}",
        ]
        if interview.get("avoid"):
            context_lines.append(f"- Avoid: {interview['avoid']}")

        interview_context = "\n".join(context_lines)
        system = SYSTEM.format(interview_context=interview_context)

        prompt = (
            f"Build a complete blogging strategy for a '{interview['niche']}' blog. "
            f"Research the competitive landscape, identify content pillars and keyword clusters, "
            f"find content gaps, and save the strategy."
        )
        self.run(prompt, system, TOOLS, max_iterations=25)

    def _execute_tool(self, name: str, inputs: dict):
        if name == "web_search":
            return web_search(inputs["query"], inputs.get("max_results", 8))
        elif name == "find_serp_competitors":
            return find_competitors_for_niche(inputs["niche"], inputs.get("num_queries", 5))
        elif name == "analyze_serp":
            return analyze_serp(inputs["keyword"], inputs.get("max_results", 10))
        elif name == "get_competitor_posts":
            return get_sitemap_posts(inputs["site_url"], inputs.get("days_recent", 45))
        elif name == "analyze_post":
            return fetch_post_summary(inputs["url"])
        elif name == "discover_keywords":
            return discover_keywords(inputs["seed_keyword"], inputs.get("max_patterns", 6))
        elif name == "get_google_trends":
            return get_google_trends(inputs["topic"])
        elif name == "save_strategy":
            save_strategy(inputs)
            return {"success": True}
        return {"error": f"Unknown tool: {name}"}
