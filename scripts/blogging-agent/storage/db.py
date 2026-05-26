import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path("blogging_agent.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                keyword TEXT NOT NULL,
                research_brief TEXT,
                source TEXT DEFAULT 'web_search',
                priority_score REAL DEFAULT 0.5,
                status TEXT DEFAULT 'queued',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER REFERENCES topics(id),
                title TEXT,
                slug TEXT,
                meta_description TEXT,
                tags TEXT DEFAULT '[]',
                content TEXT,
                version INTEGER DEFAULT 1,
                edit_notes TEXT,
                status TEXT DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS strategy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_pillars TEXT DEFAULT '[]',
                competitors TEXT DEFAULT '[]',
                content_gaps TEXT DEFAULT '[]',
                quick_wins TEXT DEFAULT '[]',
                avoid_topics TEXT DEFAULT '[]',
                strategic_summary TEXT,
                interview_data TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS competitor_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competitor_url TEXT NOT NULL,
                post_url TEXT NOT NULL UNIQUE,
                post_title TEXT,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS refreshes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                title TEXT,
                keyword TEXT,
                slug TEXT,
                original_content TEXT,
                refreshed_content TEXT,
                meta_description TEXT,
                refresh_notes TEXT,
                refresh_score INTEGER DEFAULT 1,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scheduler_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                decision TEXT NOT NULL,
                reason TEXT,
                topic_id INTEGER,
                score REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS post_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                title TEXT,
                keyword TEXT,
                tags TEXT DEFAULT '[]',
                summary TEXT,
                semantic_fingerprint TEXT,
                published_at TEXT,
                word_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS performance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                keyword TEXT,
                snapshot_date TEXT NOT NULL,
                gsc_clicks INTEGER DEFAULT 0,
                gsc_impressions INTEGER DEFAULT 0,
                gsc_position REAL DEFAULT 0.0,
                gsc_ctr REAL DEFAULT 0.0,
                ga4_sessions INTEGER DEFAULT 0,
                health_score INTEGER DEFAULT 50,
                flag TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS correction_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                flagged_at TEXT,
                action TEXT,
                reason TEXT,
                executed_at TEXT,
                check_after TEXT
            );

            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT UNIQUE NOT NULL,
                agent_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_seconds REAL,
                status TEXT DEFAULT 'running',
                error_message TEXT,
                iterations INTEGER DEFAULT 0,
                tokens_input INTEGER DEFAULT 0,
                tokens_output INTEGER DEFAULT 0,
                topic_id INTEGER,
                topic_title TEXT,
                trigger TEXT DEFAULT 'manual'
            );

            CREATE TABLE IF NOT EXISTS agent_tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                seq_num INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                inputs_json TEXT,
                result_json TEXT,
                result_preview TEXT,
                success INTEGER DEFAULT 1,
                error_message TEXT,
                started_at TEXT,
                duration_ms INTEGER DEFAULT 0
            );
        """)


def save_topic(title: str, keyword: str, research_brief: str, source: str = "web_search", priority_score: float = 0.5) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO topics (title, keyword, research_brief, source, priority_score) VALUES (?, ?, ?, ?, ?)",
            (title, keyword, research_brief, source, priority_score)
        )
        return cursor.lastrowid


def get_next_topic() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM topics WHERE status = 'queued' ORDER BY priority_score DESC, created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE topics SET status = 'writing' WHERE id = ?", (row["id"],))
        return dict(row)


def get_topic_by_id(topic_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        return dict(row) if row else None


def get_all_topics(status: str = None) -> list[dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM topics WHERE status = ? ORDER BY priority_score DESC, created_at DESC",
                (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM topics ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def save_draft(topic_id: int, title: str, slug: str, meta_description: str, tags: list, content: str) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO drafts (topic_id, title, slug, meta_description, tags, content) VALUES (?, ?, ?, ?, ?, ?)",
            (topic_id, title, slug, meta_description, json.dumps(tags), content)
        )
        conn.execute("UPDATE topics SET status = 'editing' WHERE id = ?", (topic_id,))
        return cursor.lastrowid


def get_latest_draft_for_topic(topic_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE topic_id = ? ORDER BY created_at DESC LIMIT 1",
            (topic_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = json.loads(d["tags"] or "[]")
        return d


def save_edited_draft(draft_id: int, content: str, edit_notes: str, title: str = None, meta_description: str = None) -> None:
    with get_conn() as conn:
        draft = conn.execute("SELECT topic_id FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if not draft:
            return

        fields = {"content": content, "edit_notes": edit_notes, "version": 2, "status": "edited",
                  "updated_at": datetime.now().isoformat()}
        if title:
            fields["title"] = title
        if meta_description:
            fields["meta_description"] = meta_description

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE drafts SET {set_clause} WHERE id = ?", (*fields.values(), draft_id))
        conn.execute("UPDATE topics SET status = 'pending_approval' WHERE id = ?", (draft["topic_id"],))


def get_pending_drafts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT d.*, t.keyword
            FROM drafts d
            JOIN topics t ON d.topic_id = t.id
            WHERE d.status = 'edited'
            ORDER BY d.updated_at DESC
        """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d["tags"] or "[]")
            result.append(d)
        return result


def approve_draft(draft_id: int) -> dict:
    with get_conn() as conn:
        conn.execute("UPDATE drafts SET status = 'approved' WHERE id = ?", (draft_id,))
        row = conn.execute("""
            SELECT d.*, t.keyword
            FROM drafts d JOIN topics t ON d.topic_id = t.id
            WHERE d.id = ?
        """, (draft_id,)).fetchone()
        conn.execute("UPDATE topics SET status = 'published' WHERE id = ?", (row["topic_id"],))
        d = dict(row)
        d["tags"] = json.loads(d["tags"] or "[]")
        return d


def reject_draft(draft_id: int) -> None:
    with get_conn() as conn:
        draft = conn.execute("SELECT topic_id FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        conn.execute("UPDATE drafts SET status = 'rejected' WHERE id = ?", (draft_id,))
        if draft:
            conn.execute("UPDATE topics SET status = 'rejected' WHERE id = ?", (draft["topic_id"],))


# ── Strategy ──────────────────────────────────────────────────────────────────

def save_strategy(data: dict, interview: dict = None) -> int:
    with get_conn() as conn:
        conn.execute("UPDATE strategy SET is_active = 0")
        cursor = conn.execute(
            """INSERT INTO strategy
               (content_pillars, competitors, content_gaps, quick_wins, avoid_topics, strategic_summary, interview_data)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                json.dumps(data.get("content_pillars", [])),
                json.dumps(data.get("competitors", [])),
                json.dumps(data.get("content_gaps", [])),
                json.dumps(data.get("quick_wins", [])),
                json.dumps(data.get("avoid_topics", [])),
                data.get("strategic_summary", ""),
                json.dumps(interview or {}),
            ),
        )
        return cursor.lastrowid


def get_active_strategy() -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM strategy WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        d = dict(row)
        for key in ("content_pillars", "competitors", "content_gaps", "quick_wins", "avoid_topics", "interview_data"):
            d[key] = json.loads(d[key] or "[]")
        return d


# ── Competitor post tracking ───────────────────────────────────────────────────

def get_known_post_urls(competitor_url: str) -> set[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT post_url FROM competitor_posts WHERE competitor_url = ?", (competitor_url,)
        ).fetchall()
        return {r["post_url"] for r in rows}


def save_competitor_posts(competitor_url: str, posts: list[dict]) -> list[dict]:
    """Save new posts; return only the ones not seen before."""
    known = get_known_post_urls(competitor_url)
    new_posts = [p for p in posts if p.get("url") and p["url"] not in known]
    with get_conn() as conn:
        for p in new_posts:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO competitor_posts (competitor_url, post_url, post_title) VALUES (?, ?, ?)",
                    (competitor_url, p["url"], p.get("title", "")),
                )
            except Exception:
                pass
    return new_posts


# ── Refresh tracking ──────────────────────────────────────────────────────────

def save_refresh(file_path: str, title: str, keyword: str, slug: str,
                 original_content: str, refreshed_content: str,
                 meta_description: str, refresh_notes: str, refresh_score: int) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO refreshes
               (file_path, title, keyword, slug, original_content, refreshed_content,
                meta_description, refresh_notes, refresh_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_path, title, keyword, slug, original_content, refreshed_content,
             meta_description, refresh_notes, refresh_score),
        )
        return cursor.lastrowid


def get_pending_refreshes() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM refreshes WHERE status = 'pending' ORDER BY refresh_score DESC, created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_refresh_done(refresh_id: int, status: str = "pr_created") -> None:
    with get_conn() as conn:
        conn.execute("UPDATE refreshes SET status = ? WHERE id = ?", (status, refresh_id))


def was_recently_refreshed(file_path: str, within_days: int = 60) -> bool:
    """Prevent refreshing the same article too often."""
    with get_conn() as conn:
        cutoff = datetime.now().isoformat()[:10]
        row = conn.execute(
            """SELECT id FROM refreshes
               WHERE file_path = ? AND status != 'rejected'
               AND date(created_at) >= date(?, ?)
               LIMIT 1""",
            (file_path, cutoff, f"-{within_days} days"),
        ).fetchone()
        return row is not None


# ── Scheduler decisions ───────────────────────────────────────────────────────

def save_scheduler_decision(decision: str, reason: str, topic_id: int = None, score: float = 0.0) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO scheduler_decisions (decision, reason, topic_id, score) VALUES (?, ?, ?, ?)",
            (decision, reason, topic_id, score),
        )
        return cursor.lastrowid


def get_latest_scheduler_decision() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduler_decisions ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_published_count_last_n_days(days: int = 7) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM topics WHERE status = 'published' AND created_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchone()
        return row["cnt"] if row else 0


def get_published_today_count() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM topics WHERE status = 'published' AND date(created_at) = date('now')"
        ).fetchone()
        return row["cnt"] if row else 0


# ── Post memory ───────────────────────────────────────────────────────────────

def save_post_memory(slug: str, title: str, keyword: str, tags: list,
                     summary: str, semantic_fingerprint: str,
                     published_at: str = None, word_count: int = 0) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT OR REPLACE INTO post_memory
               (slug, title, keyword, tags, summary, semantic_fingerprint, published_at, word_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (slug, title, keyword, json.dumps(tags), summary, semantic_fingerprint,
             published_at or datetime.now().isoformat()[:10], word_count),
        )
        return cursor.lastrowid


def get_all_post_memory() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM post_memory ORDER BY published_at DESC").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d["tags"] or "[]")
            result.append(d)
        return result


def get_post_memory_count_last_n_days(days: int = 30) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM post_memory WHERE published_at >= date('now', ?)",
            (f"-{days} days",),
        ).fetchone()
        return row["cnt"] if row else 0


# ── Performance snapshots ─────────────────────────────────────────────────────

def save_performance_snapshot(slug: str, keyword: str, gsc_clicks: int,
                               gsc_impressions: int, gsc_position: float,
                               gsc_ctr: float, ga4_sessions: int,
                               health_score: int, flag: str = "") -> int:
    snapshot_date = datetime.now().isoformat()[:10]
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO performance_snapshots
               (slug, keyword, snapshot_date, gsc_clicks, gsc_impressions,
                gsc_position, gsc_ctr, ga4_sessions, health_score, flag)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slug, keyword, snapshot_date, gsc_clicks, gsc_impressions,
             gsc_position, gsc_ctr, ga4_sessions, health_score, flag),
        )
        return cursor.lastrowid


def get_flagged_posts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT s.* FROM performance_snapshots s
               INNER JOIN (
                 SELECT slug, MAX(snapshot_date) as max_date
                 FROM performance_snapshots GROUP BY slug
               ) latest ON s.slug = latest.slug AND s.snapshot_date = latest.max_date
               WHERE s.flag != ''
               ORDER BY s.health_score ASC""",
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_snapshots() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.* FROM performance_snapshots s
               INNER JOIN (
                 SELECT slug, MAX(snapshot_date) as max_date
                 FROM performance_snapshots GROUP BY slug
               ) latest ON s.slug = latest.slug AND s.snapshot_date = latest.max_date
               ORDER BY s.health_score DESC""",
        ).fetchall()
        return [dict(r) for r in rows]


# ── Correction log ────────────────────────────────────────────────────────────

def save_correction_log(slug: str, action: str, reason: str, check_after: str = None) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO correction_log (slug, flagged_at, action, reason, check_after)
               VALUES (?, ?, ?, ?, ?)""",
            (slug, datetime.now().isoformat()[:10], action, reason, check_after),
        )
        return cursor.lastrowid


def mark_correction_executed(log_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE correction_log SET executed_at = ? WHERE id = ?",
            (datetime.now().isoformat(), log_id),
        )


def get_correction_log() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM correction_log ORDER BY flagged_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Agent run logging ─────────────────────────────────────────────────────────

def create_agent_run(run_id: str, agent_name: str, started_at: str,
                     topic_id: int = None, topic_title: str = None,
                     trigger: str = "manual") -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO agent_runs
               (run_id, agent_name, started_at, topic_id, topic_title, trigger)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, agent_name, started_at, topic_id, topic_title, trigger),
        )


def finish_agent_run(run_id: str, status: str, finished_at: str,
                     duration_seconds: float, iterations: int,
                     tokens_input: int, tokens_output: int,
                     error_message: str = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE agent_runs SET
               status=?, finished_at=?, duration_seconds=?, iterations=?,
               tokens_input=?, tokens_output=?, error_message=?
               WHERE run_id=?""",
            (status, finished_at, duration_seconds, iterations,
             tokens_input, tokens_output, error_message, run_id),
        )


def log_tool_call(run_id: str, seq_num: int, tool_name: str,
                  inputs_json: str, result_json: str, result_preview: str,
                  success: bool, error_message: str, started_at: str,
                  duration_ms: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_tool_calls
               (run_id, seq_num, tool_name, inputs_json, result_json,
                result_preview, success, error_message, started_at, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, seq_num, tool_name, inputs_json, result_json,
             result_preview, int(success), error_message, started_at, duration_ms),
        )


def get_agent_runs(limit: int = 20, agent_name: str = None,
                   status: str = None) -> list[dict]:
    with get_conn() as conn:
        clauses, params = [], []
        if agent_name:
            clauses.append("agent_name = ?")
            params.append(agent_name)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM agent_runs {where} ORDER BY started_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows]


def get_agent_run_by_id(run_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None


def get_tool_calls_for_run(run_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_tool_calls WHERE run_id = ? ORDER BY seq_num",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_agent_stats() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT
               agent_name,
               COUNT(*) as total_runs,
               SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successes,
               SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failures,
               ROUND(AVG(duration_seconds), 1) as avg_duration_seconds,
               ROUND(AVG(iterations), 1) as avg_iterations,
               SUM(tokens_input) as total_tokens_input,
               SUM(tokens_output) as total_tokens_output
               FROM agent_runs
               WHERE status IN ('success', 'failed')
               GROUP BY agent_name
               ORDER BY total_runs DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_tool_stats(limit: int = 15) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT
               tool_name,
               COUNT(*) as total_calls,
               SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as errors,
               ROUND(AVG(duration_ms), 0) as avg_duration_ms
               FROM agent_tool_calls
               GROUP BY tool_name
               ORDER BY total_calls DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
