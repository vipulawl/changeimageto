import argparse
import sys

from rich.console import Console
from rich.table import Table

from storage.db import init_db

console = Console()


def cmd_research(_args):
    from orchestrator import run_research
    run_research()


def cmd_write(args):
    from orchestrator import run_write
    run_write(topic_id=getattr(args, "topic_id", None))


def cmd_review(_args):
    from orchestrator import run_review
    run_review()


def cmd_pipeline(_args):
    from orchestrator import run_pipeline
    run_pipeline()


def cmd_strategy(args):
    from orchestrator import run_strategy
    run_strategy(force=getattr(args, "force", False), auto=getattr(args, "auto", False))


def cmd_refresh(args):
    from orchestrator import run_refresh
    run_refresh(max_articles=getattr(args, "max_articles", 2))


def cmd_show_strategy(_args):
    from storage.db import get_active_strategy
    from orchestrator import _display_strategy
    strategy = get_active_strategy()
    if not strategy:
        console.print("[yellow]No active strategy. Run: python main.py strategy[/yellow]")
        return
    console.print(f"[dim]Created: {strategy['created_at']}[/dim]")
    _display_strategy(strategy)


def cmd_list_topics(_args):
    from storage.db import get_all_topics
    topics = get_all_topics()
    if not topics:
        console.print("[yellow]No topics yet. Run: python main.py research[/yellow]")
        return

    table = Table(title="Topics", show_lines=False)
    table.add_column("ID", style="dim", width=4)
    table.add_column("Title", max_width=55)
    table.add_column("Keyword", max_width=25)
    table.add_column("Source", width=10)
    table.add_column("Pri", width=4)
    table.add_column("Status", width=14)

    status_colors = {
        "queued": "cyan", "writing": "yellow", "editing": "yellow",
        "pending_approval": "green", "published": "blue",
        "rejected": "red", "approved": "blue",
    }
    for t in topics:
        color = status_colors.get(t["status"], "white")
        table.add_row(
            str(t["id"]),
            t["title"][:55],
            t["keyword"][:25],
            t["source"],
            f"{t['priority_score']:.1f}",
            f"[{color}]{t['status']}[/{color}]",
        )
    console.print(table)


def cmd_list_drafts(_args):
    from storage.db import get_pending_drafts
    drafts = get_pending_drafts()
    if not drafts:
        console.print("[yellow]No drafts pending review.[/yellow]")
        return

    table = Table(title="Pending Drafts")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Title", max_width=55)
    table.add_column("Keyword", max_width=25)
    table.add_column("Status", width=10)

    for d in drafts:
        table.add_row(str(d["id"]), d["title"][:55], d["keyword"][:25], d["status"])
    console.print(table)


def cmd_schedule(args):
    from orchestrator import run_schedule
    run_schedule(
        dry_run=getattr(args, "dry_run", False),
        as_json=getattr(args, "json", False),
    )


def cmd_monitor(_args):
    from orchestrator import run_monitor
    run_monitor()


def cmd_correct(_args):
    from orchestrator import run_correction
    run_correction()


def cmd_dedup(args):
    from dedup import DedupChecker
    keyword = getattr(args, "keyword", "") or ""
    title = getattr(args, "title", "") or keyword
    if not keyword and not title:
        console.print("[yellow]Provide --keyword or --title[/yellow]")
        return
    is_dup, reason, match = DedupChecker().check(title, keyword)
    status = "[red]DUPLICATE[/red]" if is_dup else "[green]UNIQUE[/green]"
    console.print(f"\nResult: {status}")
    console.print(f"Reason: {reason}")
    if match:
        console.print(f"Nearest: {match.get('title')} (/{match.get('slug')})")


def cmd_logs(args):
    import logs as log_module
    run_id = getattr(args, "run", None)
    stats = getattr(args, "stats", False)
    failed = getattr(args, "failed", False)
    agent = getattr(args, "agent", None)
    limit = getattr(args, "limit", 25)

    if run_id:
        log_module.show_run_detail(run_id)
    elif stats:
        log_module.show_stats()
    else:
        log_module.show_recent(limit=limit, agent_name=agent, failed_only=failed)


def main():
    init_db()

    parser = argparse.ArgumentParser(
        description="Blogging Agent — AI-powered content pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
commands:
  strategy      Interactive strategy setup (interview + competitor research) — run first
  show-strategy Show the active content strategy
  research      Run research agent — finds 3-5 topics guided by your strategy
  write         Write + edit the highest-priority queued topic
  review        Review pending articles and approve/reject
  pipeline      research (if queue empty) → write → review
  schedule      Evaluate scheduling signals → publish/wait/research/requeue
  monitor       Snapshot GSC+GA4 performance for all published posts
  correct       Review underperformers and apply corrections
  dedup         Check a keyword/title for duplicates in post memory
  logs          View agent run logs and performance dashboard
  list-topics   Show all topics and their status
  list-drafts   Show drafts awaiting your approval
        """,
    )
    subparsers = parser.add_subparsers(dest="command")

    strategy_p = subparsers.add_parser("strategy", help="Build/update content strategy (interactive or --auto)")
    strategy_p.add_argument("--force", action="store_true", help="Replace existing strategy without asking")
    strategy_p.add_argument("--auto", action="store_true", help="CI mode: read inputs from STRATEGY_* env vars")

    subparsers.add_parser("show-strategy", help="Show active strategy")
    subparsers.add_parser("research", help="Research new topics")

    write_p = subparsers.add_parser("write", help="Write + edit an article")
    write_p.add_argument("--topic-id", type=int, dest="topic_id", help="Specific topic ID (default: highest priority)")

    subparsers.add_parser("review", help="Review pending articles")
    subparsers.add_parser("pipeline", help="Full pipeline: research + write + PR")

    refresh_p = subparsers.add_parser("refresh", help="Refresh stale published articles")
    refresh_p.add_argument("--max", type=int, dest="max_articles", default=2, help="Max articles to refresh per run")

    subparsers.add_parser("list-topics", help="List all topics")
    subparsers.add_parser("list-drafts", help="List pending drafts")

    schedule_p = subparsers.add_parser("schedule", help="Evaluate scheduling signals and decide next action")
    schedule_p.add_argument("--dry-run", action="store_true", dest="dry_run", help="Print decision without saving")
    schedule_p.add_argument("--json", action="store_true", dest="json", help="Output decision as JSON (for GitHub Actions)")

    subparsers.add_parser("monitor", help="Snapshot GSC/GA4 performance for all posts")
    subparsers.add_parser("correct", help="Review flagged underperformers and apply corrections")

    dedup_p = subparsers.add_parser("dedup", help="Check a keyword/title for duplicates")
    dedup_p.add_argument("--keyword", type=str, default="", help="Keyword to check")
    dedup_p.add_argument("--title", type=str, default="", help="Title to check")

    logs_p = subparsers.add_parser("logs", help="View agent run logs and performance dashboard")
    logs_p.add_argument("--run", type=str, default=None, metavar="ID", help="Show full trace for a specific run ID")
    logs_p.add_argument("--stats", action="store_true", help="Show aggregate stats per agent and tool")
    logs_p.add_argument("--failed", action="store_true", help="Show only failed runs")
    logs_p.add_argument("--agent", type=str, default=None, help="Filter by agent name (e.g. research, writer)")
    logs_p.add_argument("--limit", type=int, default=25, help="Number of runs to show (default: 25)")

    args = parser.parse_args()

    commands = {
        "strategy": cmd_strategy,
        "show-strategy": cmd_show_strategy,
        "research": cmd_research,
        "write": cmd_write,
        "review": cmd_review,
        "pipeline": cmd_pipeline,
        "refresh": cmd_refresh,
        "list-topics": cmd_list_topics,
        "list-drafts": cmd_list_drafts,
        "schedule": cmd_schedule,
        "monitor": cmd_monitor,
        "correct": cmd_correct,
        "dedup": cmd_dedup,
        "logs": cmd_logs,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
