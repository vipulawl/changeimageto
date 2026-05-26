from .base import BaseAgent
from tools.search import web_search
from tools.serp import analyze_serp
from tools.keyword_discovery import get_google_trends
from storage.db import save_refresh

TOOLS = [
    {
        "name": "web_search",
        "description": "Search for current information on the topic — updated stats, new developments, recent examples.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 6},
            },
            "required": ["query"],
        },
    },
    {
        "name": "analyze_serp",
        "description": "Check who ranks top-10 for the keyword NOW. Compare their angle, structure, and coverage to the current article.",
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
        "name": "get_google_trends",
        "description": "Check if related queries or search intent has shifted since publication.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "save_refresh",
        "description": "Save the refreshed article. Call this ONCE when you have finished all research and rewriting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "refreshed_content": {
                    "type": "string",
                    "description": "Full updated article in markdown — do NOT include frontmatter",
                },
                "refresh_notes": {
                    "type": "string",
                    "description": "Bullet list of exactly what changed and why (be specific: 'Updated 2023 stat to 2025 in section 2', 'Added new H2 on X because top competitor covers it')",
                },
                "refresh_score": {
                    "type": "integer",
                    "description": "1=minor (stats/dates only), 3=moderate (section rewrites), 5=major restructure",
                },
                "meta_description": {
                    "type": "string",
                    "description": "Updated meta description if keyword landscape shifted (optional — omit if still accurate)",
                },
            },
            "required": ["refreshed_content", "refresh_notes", "refresh_score"],
        },
    },
]

SYSTEM = """You are a content refresh specialist. Review an existing blog article and update it to remain competitive and accurate.

Article details:
{article_context}

Refresh process:
1. analyze_serp for the primary keyword — who ranks top-5 now? What angle, depth, or sections do they have that this article lacks?
2. web_search for "[keyword] {current_year}" and "[keyword] latest" — find updated stats, new developments, or changed best practices
3. get_google_trends — has search intent or related queries shifted?
4. Review the existing article critically against what you found
5. Save the refreshed version

What to update:
- Outdated stats, years, or examples → replace with current data from your search
- Sections where top-ranking competitors go deeper → expand with fresh content
- New developments since publication → add a new section or update existing
- Keyword opportunities from trends → weave naturally into existing sections
- Meta description → update only if the keyword angle has meaningfully shifted

What NOT to change:
- The URL slug
- The article's core structure if it's still sound
- The author's voice and style
- Sections that are still accurate and competitive

Score honestly: if the article only needs a stat update (score 1-2), say so in refresh_notes.
If it needs significant new sections (score 4-5), rewrite substantially.

Do NOT save if the article is still fully current and competitive — explain that in a final message instead."""


class RefreshAgent(BaseAgent):
    def refresh_article(self, article: dict) -> bool:
        self._topic_title = article.get("title")
        """
        Review and refresh an article. Returns True if a refresh was saved, False if skipped.
        article dict: file_path, title, keyword, slug, date_published, age_days, content
        """
        from datetime import datetime
        current_year = datetime.now().year

        context = (
            f"Title: {article['title']}\n"
            f"Primary keyword: {article['keyword']}\n"
            f"Published: {article.get('date_published', 'unknown')} ({article['age_days']} days ago)\n"
            f"Slug: {article['slug']}\n"
        )
        if article.get("declining_traffic"):
            context += f"GA4 signal: traffic has declined {article['declining_traffic']}% — refresh is high priority\n"

        system = SYSTEM.format(
            article_context=context,
            current_year=current_year,
        )

        prompt = f"""Review and refresh this article.

{context}

Current article content:
---
{article['content']}
---

Research what has changed, then save the refreshed version.
If the article is fully current and competitive, explain why no refresh is needed (do NOT call save_refresh)."""

        self._article = article
        self._refresh_saved = False
        self.run(prompt, system, TOOLS, max_iterations=15)
        return self._refresh_saved

    def _execute_tool(self, name: str, inputs: dict):
        if name == "web_search":
            return web_search(inputs["query"], inputs.get("max_results", 6))
        elif name == "analyze_serp":
            return analyze_serp(inputs["keyword"], inputs.get("max_results", 8))
        elif name == "get_google_trends":
            return get_google_trends(inputs["topic"])
        elif name == "save_refresh":
            a = self._article
            refresh_id = save_refresh(
                file_path=a["file_path"],
                title=a["title"],
                keyword=a["keyword"],
                slug=a["slug"],
                original_content=a["content"],
                refreshed_content=inputs["refreshed_content"],
                meta_description=inputs.get("meta_description", a.get("meta_description", "")),
                refresh_notes=inputs["refresh_notes"],
                refresh_score=inputs.get("refresh_score", 1),
            )
            self._refresh_saved = True
            return {"success": True, "refresh_id": refresh_id}
        return {"error": f"Unknown tool: {name}"}
