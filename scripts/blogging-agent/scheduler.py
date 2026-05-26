import json
from datetime import datetime

import config
from storage.db import (
    get_all_topics, get_published_today_count, get_published_count_last_n_days,
    get_post_memory_count_last_n_days, save_scheduler_decision,
)
from dedup import DedupChecker
from tools.gsc import get_top_queries


def _gsc_opportunity_score(keyword: str, gsc_data: list[dict]) -> float:
    """Score 0-1: high impressions + poor position = high opportunity."""
    kw_lower = keyword.lower()
    for row in gsc_data:
        if kw_lower in row.get("query", "").lower():
            impressions = row.get("impressions", 0)
            position = row.get("position", 100)
            imp_score = min(impressions / 500, 1.0)
            pos_score = max(0, (50 - position) / 50)
            return (imp_score + pos_score) / 2
    return 0.0


def _score_topic(topic: dict, gsc_data: list[dict], dedup: DedupChecker) -> float:
    created_at = topic.get("created_at", "")
    try:
        days_waiting = (datetime.now() - datetime.fromisoformat(created_at)).days
    except Exception:
        days_waiting = 0

    wait_score = min(days_waiting / 7, 1.0)
    gsc_score = _gsc_opportunity_score(topic["keyword"], gsc_data)
    priority = topic.get("priority_score", 0.5)
    dedup_penalty = dedup.score_penalty(topic["title"], topic["keyword"])

    return wait_score * 0.3 + gsc_score * 0.3 + priority * 0.4 - dedup_penalty


def evaluate(dry_run: bool = False, as_json: bool = False) -> dict:
    queued = get_all_topics(status="queued")
    published_today = get_published_today_count()
    published_7d = get_published_count_last_n_days(7)
    in_ramp = get_post_memory_count_last_n_days(30)

    gsc_data = []
    try:
        gsc_data = get_top_queries(days=28) or []
    except Exception:
        pass

    dedup = DedupChecker()

    # Signal checks in priority order
    if published_today >= 1:
        result = {
            "decision": "wait",
            "reason": f"Already published {published_today} post(s) today — max 1 per day",
            "topic_id": None,
            "score": 0.0,
        }
    elif len(queued) < 2:
        result = {
            "decision": "research",
            "reason": f"Queue has only {len(queued)} topic(s) — need at least 2 before writing",
            "topic_id": None,
            "score": 0.0,
        }
    elif in_ramp > config.MAX_POSTS_IN_RAMP:
        result = {
            "decision": "wait",
            "reason": f"{in_ramp} posts in ramp (< 30 days old) — waiting for them to gain traction before adding more",
            "topic_id": None,
            "score": 0.0,
        }
    else:
        scored = [(t, _score_topic(t, gsc_data, dedup)) for t in queued]
        scored.sort(key=lambda x: x[1], reverse=True)
        best_topic, best_score = scored[0]

        result = {
            "decision": "publish",
            "reason": (
                f"Queue has {len(queued)} topics, {published_7d} published in last 7 days, "
                f"{in_ramp} in ramp. Best topic: '{best_topic['title']}' (score {best_score:.2f})"
            ),
            "topic_id": best_topic["id"],
            "score": round(best_score, 4),
        }

    if not dry_run:
        save_scheduler_decision(
            decision=result["decision"],
            reason=result["reason"],
            topic_id=result.get("topic_id"),
            score=result.get("score", 0.0),
        )

    if as_json:
        print(json.dumps(result))
    else:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        color = {"publish": "green", "research": "cyan", "wait": "yellow", "requeue": "magenta"}.get(
            result["decision"], "white"
        )
        label = f"[{color}]{result['decision'].upper()}[/{color}]"
        dry_label = " [dim](dry-run — not saved)[/dim]" if dry_run else ""
        console.print(Panel(
            f"Decision: {label}{dry_label}\nReason:   {result['reason']}\n"
            f"Topic ID: {result.get('topic_id') or 'N/A'}  Score: {result.get('score', 0):.4f}",
            title="[bold]Scheduler[/bold]",
            border_style=color,
        ))

    return result
