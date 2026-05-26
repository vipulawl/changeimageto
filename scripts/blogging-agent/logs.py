from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.rule import Rule

from storage.db import (
    get_agent_runs, get_agent_run_by_id, get_tool_calls_for_run,
    get_agent_stats, get_tool_stats,
)

console = Console()

_AGENT_COLORS = {
    "research": "cyan", "writer": "blue", "editor": "magenta",
    "strategy": "yellow", "refresh": "orange3", "corrector": "red",
    "base": "white",
}


def _agent_color(name: str) -> str:
    return _AGENT_COLORS.get(name.lower(), "white")


def _status_text(status: str) -> Text:
    if status == "success":
        return Text("✓ success", style="green")
    if status == "failed":
        return Text("✗ failed", style="bold red")
    if status == "running":
        return Text("⟳ running", style="yellow")
    return Text(status, style="dim")


def _fmt_duration(seconds) -> str:
    if seconds is None:
        return "—"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def _fmt_ts(ts: str) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts[:19]


def show_recent(limit: int = 25, agent_name: str = None,
                failed_only: bool = False) -> None:
    status_filter = "failed" if failed_only else None
    runs = get_agent_runs(limit=limit, agent_name=agent_name, status=status_filter)

    title = "Recent Agent Runs"
    if agent_name:
        title += f" — {agent_name}"
    if failed_only:
        title += " (failures only)"

    table = Table(title=title, show_lines=False, border_style="dim")
    table.add_column("ID", style="dim", width=6, justify="right")
    table.add_column("Agent", width=12)
    table.add_column("Started", width=20)
    table.add_column("Duration", width=10, justify="right")
    table.add_column("Steps", width=6, justify="right")
    table.add_column("Tokens in/out", width=16, justify="right")
    table.add_column("Topic", max_width=30, no_wrap=True)
    table.add_column("Status", width=16)
    table.add_column("Trigger", width=8, style="dim")

    for r in runs:
        color = _agent_color(r["agent_name"])
        tokens = (
            f"{r['tokens_input']:,}/{r['tokens_output']:,}"
            if r["tokens_input"] or r["tokens_output"]
            else "—"
        )
        table.add_row(
            str(r["id"]),
            f"[{color}]{r['agent_name']}[/{color}]",
            _fmt_ts(r["started_at"]),
            _fmt_duration(r["duration_seconds"]),
            str(r["iterations"] or "—"),
            tokens,
            (r.get("topic_title") or "")[:30],
            _status_text(r["status"]),
            r.get("trigger", "manual"),
        )

    console.print()
    console.print(table)

    total = len(runs)
    successes = sum(1 for r in runs if r["status"] == "success")
    failures = sum(1 for r in runs if r["status"] == "failed")
    pct = f"{successes / total * 100:.0f}%" if total else "—"
    console.print(
        f"\n[dim]Showing {total} run(s)  |  "
        f"[green]{successes} success ({pct})[/green]  |  "
        f"[{'red' if failures else 'dim'}]{failures} failed[/{'red' if failures else 'dim'}][/dim]"
    )
    if runs:
        console.print("[dim]For detail on a run: python main.py logs --run <ID>[/dim]\n")


def show_run_detail(run_id_or_db_id: str) -> None:
    run = get_agent_run_by_id(run_id_or_db_id)
    if not run:
        # Try by integer DB id
        runs = get_agent_runs(limit=1000)
        run = next((r for r in runs if str(r["id"]) == str(run_id_or_db_id)), None)
    if not run:
        console.print(f"[red]Run not found: {run_id_or_db_id}[/red]")
        return

    color = _agent_color(run["agent_name"])
    status_t = _status_text(run["status"])

    console.print()
    console.rule(
        f"[bold]Run #{run['id']} — [{color}]{run['agent_name']}[/{color}]  "
        f"{status_t}[/bold]"
    )

    meta_lines = [
        f"[dim]Started:[/dim]   {_fmt_ts(run['started_at'])}",
        f"[dim]Finished:[/dim]  {_fmt_ts(run.get('finished_at', ''))}",
        f"[dim]Duration:[/dim]  {_fmt_duration(run.get('duration_seconds'))}",
        f"[dim]Iterations:[/dim] {run.get('iterations', 0)}",
        f"[dim]Trigger:[/dim]   {run.get('trigger', 'manual')}",
    ]
    if run.get("topic_title"):
        meta_lines.append(f"[dim]Topic:[/dim]     {run['topic_title']}")
    if run.get("tokens_input") or run.get("tokens_output"):
        meta_lines.append(
            f"[dim]Tokens:[/dim]    {run.get('tokens_input', 0):,} input / "
            f"{run.get('tokens_output', 0):,} output"
        )
    if run.get("error_message"):
        meta_lines.append(f"[red]Error:[/red]     {run['error_message']}")

    for line in meta_lines:
        console.print(f"  {line}")

    tool_calls = get_tool_calls_for_run(run["run_id"])
    if not tool_calls:
        console.print("\n  [dim](no tool calls recorded)[/dim]\n")
        return

    console.print()
    table = Table(title="Tool Call Trace", show_lines=False, border_style="dim")
    table.add_column("#", width=4, justify="right", style="dim")
    table.add_column("Tool", width=28)
    table.add_column("Time", width=8, justify="right")
    table.add_column("Result", min_width=40)
    table.add_column("Inputs", max_width=50, no_wrap=True, style="dim")

    for tc in tool_calls:
        status_icon = "[green]✓[/green]" if tc["success"] else "[red]✗[/red]"
        result_text = tc.get("result_preview") or ""
        if tc.get("error_message") and not tc["success"]:
            result_text = f"[red]{tc['error_message'][:80]}[/red]"
        duration = (
            f"{tc['duration_ms'] / 1000:.2f}s"
            if tc.get("duration_ms") is not None
            else "—"
        )

        import json as _json
        inputs_preview = ""
        try:
            inp = _json.loads(tc.get("inputs_json") or "{}")
            inputs_preview = ", ".join(
                f"{k}={repr(str(v))[:30]}"
                for k, v in list(inp.items())[:3]
            )
        except Exception:
            pass

        table.add_row(
            f"{status_icon} {tc['seq_num']}",
            tc["tool_name"],
            duration,
            result_text[:100],
            inputs_preview[:80],
        )

    console.print(table)
    console.print()


def show_stats() -> None:
    agent_stats = get_agent_stats()
    tool_stats = get_tool_stats(limit=12)

    console.print()
    console.rule("[bold]Agent Performance Stats[/bold]")
    console.print()

    if not agent_stats:
        console.print("[yellow]No completed runs yet.[/yellow]")
        return

    agent_table = Table(show_lines=False, border_style="dim")
    agent_table.add_column("Agent", width=14)
    agent_table.add_column("Runs", width=6, justify="right")
    agent_table.add_column("Success", width=8, justify="right")
    agent_table.add_column("Failed", width=7, justify="right")
    agent_table.add_column("Fail %", width=7, justify="right")
    agent_table.add_column("Avg Duration", width=14, justify="right")
    agent_table.add_column("Avg Steps", width=10, justify="right")
    agent_table.add_column("Total Tokens", width=14, justify="right")

    for s in agent_stats:
        color = _agent_color(s["agent_name"])
        total = s["total_runs"]
        fail_pct = (s["failures"] / total * 100) if total else 0
        fail_color = "red" if fail_pct > 10 else ("yellow" if fail_pct > 0 else "green")
        total_tokens = (s.get("total_tokens_input", 0) or 0) + (s.get("total_tokens_output", 0) or 0)
        agent_table.add_row(
            f"[{color}]{s['agent_name']}[/{color}]",
            str(total),
            f"[green]{s['successes']}[/green]",
            f"[{fail_color}]{s['failures']}[/{fail_color}]",
            f"[{fail_color}]{fail_pct:.0f}%[/{fail_color}]",
            _fmt_duration(s.get("avg_duration_seconds")),
            f"{s.get('avg_iterations', 0) or 0:.1f}",
            f"{total_tokens:,}" if total_tokens else "—",
        )

    console.print(agent_table)

    if tool_stats:
        console.print()
        console.rule("[bold]Most Used Tools[/bold]")
        console.print()

        tool_table = Table(show_lines=False, border_style="dim")
        tool_table.add_column("Tool", width=30)
        tool_table.add_column("Calls", width=7, justify="right")
        tool_table.add_column("Errors", width=7, justify="right")
        tool_table.add_column("Error %", width=8, justify="right")
        tool_table.add_column("Avg Time", width=10, justify="right")

        for t in tool_stats:
            total_calls = t["total_calls"]
            err_pct = (t["errors"] / total_calls * 100) if total_calls else 0
            err_color = "red" if err_pct > 10 else ("yellow" if err_pct > 0 else "dim")
            avg_ms = t.get("avg_duration_ms") or 0
            avg_str = f"{avg_ms / 1000:.2f}s" if avg_ms >= 100 else f"{int(avg_ms)}ms"
            tool_table.add_row(
                t["tool_name"],
                str(total_calls),
                f"[{err_color}]{t['errors']}[/{err_color}]",
                f"[{err_color}]{err_pct:.0f}%[/{err_color}]",
                avg_str,
            )

        console.print(tool_table)

    console.print()
