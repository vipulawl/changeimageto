from datetime import datetime

import config
from storage.db import (
    get_all_post_memory, save_performance_snapshot, get_latest_snapshots,
)
from tools.gsc import get_page_performance
from tools.ga4 import get_top_pages


def _compute_health(gsc_clicks: int, gsc_impressions: int, gsc_position: float,
                    gsc_ctr: float, ga4_sessions: int, age_days: int) -> int:
    score = 50

    if gsc_position > 0:
        if gsc_position <= 5:
            score += 30
        elif gsc_position <= 10:
            score += 20
        elif gsc_position <= 20:
            score += 5
        else:
            score -= 20

    if gsc_ctr >= 5.0:
        score += 15

    if ga4_sessions >= 100:
        score += 15

    if gsc_impressions < 10 and age_days > 30:
        score -= 15

    return max(0, min(100, score))


def _flag_post(age_days: int, gsc_position: float, gsc_impressions: int,
               gsc_clicks: int) -> str:
    flags = []
    if age_days >= 30 and gsc_position > 20 and gsc_impressions > 50:
        flags.append("low_ranking")
    if age_days >= 45 and gsc_clicks < 5:
        flags.append("low_traffic")
    return ",".join(flags)


def run_monitor() -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("\n[bold blue]Performance Monitor[/bold blue] — querying GSC + GA4...")

    posts = get_all_post_memory()
    if not posts:
        console.print("[yellow]No posts in memory index. Approve articles to populate it.[/yellow]")
        return

    gsc_pages = get_page_performance(days=90)
    ga4_pages = get_top_pages(days=90, limit=200)

    gsc_by_slug: dict[str, dict] = {}
    for row in gsc_pages:
        if "error" in row:
            continue
        page = row.get("page", "")
        slug = page.rstrip("/").rsplit("/", 1)[-1]
        if slug.endswith(".html"):
            slug = slug[:-5]
        if slug:
            gsc_by_slug[slug] = row

    ga4_by_slug: dict[str, int] = {}
    for row in ga4_pages:
        if "error" in row:
            continue
        path = row.get("page_path", "").rstrip("/")
        slug = path.rsplit("/", 1)[-1]
        if slug.endswith(".html"):
            slug = slug[:-5]
        if slug:
            ga4_by_slug[slug] = row.get("sessions", 0)

    today = datetime.now().isoformat()[:10]
    snapshot_count = 0
    flagged_count = 0

    table = Table(title="Performance Snapshot", show_lines=False)
    table.add_column("Slug", max_width=30)
    table.add_column("Age", width=6, justify="right")
    table.add_column("Pos", width=6, justify="right")
    table.add_column("Imp", width=6, justify="right")
    table.add_column("Clicks", width=6, justify="right")
    table.add_column("CTR%", width=6, justify="right")
    table.add_column("Sessions", width=8, justify="right")
    table.add_column("Health", width=7, justify="right")
    table.add_column("Flag", width=14)

    for post in posts:
        slug = post["slug"]
        published_at = post.get("published_at", today)
        try:
            age_days = (datetime.now() - datetime.fromisoformat(published_at[:10])).days
        except Exception:
            age_days = 0

        gsc = gsc_by_slug.get(slug, {})
        gsc_clicks = gsc.get("clicks", 0)
        gsc_impressions = gsc.get("impressions", 0)
        gsc_position = gsc.get("position", 0.0)
        gsc_ctr = gsc.get("ctr", 0.0)
        ga4_sessions = ga4_by_slug.get(slug, 0)

        health = _compute_health(gsc_clicks, gsc_impressions, gsc_position,
                                 gsc_ctr, ga4_sessions, age_days)
        flag = _flag_post(age_days, gsc_position, gsc_impressions, gsc_clicks)

        save_performance_snapshot(
            slug=slug,
            keyword=post.get("keyword", ""),
            gsc_clicks=gsc_clicks,
            gsc_impressions=gsc_impressions,
            gsc_position=gsc_position,
            gsc_ctr=gsc_ctr,
            ga4_sessions=ga4_sessions,
            health_score=health,
            flag=flag,
        )
        snapshot_count += 1
        if flag:
            flagged_count += 1

        pos_str = f"{gsc_position:.1f}" if gsc_position > 0 else "—"
        health_color = "green" if health >= 70 else ("yellow" if health >= 40 else "red")
        flag_color = "red" if flag else "dim"

        table.add_row(
            slug[:30],
            str(age_days),
            pos_str,
            str(gsc_impressions),
            str(gsc_clicks),
            f"{gsc_ctr:.1f}",
            str(ga4_sessions),
            f"[{health_color}]{health}[/{health_color}]",
            f"[{flag_color}]{flag or '—'}[/{flag_color}]",
        )

    console.print(table)
    console.print(
        f"\n[green]Snapshotted {snapshot_count} post(s).[/green] "
        f"{'[red]' if flagged_count else '[dim]'}"
        f"{flagged_count} flagged for correction."
        f"{'[/red]' if flagged_count else '[/dim]'}"
    )
    if flagged_count:
        console.print("[dim]Run: python main.py correct[/dim]")
