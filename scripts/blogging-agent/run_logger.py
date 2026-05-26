import json
import os
import uuid
import time
from datetime import datetime

from storage.db import (
    create_agent_run, finish_agent_run, log_tool_call as _db_log_tool,
)

_SENSITIVE_KEYS = {"content", "research_brief", "edit_notes", "original_content",
                   "refreshed_content", "refreshed_content"}


def _detect_trigger() -> str:
    return "actions" if os.getenv("GITHUB_ACTIONS") else "manual"


def _inputs_preview(inputs: dict) -> str:
    parts = []
    for k, v in inputs.items():
        if k in _SENSITIVE_KEYS:
            parts.append(f"{k}=<{len(str(v))} chars>")
        else:
            parts.append(f"{k}={repr(str(v))[:60]}")
    return ", ".join(parts)


def _result_preview(result) -> str:
    if result is None:
        return "null"
    if isinstance(result, dict):
        if "error" in result:
            return f"ERROR: {result['error'][:120]}"
        if "success" in result and result["success"]:
            extras = {k: v for k, v in result.items() if k not in ("success", "error")}
            if extras:
                preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in list(extras.items())[:3])
                return f"ok — {preview}"
            return "ok"
        # Show top-level keys with counts for list values
        parts = []
        for k, v in list(result.items())[:4]:
            if isinstance(v, list):
                parts.append(f"{k}=[{len(v)} items]")
            else:
                parts.append(f"{k}={repr(str(v))[:40]}")
        return ", ".join(parts) or "{}"
    if isinstance(result, list):
        if len(result) == 0:
            return "[] (empty)"
        if isinstance(result[0], dict) and "error" in result[0]:
            return f"ERROR: {result[0]['error'][:100]}"
        return f"[{len(result)} items]"
    return str(result)[:120]


class RunContext:
    def __init__(self, agent_name: str, topic_id: int = None, topic_title: str = None):
        self.run_id = uuid.uuid4().hex[:12]
        self.agent_name = agent_name
        self.topic_id = topic_id
        self.topic_title = topic_title
        self._started_at = datetime.now().isoformat()
        self._start_mono = time.monotonic()
        self._iterations = 0
        self._tokens_input = 0
        self._tokens_output = 0
        self._seq = 0
        self._error: str | None = None

    def __enter__(self):
        create_agent_run(
            run_id=self.run_id,
            agent_name=self.agent_name,
            started_at=self._started_at,
            topic_id=self.topic_id,
            topic_title=self.topic_title,
            trigger=_detect_trigger(),
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._error = f"{exc_type.__name__}: {exc_val}"
        status = "failed" if self._error else "success"
        finish_agent_run(
            run_id=self.run_id,
            status=status,
            finished_at=datetime.now().isoformat(),
            duration_seconds=round(time.monotonic() - self._start_mono, 2),
            iterations=self._iterations,
            tokens_input=self._tokens_input,
            tokens_output=self._tokens_output,
            error_message=self._error,
        )
        return False  # don't suppress exceptions

    def mark_failed(self, error: str) -> None:
        self._error = error

    def increment_iteration(self) -> None:
        self._iterations += 1

    def add_tokens(self, inp: int, out: int) -> None:
        self._tokens_input += inp
        self._tokens_output += out

    def log_tool_call(self, tool_name: str, inputs: dict, result,
                      duration_ms: int, error: str = None) -> None:
        self._seq += 1
        success = error is None and not (isinstance(result, dict) and "error" in result)
        if not success and error is None and isinstance(result, dict):
            error = result.get("error")

        inputs_safe = {k: (f"<{len(str(v))} chars>" if k in _SENSITIVE_KEYS else v)
                       for k, v in inputs.items()}

        result_for_db = result
        if isinstance(result, dict):
            result_for_db = {k: (f"<{len(str(v))} chars>" if k in _SENSITIVE_KEYS else v)
                             for k, v in result.items()}

        _db_log_tool(
            run_id=self.run_id,
            seq_num=self._seq,
            tool_name=tool_name,
            inputs_json=json.dumps(inputs_safe, default=str)[:2000],
            result_json=json.dumps(result_for_db, default=str)[:2000],
            result_preview=_result_preview(result)[:200],
            success=success,
            error_message=error,
            started_at=datetime.now().isoformat(),
            duration_ms=duration_ms,
        )
