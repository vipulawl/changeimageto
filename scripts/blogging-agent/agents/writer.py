import re

from rich.console import Console

import config
from .base import BaseAgent
from tools.search import web_search
from storage.db import get_latest_draft_for_topic, save_draft

console = Console()

TOOLS = [
    {
        "name": "web_search",
        "description": "Search for additional facts, examples, or statistics to strengthen the article.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_draft",
        "description": "Save the completed article draft. Call this once the full article is written.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic_id": {"type": "integer"},
                "title": {"type": "string", "description": "Final article title"},
                "slug": {"type": "string", "description": "URL slug — lowercase, hyphens, no special chars"},
                "meta_description": {"type": "string", "description": "SEO meta description, 150–160 characters"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2–5 topic tags",
                },
                "content": {"type": "string", "description": "Full article in markdown. Do not include frontmatter — that is added automatically."},
            },
            "required": ["topic_id", "title", "slug", "meta_description", "content"],
        },
    },
]

SAVE_DRAFT_TOOL = [t for t in TOOLS if t["name"] == "save_draft"]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:80] or "post"


def _looks_like_article(text: str) -> bool:
    if not text or len(text) < 500:
        return False
    if re.search(r"^#{1,3}\s", text, re.MULTILINE):
        return True
    return text.count("\n") >= 10


def _meta_from_content(content: str, keyword: str) -> str:
    plain = re.sub(r"#+\s*", "", content)
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", plain)
    plain = re.sub(r"[*_`]", "", plain)
    first_para = plain.strip().split("\n\n")[0].replace("\n", " ").strip()
    if len(first_para) >= 100:
        return first_para[:157] + "..." if len(first_para) > 160 else first_para
    return f"Practical guide to {keyword}."[:160]


def _default_tags(keyword: str) -> list[str]:
    words = [w.capitalize() for w in re.split(r"[\s-]+", keyword) if w][:3]
    return words or ["Blog"]


SYSTEM = """You are an expert blog writer. Write high-quality, SEO-optimised articles that provide genuine value.

Blog niche: {niche}
Target audience: {audience}
Tone: {tone}
Language: {language}

Writing rules:
- Open with a hook that speaks directly to the reader's problem — no "In this article we will..."
- Use H2 for main sections, H3 for subsections
- Back claims with specific examples, data, or concrete scenarios
- Keep sentences short. Vary rhythm. No padding.
- Place the primary keyword naturally in: first 100 words, one H2, meta description
- End with a clear, actionable conclusion — no "In conclusion..."
- Format: clean markdown. No HTML.
{site_rules}

Do 1–2 web searches if you need specific data or examples to make a point concrete. Then write the full article and save it.

{link_candidates_section}"""

SITE_RULES_CHANGEIMAGETO = """
Site-specific rules (ChangeImageTo.com):
- This blog supports a free online image editing platform (remove background, change colors, resize, blur, upscale, enhance).
- Include 2–3 internal links to our tools using paths like /remove-background-from-image.html, /blur-background.html, /upscale-image.html, /enhance-image.html, /change-image-background.html, /bulk-image-resizer.html
- Link to related blog posts as /blog/slug.html when relevant.
- Add a short FAQ section with 2–3 questions at the end (use ### for FAQ headings).
"""

class WriterAgent(BaseAgent):
    def write_article(self, topic: dict) -> bool:
        self._topic_id = topic.get("id")
        self._topic_title = topic.get("title")
        link_candidates = topic.get("link_candidates", [])
        if link_candidates:
            lines = ["Internal link candidates (link to these where naturally relevant):"]
            for c in link_candidates:
                slug = c["slug"]
                path = f"/blog/{slug}.html" if config.PUBLISHER == "changeimageto" else f"/{slug}"
                lines.append(f"  - [{c['title']}]({path}) — {c.get('summary', '')[:100]}")
            link_section = "\n".join(lines)
        else:
            link_section = ""

        site_rules = SITE_RULES_CHANGEIMAGETO if config.PUBLISHER == "changeimageto" else ""

        system = SYSTEM.format(
            niche=config.BLOG_NICHE or "general topics",
            audience=config.TARGET_AUDIENCE,
            tone=config.BLOG_TONE,
            language=config.BLOG_LANGUAGE,
            site_rules=site_rules,
            link_candidates_section=link_section,
        )
        prompt = f"""Write a complete blog article from this research brief.

**Title:** {topic['title']}
**Primary keyword:** {topic['keyword']}
**Topic ID:** {topic['id']}

**Research brief:**
{topic['research_brief']}

Do any needed web searches, write the full article in markdown, then save it with save_draft.
You must call save_draft — do not reply with the article as plain text."""

        final_text = self.run(prompt, system, TOOLS)
        topic_id = topic["id"]

        if self._has_draft(topic_id):
            return True

        if _looks_like_article(final_text):
            console.print("[yellow]Writer returned text without save_draft — saving from response.[/yellow]")
            self._save_fallback_draft(topic, final_text)
            if self._has_draft(topic_id):
                return True

        console.print("[yellow]Writer did not call save_draft — retrying with save_draft only.[/yellow]")
        nudge = f"""You did not call save_draft. Call save_draft now with the complete article.

**Topic ID:** {topic_id}
**Title:** {topic['title']}
**Suggested slug:** {_slugify(topic['title'])}
**Primary keyword:** {topic['keyword']}

Include the full markdown article in the content field. Do not web_search."""
        if final_text and len(final_text.strip()) > 200:
            nudge += f"\n\n**Article to save:**\n{final_text.strip()}"

        self.run(nudge, system, SAVE_DRAFT_TOOL, max_iterations=3)
        return self._has_draft(topic_id)

    def _has_draft(self, topic_id: int) -> bool:
        return get_latest_draft_for_topic(topic_id) is not None

    def _save_fallback_draft(self, topic: dict, content: str) -> None:
        save_draft(
            topic_id=topic["id"],
            title=topic["title"],
            slug=_slugify(topic["title"]),
            meta_description=_meta_from_content(content, topic["keyword"]),
            tags=_default_tags(topic["keyword"]),
            content=content.strip(),
        )

    def _execute_tool(self, name: str, inputs: dict):
        if name == "web_search":
            return web_search(inputs["query"], inputs.get("max_results", 5))
        elif name == "save_draft":
            draft_id = save_draft(
                topic_id=inputs["topic_id"],
                title=inputs["title"],
                slug=inputs["slug"],
                meta_description=inputs["meta_description"],
                tags=inputs.get("tags", []),
                content=inputs["content"],
            )
            return {"success": True, "draft_id": draft_id}
        return {"error": f"Unknown tool: {name}"}
