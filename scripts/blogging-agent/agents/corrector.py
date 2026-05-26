import os
from datetime import datetime, timedelta
from pathlib import Path

import config
from .base import BaseAgent
from storage.db import (
    get_flagged_posts, get_latest_snapshots, save_correction_log, mark_correction_executed,
)

TOOLS = [
    {
        "name": "save_correction_decision",
        "description": "Log your correction decision for a post. Call once per post.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["rewrite", "retitle", "requeue", "wait"],
                    "description": "rewrite=full refresh, retitle=title+meta only, requeue=new angle, wait=check later",
                },
                "reason": {"type": "string"},
                "new_title": {"type": "string", "description": "Required for retitle action"},
                "new_meta_description": {"type": "string", "description": "Required for retitle action"},
                "new_keyword": {"type": "string", "description": "New keyword angle for requeue action"},
                "check_after_days": {
                    "type": "integer",
                    "description": "For wait action: re-check after this many days",
                    "default": 14,
                },
            },
            "required": ["slug", "action", "reason"],
        },
    },
]

SYSTEM = """You are a content performance analyst. You review underperforming blog posts and decide the best corrective action.

Blog niche: {niche}

For each flagged post you are given:
- GSC data: clicks, impressions, position, CTR
- GA4 sessions
- Age in days
- Health score (0-100) and flag reason (low_ranking, low_traffic)
- Post title and keyword

Top-performing posts on this blog for context:
{top_performers}

For each flagged post, choose ONE action:
- rewrite: article needs a full content overhaul (position 15-30, decent impressions but low CTR — content quality issue)
- retitle: title/meta is weak but content is solid (good position 5-15, low CTR — title/meta issue). Propose a better title and meta description.
- requeue: topic is wrong angle or too competitive — archive and suggest a better keyword angle
- wait: recently flagged, give it more time (check_after_days: 14-30)

Decision logic:
- good position (≤15) + low CTR → retitle
- poor position (>20) + high impressions → rewrite (content needs depth)
- poor position + low impressions → requeue (wrong keyword or intent)
- age < 60 days → usually wait

Call save_correction_decision for each post. Be concise in reason (1-2 sentences)."""


class CorrectorAgent(BaseAgent):
    def __init__(self, client):
        super().__init__(client)
        self._decisions: list[dict] = []

    def correct_posts(self) -> list[dict]:
        flagged = get_flagged_posts()
        if not flagged:
            return []

        top_snaps = get_latest_snapshots()
        top_performers = [s for s in top_snaps if s["health_score"] >= 70][:5]
        top_summary = "\n".join(
            f"  - {s['slug']}: pos={s['gsc_position']:.1f} ctr={s['gsc_ctr']:.1f}% sessions={s['ga4_sessions']}"
            for s in top_performers
        ) or "  (no high performers yet)"

        system = SYSTEM.format(
            niche=config.BLOG_NICHE or "general",
            top_performers=top_summary,
        )

        posts_text = []
        for snap in flagged:
            posts_text.append(
                f"slug={snap['slug']} keyword={snap.get('keyword', '?')} "
                f"pos={snap['gsc_position']:.1f} imp={snap['gsc_impressions']} "
                f"clicks={snap['gsc_clicks']} ctr={snap['gsc_ctr']:.1f}% "
                f"sessions={snap['ga4_sessions']} health={snap['health_score']} "
                f"flag={snap['flag']}"
            )

        prompt = (
            f"Review these {len(flagged)} underperforming post(s) and decide the corrective action for each:\n\n"
            + "\n".join(posts_text)
            + "\n\nCall save_correction_decision for each post."
        )

        self.run(prompt, system, TOOLS, max_iterations=len(flagged) + 5)
        return self._decisions

    def _execute_tool(self, name: str, inputs: dict) -> dict:
        if name != "save_correction_decision":
            return {"error": f"Unknown tool: {name}"}

        slug = inputs["slug"]
        action = inputs["action"]
        reason = inputs["reason"]
        check_after = None

        auto_mode = os.getenv("CORRECTION_AUTO_MODE", config.CORRECTION_AUTO_MODE).lower() == "true"

        if not auto_mode:
            from rich.console import Console
            console = Console()
            console.print(f"\n[bold]Correction decision for [cyan]{slug}[/cyan][/bold]")
            console.print(f"  Action: [yellow]{action}[/yellow]")
            console.print(f"  Reason: {reason}")
            if inputs.get("new_title"):
                console.print(f"  New title: {inputs['new_title']}")
            confirm = input("  Execute? [y/N]: ").strip().lower()
            if confirm != "y":
                self._decisions.append({"slug": slug, "action": action, "skipped": True})
                return {"skipped": True, "slug": slug}

        if action == "wait":
            days = inputs.get("check_after_days", 14)
            check_after = (datetime.now() + timedelta(days=days)).isoformat()[:10]
            log_id = save_correction_log(slug, action, reason, check_after)
            mark_correction_executed(log_id)

        elif action == "retitle":
            new_title = inputs.get("new_title", "")
            new_meta = inputs.get("new_meta_description", "")
            log_id = save_correction_log(slug, action, reason)
            if new_title:
                pr_url = self._create_retitle_pr(slug, new_title, new_meta)
                if pr_url:
                    mark_correction_executed(log_id)
                    self._decisions.append({"slug": slug, "action": action, "pr_url": pr_url})
                    return {"success": True, "pr_url": pr_url}

        elif action == "rewrite":
            from agents.refresh import RefreshAgent
            article = self._load_article(slug)
            if article:
                agent = RefreshAgent(self.client)
                agent.refresh_article(article)
            log_id = save_correction_log(slug, action, reason)
            mark_correction_executed(log_id)

        elif action == "requeue":
            new_keyword = inputs.get("new_keyword", "")
            from storage.db import save_topic
            if new_keyword:
                save_topic(
                    title=f"[Requeued] {slug.replace('-', ' ').title()}",
                    keyword=new_keyword,
                    research_brief=f"Previous post on '{slug}' was requeued. New angle: {reason}",
                    source="corrector",
                    priority_score=0.6,
                )
            log_id = save_correction_log(slug, action, reason)
            mark_correction_executed(log_id)

        self._decisions.append({"slug": slug, "action": action, "reason": reason})
        return {"success": True, "slug": slug, "action": action}

    def _load_article(self, slug: str) -> dict | None:
        if config.PUBLISHER == "changeimageto":
            from publishers.changeimageto import load_article_from_html
            return load_article_from_html(slug)

        from storage.db import get_all_post_memory
        posts = get_all_post_memory()
        post = next((p for p in posts if p["slug"] == slug), None)
        if not post:
            return None

        repo_dir = Path(config.REPO_DIR).resolve() if config.REPO_DIR else Path.cwd()
        content_dir = repo_dir / config.CONTENT_DIR

        for md_file in content_dir.glob("*.md"):
            if slug in md_file.stem:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
                return {
                    "file_path": str(md_file),
                    "title": post["title"],
                    "keyword": post["keyword"],
                    "slug": slug,
                    "date_published": post.get("published_at", ""),
                    "age_days": 0,
                    "content": text,
                }
        return None

    def _create_retitle_pr(self, slug: str, new_title: str, new_meta: str) -> str | None:
        if config.PUBLISHER == "changeimageto":
            from publishers.changeimageto import retitle_post
            from orchestrator import _auto_publish_retitle
            path = retitle_post(slug, new_title, new_meta)
            if path and os.getenv("CORRECTION_AUTO_MODE", config.CORRECTION_AUTO_MODE).lower() == "true":
                return _auto_publish_retitle(path, new_title)
            return str(path) if path else None

        import subprocess
        from orchestrator import _open_pr, _parse_frontmatter

        repo_dir = Path(config.REPO_DIR).resolve() if config.REPO_DIR else Path.cwd()
        content_dir = repo_dir / config.CONTENT_DIR

        for md_file in content_dir.glob("*.md"):
            if slug in md_file.stem:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
                fm, body = _parse_frontmatter(text)
                fm["title"] = new_title
                if new_meta:
                    fm["description"] = new_meta
                fm_lines = "\n".join(f'{k}: "{v}"' for k, v in fm.items())
                updated = f"---\n{fm_lines}\n---\n\n{body}"
                date_str = datetime.now().strftime("%Y-%m-%d")
                return _open_pr(
                    repo_dir=repo_dir,
                    branch=f"retitle/{date_str}-{slug}",
                    filepath=md_file,
                    content=updated,
                    commit_msg=f"retitle: {new_title}",
                    pr_title=f"Retitle: {new_title}",
                    pr_body=f"**Correction action:** retitle\n**New title:** {new_title}\n**New meta:** {new_meta}",
                )
        return None
