import json
import subprocess
import os
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt

import config
from agents.research import ResearchAgent
from agents.writer import WriterAgent
from agents.editor import EditorAgent
from agents.strategy import StrategyAgent
from agents.refresh import RefreshAgent
from storage.db import (
    get_next_topic, get_topic_by_id, get_pending_drafts,
    get_latest_draft_for_topic, approve_draft, reject_draft,
    get_active_strategy, save_strategy,
    get_pending_refreshes, mark_refresh_done, was_recently_refreshed,
)
from memory.post_index import PostIndex
from dedup import DedupChecker

console = Console()


def _client():
    """Return the appropriate API client based on configured provider."""
    if config.PROVIDER == "openai":
        from openai import OpenAI
        if not config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not set in .env")
        return OpenAI(api_key=config.OPENAI_API_KEY)
    elif config.PROVIDER == "groq":
        from openai import OpenAI
        if not config.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY not set in .env")
        return OpenAI(api_key=config.GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    elif config.PROVIDER == "ollama":
        from openai import OpenAI
        return OpenAI(api_key="ollama", base_url=config.OLLAMA_BASE_URL)
    else:
        import anthropic
        return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def run_research():
    console.print("\n[bold blue]Research Agent[/bold blue] — finding topics...")
    ResearchAgent(_client()).run_research()
    console.print("[green]Done. Topics saved to queue.[/green]")


def run_write(topic_id: int = None):
    topic = get_topic_by_id(topic_id) if topic_id else get_next_topic()
    if not topic:
        console.print("[yellow]No queued topics. Run: python main.py research[/yellow]")
        return

    console.print(f"\n[bold]Topic:[/bold] {topic['title']}")
    console.print(f"[dim]Keyword: {topic['keyword']} | Source: {topic['source']}[/dim]\n")

    # Dedup check — warn but don't block (user chose this topic)
    is_dup, dup_reason, _ = DedupChecker().check(topic["title"], topic["keyword"])
    if is_dup:
        console.print(f"[yellow]Warning: near-duplicate detected — {dup_reason}[/yellow]")
        from rich.prompt import Prompt
        choice = Prompt.ask("Proceed anyway?", choices=["y", "n"], default="n")
        if choice != "y":
            console.print("[dim]Skipped.[/dim]")
            return

    # Attach internal link candidates from post memory
    index = PostIndex()
    topic["link_candidates"] = index.get_link_candidates(
        [topic["keyword"]] + topic.get("research_brief", "").split()[:10]
    )

    console.print("[bold blue]Writer Agent[/bold blue] — writing article...")
    WriterAgent(_client()).write_article(topic)

    draft = get_latest_draft_for_topic(topic["id"])
    if not draft:
        console.print("[red]Writer did not save a draft. Check API key and try again.[/red]")
        return

    draft["keyword"] = topic["keyword"]
    draft["research_brief"] = topic.get("research_brief", "")

    console.print("[bold blue]Editor Agent[/bold blue] — reviewing and editing...")
    EditorAgent(_client()).edit_article(draft)

    # Re-fetch the edited draft
    edited = get_latest_draft_for_topic(topic["id"])
    if not edited:
        console.print("[red]Editor did not save draft.[/red]")
        return

    if config.APPROVAL_MODE == "auto":
        path = _auto_publish(edited)
        if path:
            console.print(f"\n[green]Published →[/green] {path}")
        else:
            console.print("\n[red]Auto-publish failed. Check logs.[/red]")
    elif config.APPROVAL_MODE == "pr":
        pr_url = _create_pr(edited)
        if pr_url:
            console.print(f"\n[green]PR created →[/green] {pr_url}")
            console.print("[dim]Review in Cursor or GitHub. Merge = publish. Close = reject.[/dim]")
        else:
            console.print(f"\n[green]Article ready for review.[/green] Run: python main.py review")
    else:
        console.print(f"\n[green]Article ready for review.[/green] Run: python main.py review")


def run_review():
    pending = get_pending_drafts()
    if not pending:
        console.print("[yellow]No articles pending review.[/yellow]")
        return

    console.print(f"\n[bold]{len(pending)} article(s) awaiting review[/bold]\n")

    for draft in pending:
        console.rule(f"[bold]{draft['title']}[/bold]")
        console.print(f"[dim]Keyword:[/dim] {draft['keyword']}")
        console.print(f"[dim]Slug:[/dim] {draft['slug']}")
        console.print(f"[dim]Tags:[/dim] {', '.join(draft['tags'])}")
        console.print(f"[dim]Meta:[/dim] {draft['meta_description']}\n")

        if draft.get("edit_notes"):
            console.print(Panel(draft["edit_notes"], title="[yellow]Editor notes[/yellow]", border_style="yellow", padding=(0, 1)))

        console.print(Panel(Markdown(draft["content"]), title="[blue]Article preview[/blue]", border_style="blue"))

        choice = Prompt.ask(
            "\n[bold]Action[/bold]",
            choices=["a", "r", "s"],
            default="s",
            show_default=False,
            prompt_suffix=" → [a]pprove  [r]eject  [s]kip: ",
        )

        if choice == "a":
            approved = approve_draft(draft["id"])
            if config.PUBLISHER == "changeimageto":
                from publishers.changeimageto import publish_post
                path = publish_post(approved)
            else:
                path = _save_output(approved)
            PostIndex().add_post(
                slug=approved.get("slug", ""),
                title=approved.get("title", ""),
                keyword=approved.get("keyword", ""),
                tags=approved.get("tags", []),
                content=approved.get("content", ""),
            )
            console.print(f"[green]Approved →[/green] {path}\n")
        elif choice == "r":
            reject_draft(draft["id"])
            console.print("[red]Rejected.[/red]\n")
        else:
            console.print("[dim]Skipped.[/dim]\n")


def run_pipeline():
    from storage.db import get_all_topics
    queued = get_all_topics(status="queued")
    if not queued:
        run_research()
    run_write()
    if config.APPROVAL_MODE == "cli":
        run_review()


def run_schedule(dry_run: bool = False, as_json: bool = False) -> dict:
    from scheduler import evaluate
    return evaluate(dry_run=dry_run, as_json=as_json)


def run_monitor() -> None:
    from monitor import run_monitor as _run_monitor
    _run_monitor()


def run_correction() -> None:
    from agents.corrector import CorrectorAgent
    from storage.db import get_flagged_posts
    flagged = get_flagged_posts()
    if not flagged:
        console.print("[yellow]No flagged posts to correct. Run: python main.py monitor[/yellow]")
        return
    console.print(f"\n[bold blue]Corrector Agent[/bold blue] — reviewing {len(flagged)} flagged post(s)...")
    decisions = CorrectorAgent(_client()).correct_posts()
    for d in decisions:
        if d.get("skipped"):
            console.print(f"  [dim]Skipped:[/dim] {d['slug']}")
        else:
            color = {"rewrite": "yellow", "retitle": "cyan", "requeue": "magenta", "wait": "dim"}.get(d.get("action", ""), "white")
            console.print(f"  [{color}]{d.get('action', '?')}[/{color}]: {d['slug']}")


def run_refresh(max_articles: int = 2):
    """
    Scan published articles, pick stale candidates, run RefreshAgent on each,
    then open a PR per refresh. Skips articles refreshed within the last 60 days.
    """
    candidates = _find_refresh_candidates(max_candidates=max_articles)
    if not candidates:
        console.print("[yellow]No refresh candidates found (all articles are recent or recently refreshed).[/yellow]")
        return

    console.print(f"\n[bold]{len(candidates)} article(s) flagged for refresh[/bold]")

    for article in candidates:
        age = article["age_days"]
        signal = f"  [dim]Age: {age}d"
        if article.get("declining_traffic"):
            signal += f" | GA4 traffic –{article['declining_traffic']}%"
        signal += "[/dim]"
        console.print(f"\n[bold]{article['title']}[/bold]{signal}")
        console.print(f"[dim]Keyword: {article['keyword']} | {article['file_path']}[/dim]\n")

        console.print("[bold blue]Refresh Agent[/bold blue] — researching and rewriting...")
        agent = RefreshAgent(_client())
        saved = agent.refresh_article(article)

        if not saved:
            console.print("[dim]Agent determined no refresh needed — skipping.[/dim]")
            continue

        # Pull the saved refresh and create a PR
        pending = get_pending_refreshes()
        for r in pending:
            if r["file_path"] == article["file_path"] and r["status"] == "pending":
                pr_url = _open_pr_for_refresh(r)
                if pr_url:
                    console.print(f"[green]Refresh PR →[/green] {pr_url}")
                mark_refresh_done(r["id"], "pr_created")
                break


def _find_refresh_candidates(max_candidates: int = 2) -> list[dict]:
    """
    Scan CONTENT_DIR for published markdown files and score by refresh need.
    Scoring: older articles + GA4 declining traffic = higher priority.
    """
    from datetime import datetime
    from tools.ga4 import get_declining_pages

    repo_dir = Path(config.REPO_DIR).resolve() if config.REPO_DIR else Path.cwd()
    content_dir = repo_dir / config.CONTENT_DIR
    if not content_dir.exists():
        return []

    if config.PUBLISHER == "changeimageto":
        from publishers.changeimageto import load_article_from_html
        from storage.db import get_all_post_memory
        candidates = []
        for post in get_all_post_memory():
            article = load_article_from_html(post["slug"])
            if not article or was_recently_refreshed(article["file_path"]):
                continue
            if article["age_days"] < 60:
                continue
            decline_pct = declining.get(f"/blog/{post['slug']}.html", 0) or declining.get(f"/blog/{post['slug']}", 0)
            score = article["age_days"] / 30 + (decline_pct / 10)
            candidates.append({**article, "declining_traffic": decline_pct or None, "score": score})
        return sorted(candidates, key=lambda x: x["score"], reverse=True)[:max_candidates]

    # Index GA4 declining pages by path for quick lookup
    declining = {}
    try:
        for page in get_declining_pages(days=28):
            if not page.get("error"):
                declining[page["page_path"]] = page.get("decline_pct", 0)
    except Exception:
        pass

    candidates = []
    for md_file in sorted(content_dir.glob("*.md")):
        if md_file.name == ".gitkeep":
            continue

        text = md_file.read_text(encoding="utf-8", errors="ignore")
        fm, body = _parse_frontmatter(text)

        if fm.get("status") not in (None, "published", ""):
            continue

        if was_recently_refreshed(str(md_file)):
            continue

        date_str = fm.get("date", "2020-01-01")
        try:
            age_days = (datetime.now() - datetime.fromisoformat(date_str[:10])).days
        except Exception:
            age_days = 0

        if age_days < 60:
            continue

        slug = fm.get("slug", md_file.stem)
        decline_pct = declining.get(f"/{slug}", 0) or declining.get(f"/{slug}/", 0)

        score = age_days / 30 + (decline_pct / 10)  # months old + GA4 penalty

        candidates.append({
            "file_path": str(md_file),
            "title": fm.get("title", md_file.stem),
            "keyword": fm.get("keyword", fm.get("slug", "")),
            "slug": slug,
            "meta_description": fm.get("description", ""),
            "date_published": date_str,
            "age_days": age_days,
            "declining_traffic": decline_pct or None,
            "content": body,
            "score": score,
        })

    return sorted(candidates, key=lambda x: x["score"], reverse=True)[:max_candidates]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse simple YAML frontmatter (--- ... ---) from a markdown string."""
    if not text.startswith("---"):
        return {}, text
    try:
        end = text.index("---", 3)
        fm_text = text[3:end].strip()
        body = text[end + 3:].strip()
        fm = {}
        for line in fm_text.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                fm[key.strip()] = val.strip().strip('"').strip("'")
        return fm, body
    except ValueError:
        return {}, text


def run_strategy(force: bool = False, auto: bool = False):
    # CI / GitHub Actions mode: read interview answers from env vars, no prompts
    if auto:
        interview = {
            "niche":          os.environ.get("STRATEGY_NICHE", config.BLOG_NICHE),
            "goal":           os.environ.get("STRATEGY_GOAL", "organic SEO traffic"),
            "target_reader":  os.environ.get("STRATEGY_TARGET_READER", config.TARGET_AUDIENCE),
            "desired_action": os.environ.get("STRATEGY_DESIRED_ACTION", ""),
            "avoid":          os.environ.get("STRATEGY_AVOID", ""),
            "frequency":      os.environ.get("STRATEGY_FREQUENCY", "weekly"),
        }
        console.print("[bold blue]Strategy Agent[/bold blue] (CI mode) — building strategy...")
        for k, v in interview.items():
            console.print(f"  [dim]{k}:[/dim] {v}")
        console.print()
        StrategyAgent(_client()).build_strategy(interview)
        strategy = get_active_strategy()
        if strategy:
            _display_strategy(strategy)
            console.print("\n[green]Strategy saved.[/green]")
        else:
            console.print("[red]Strategy agent did not save. Check logs.[/red]")
        return

    existing = get_active_strategy()
    if existing and not force:
        console.print("\n[yellow]An active strategy already exists.[/yellow]")
        console.print(f"[dim]Created: {existing['created_at']}[/dim]")
        console.print(f"[dim]Pillars: {', '.join(p['name'] for p in existing['content_pillars'])}[/dim]")
        choice = Prompt.ask(
            "\nReplace it with a new strategy?",
            choices=["y", "n"],
            default="n",
        )
        if choice != "y":
            return

    console.print()
    console.rule("[bold blue]Blogging Strategy Setup[/bold blue]")
    console.print(
        "\nI'll ask 6 quick questions, then research your competitive landscape\n"
        "and build a full content strategy. Takes about 3–5 minutes.\n"
    )

    niche = Prompt.ask("[bold][1/6][/bold] What is your blog about?\n      [dim](Be specific — include niche, angle, and what makes it different)[/dim]\n     ")

    console.print("\n[bold][2/6][/bold] What is your primary goal?")
    console.print("      1. Grow organic SEO traffic")
    console.print("      2. Generate leads for a product/service")
    console.print("      3. Build brand authority / thought leadership")
    console.print("      4. Monetization (ads, affiliates, sponsorships)")
    goal_num = IntPrompt.ask("     ", default=1)
    goal_map = {1: "organic SEO traffic", 2: "lead generation", 3: "brand authority", 4: "monetization"}
    goal = goal_map.get(goal_num, "organic SEO traffic")

    target_reader = Prompt.ask(
        "\n[bold][3/6][/bold] Describe your target reader in one sentence\n      [dim](job title, situation, what they're trying to solve)[/dim]\n     "
    )

    desired_action = Prompt.ask(
        "\n[bold][4/6][/bold] What action should readers take after reading?\n      [dim](subscribe, book a call, buy X, share, etc.)[/dim]\n     "
    )

    avoid = Prompt.ask(
        "\n[bold][5/6][/bold] Any topics, angles, or keywords to explicitly avoid?\n      [dim](Press Enter to skip)[/dim]\n     ",
        default="",
    )

    console.print("\n[bold][6/6][/bold] Publishing frequency?")
    console.print("      1. Weekly")
    console.print("      2. Twice a week")
    console.print("      3. Daily")
    console.print("      4. Bi-weekly")
    freq_num = IntPrompt.ask("     ", default=1)
    freq_map = {1: "weekly", 2: "twice a week", 3: "daily", 4: "bi-weekly"}
    frequency = freq_map.get(freq_num, "weekly")

    interview = {
        "niche": niche,
        "goal": goal,
        "target_reader": target_reader,
        "desired_action": desired_action,
        "avoid": avoid,
        "frequency": frequency,
    }

    console.print(f"\n[green]Got it.[/green] Researching your competitive landscape...\n")
    console.print("[dim]This will search DuckDuckGo, scrape competitor sitemaps, and analyse keyword clusters.[/dim]\n")

    agent = StrategyAgent(_client())
    agent.build_strategy(interview)

    strategy = get_active_strategy()
    if not strategy:
        console.print("[red]Strategy agent did not save a strategy. Try again.[/red]")
        return

    _display_strategy(strategy)

    choice = Prompt.ask(
        "\n[bold]Approve this strategy?[/bold]",
        choices=["y", "n"],
        default="y",
        prompt_suffix=" [y]es / [n]o, discard: ",
    )

    if choice == "y":
        console.print("[green]Strategy saved. Research runs will now use this strategy.[/green]")
        console.print("Next: [bold]python main.py research[/bold]")
    else:
        # Deactivate
        from storage.db import get_conn
        with get_conn() as conn:
            conn.execute("UPDATE strategy SET is_active = 0 WHERE id = ?", (strategy["id"],))
        console.print("[yellow]Strategy discarded. Run 'python main.py strategy' again to rebuild.[/yellow]")


def _display_strategy(strategy: dict):
    console.print()
    console.rule("[bold]Strategy Summary[/bold]")

    pillars = strategy.get("content_pillars", [])
    if pillars:
        console.print("\n[bold]Content Pillars[/bold]")
        for p in pillars:
            kws = ", ".join(p.get("target_keywords", [])[:5])
            console.print(f"  [cyan]{p['name']}[/cyan] — {p.get('description', '')}")
            console.print(f"    [dim]Keywords: {kws}[/dim]")

    competitors = strategy.get("competitors", [])
    if competitors:
        console.print("\n[bold]Competitors to Monitor[/bold]")
        for c in competitors:
            console.print(f"  [yellow]{c.get('name', c.get('url', ''))}[/yellow] — {c.get('focus', '')} [dim]({c.get('url', '')})[/dim]")

    gaps = strategy.get("content_gaps", [])
    if gaps:
        console.print(f"\n[bold]Content Gaps[/bold] [dim](topics competitors rank for that you don't cover)[/dim]")
        for g in gaps[:6]:
            console.print(f"  · {g}")

    wins = strategy.get("quick_wins", [])
    if wins:
        console.print(f"\n[bold]Quick Wins[/bold] [dim](low-competition keywords to target first)[/dim]")
        for w in wins[:6]:
            console.print(f"  · {w}")

    if strategy.get("strategic_summary"):
        console.print(Panel(strategy["strategic_summary"], title="[green]Strategic rationale[/green]", border_style="green", padding=(0, 1)))


def _auto_publish_retitle(path: Path, new_title: str) -> str | None:
    repo_dir = Path(config.REPO_DIR).resolve() if config.REPO_DIR else Path.cwd()
    files = [path, repo_dir / "frontend" / "sitemap.xml"]
    if _publish_files(repo_dir, [f for f in files if f.exists()], f"retitle: {new_title}"):
        return str(path)
    return None


def _publish_files(repo_dir: Path, files: list[Path], commit_msg: str) -> bool:
    """Stage files, commit, and push to origin."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True, check=True)

    try:
        for f in files:
            git("add", str(f.relative_to(repo_dir)))
        git("commit", "-m", commit_msg)
        git("push", "origin", "HEAD")
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Git push failed: {e.stderr.strip() or e}[/yellow]")
        return False


def _auto_publish(draft: dict) -> str | None:
    """Publish directly to main branch (no PR). Used for ChangeImageTo auto-posting."""
    from storage.db import approve_draft

    repo_dir = Path(config.REPO_DIR).resolve() if config.REPO_DIR else Path.cwd()
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = draft.get("slug") or "post"

    approved = approve_draft(draft["id"])
    if not approved:
        approved = draft

    if config.PUBLISHER == "changeimageto":
        from publishers.changeimageto import publish_post
        path = publish_post(approved)
        files = [path, repo_dir / "frontend" / "blog" / "index.html", repo_dir / "frontend" / "sitemap.xml"]
        files = [f for f in files if f.exists()]
        PostIndex().add_post(
            slug=slug,
            title=approved.get("title", ""),
            keyword=approved.get("keyword", ""),
            tags=approved.get("tags", []),
            content=approved.get("content", ""),
            published_at=date_str,
        )
        if _publish_files(repo_dir, files, f"blog: {approved['title']}"):
            return str(path)
        return str(path) if path.exists() else None

    # Fallback: markdown output dir
    path_str = _save_output(approved)
    PostIndex().add_post(
        slug=slug,
        title=approved.get("title", ""),
        keyword=approved.get("keyword", ""),
        tags=approved.get("tags", []),
        content=approved.get("content", ""),
        published_at=date_str,
    )
    _publish_files(repo_dir, [Path(path_str)], f"blog: {approved['title']}")
    return path_str


def _create_pr(draft: dict) -> str | None:
    """Write a new article and open a GitHub PR."""
    repo_dir = Path(config.REPO_DIR).resolve() if config.REPO_DIR else Path.cwd()
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = draft.get("slug") or "post"

    PostIndex().add_post(
        slug=slug,
        title=draft.get("title", ""),
        keyword=draft.get("keyword", ""),
        tags=draft.get("tags", []),
        content=draft.get("content", ""),
        published_at=date_str,
    )

    if config.PUBLISHER == "changeimageto":
        from publishers.changeimageto import publish_post
        path = publish_post(draft)
        branch = f"blog/{date_str}-{slug}"
        files = [path, repo_dir / "frontend" / "blog" / "index.html", repo_dir / "frontend" / "sitemap.xml"]
        return _open_pr_multi(
            repo_dir=repo_dir,
            branch=branch,
            files=[f for f in files if f.exists()],
            commit_msg=f"blog: {draft['title']}",
            pr_title=draft["title"],
            pr_body=_build_pr_body(draft),
        )

    content_dir = repo_dir / config.CONTENT_DIR
    filepath = content_dir / f"{date_str}-{slug}.md"
    return _open_pr(
        repo_dir=repo_dir,
        branch=f"blog/{date_str}-{slug}",
        filepath=filepath,
        content=_build_markdown(draft),
        commit_msg=f"blog: {draft['title']}",
        pr_title=draft["title"],
        pr_body=_build_pr_body(draft),
    )


def _build_pr_body(draft: dict) -> str:
    tags = draft.get("tags", [])
    if isinstance(tags, str):
        tags = json.loads(tags)
    preview = (draft.get("content") or "")[:400].strip()

    edit_notes_section = ""
    if draft.get("edit_notes"):
        edit_notes_section = f"\n### Editor notes\n{draft['edit_notes']}\n"

    return f"""**Keyword:** `{draft.get('keyword', '')}`
**Slug:** `{draft.get('slug', '')}`
**Tags:** {', '.join(tags)}

**Meta description:**
> {draft.get('meta_description', '')}
{edit_notes_section}
---

### Article preview
{preview}…

---
*Generated by [Blogging Agent](https://github.com/vipulawl/blogging-agent) · Merge to publish · Close to reject*"""


def _build_markdown(draft: dict) -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = draft.get("slug") or "post"
    tags = draft.get("tags", [])
    if isinstance(tags, str):
        tags = json.loads(tags)
    tags_yaml = "[" + ", ".join(f'"{t}"' for t in tags) + "]"
    title = (draft.get("title") or "").replace('"', '\\"')
    description = (draft.get("meta_description") or "").replace('"', '\\"')
    keyword = (draft.get("keyword") or "").replace('"', '\\"')

    return f"""---
title: "{title}"
date: "{date_str}"
slug: "{slug}"
description: "{description}"
keyword: "{keyword}"
tags: {tags_yaml}
status: published
---

{draft['content']}"""


def _open_pr_multi(repo_dir: Path, branch: str, files: list[Path],
                   commit_msg: str, pr_title: str, pr_body: str) -> str | None:
    """Create branch, stage multiple files, push, open PR."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True, check=True)

    current = None
    try:
        current = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        git("checkout", "-b", branch)
        for filepath in files:
            git("add", str(filepath.relative_to(repo_dir)))
        git("commit", "-m", commit_msg)
        git("push", "-u", "origin", branch)

        result = subprocess.run(
            ["gh", "pr", "create", "--title", pr_title, "--body", pr_body, "--base", "main"],
            cwd=repo_dir, capture_output=True, text=True
        )
        pr_url = result.stdout.strip() if result.returncode == 0 else None
        git("checkout", current)
        return pr_url
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Git/PR step failed: {e.stderr.strip() or e}[/yellow]")
        if current:
            try:
                git("checkout", current)
            except Exception:
                pass
        return None


def _open_pr(repo_dir: Path, branch: str, filepath: Path, content: str,
             commit_msg: str, pr_title: str, pr_body: str) -> str | None:
    """
    Generic helper: create branch, write file, push, open PR, return to original branch.
    Used by both new articles and content refreshes.
    """
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True, check=True)

    current = None
    try:
        current = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        git("checkout", "-b", branch)

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")

        git("add", str(filepath))
        git("commit", "-m", commit_msg)
        git("push", "-u", "origin", branch)

        result = subprocess.run(
            ["gh", "pr", "create", "--title", pr_title, "--body", pr_body, "--base", "main"],
            cwd=repo_dir, capture_output=True, text=True
        )
        pr_url = result.stdout.strip() if result.returncode == 0 else None
        git("checkout", current)
        return pr_url

    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Git/PR step failed: {e.stderr.strip() or e}[/yellow]")
        if current:
            try:
                git("checkout", current)
            except Exception:
                pass
        return None


def _open_pr_for_refresh(r: dict) -> str | None:
    """Open a PR for a content refresh."""
    repo_dir = Path(config.REPO_DIR).resolve() if config.REPO_DIR else Path.cwd()
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = r.get("slug") or "refresh"
    branch = f"refresh/{date_str}-{slug}"

    score = r.get("refresh_score", 1)
    score_label = {1: "minor", 3: "moderate", 5: "major"}.get(score, str(score))
    pr_body = f"""**Keyword:** `{r.get('keyword', '')}`
**Refresh depth:** {score_label} (score {score}/5)

### What changed
{r.get('refresh_notes', '')}

---
*Content refresh by Blogging Agent · Merge to update · Close to skip*"""

    if config.PUBLISHER == "changeimageto":
        from publishers.changeimageto import refresh_post, find_blog_path_for_slug
        path = find_blog_path_for_slug(slug)
        draft = {
            "title": r.get("title", slug),
            "slug": slug,
            "meta_description": r.get("meta_description", ""),
            "content": r.get("refreshed_content", ""),
        }
        refresh_post(slug, draft, path)
        if config.APPROVAL_MODE == "auto":
            files = [path, repo_dir / "frontend" / "sitemap.xml"]
            if _publish_files(repo_dir, [f for f in files if f and f.exists()], f"refresh: {r.get('title', slug)}"):
                return str(path)
            return str(path) if path else None
        return _open_pr_multi(
            repo_dir=repo_dir,
            branch=branch,
            files=[f for f in [path, repo_dir / "frontend" / "sitemap.xml"] if f and f.exists()],
            commit_msg=f"refresh: {r.get('title', slug)}",
            pr_title=f"refresh: {r.get('title', slug)}",
            pr_body=pr_body,
        )

    # Rebuild the full markdown with updated content but same frontmatter structure
    md_text = Path(r["file_path"]).read_text(encoding="utf-8")
    fm, _ = _parse_frontmatter(md_text)
    fm["description"] = r.get("meta_description") or fm.get("description", "")

    # Reconstruct frontmatter lines
    fm_lines = "\n".join(f'{k}: "{v}"' for k, v in fm.items())
    updated_content = f"---\n{fm_lines}\n---\n\n{r['refreshed_content']}"

    return _open_pr(
        repo_dir=repo_dir,
        branch=branch,
        filepath=Path(r["file_path"]),
        content=updated_content,
        commit_msg=f"refresh: {r.get('title', slug)}",
        pr_title=f"refresh: {r.get('title', slug)}",
        pr_body=pr_body,
    )


def _save_output(draft: dict) -> str:
    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = draft.get("slug") or "post"
    filename = f"{date_str}-{slug}.md"

    tags = draft.get("tags", [])
    if isinstance(tags, str):
        tags = json.loads(tags)
    tags_yaml = "[" + ", ".join(f'"{t}"' for t in tags) + "]"

    title = (draft.get("title") or "").replace('"', '\\"')
    description = (draft.get("meta_description") or "").replace('"', '\\"')

    frontmatter = f"""---
title: "{title}"
date: "{date_str}"
slug: "{slug}"
description: "{description}"
tags: {tags_yaml}
status: published
---

"""

    filepath = output_dir / filename
    filepath.write_text(frontmatter + draft["content"])
    return str(filepath)
