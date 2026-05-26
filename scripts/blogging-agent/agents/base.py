import json
import time
from typing import Any
from rich.console import Console

console = Console()


class BaseAgent:
    def __init__(self, client, model: str = None):
        self.client = client
        import config as cfg
        self.provider = cfg.PROVIDER
        self.model = model or cfg.MODEL
        self._run_ctx = None
        # Subclasses can set these before calling run() for richer log metadata
        self._topic_id: int | None = None
        self._topic_title: str | None = None

    def _execute_tool(self, name: str, inputs: dict) -> Any:
        raise NotImplementedError(f"Tool not implemented: {name}")

    def _call_tool(self, name: str, inputs: dict) -> Any:
        """Wraps _execute_tool to log timing, success, and result."""
        _log_tool_console(name, inputs)
        start = time.monotonic()
        error = None
        result: Any = {"error": "unknown"}
        try:
            result = self._execute_tool(name, inputs)
        except Exception as e:
            result = {"error": str(e)}
            error = str(e)
        duration_ms = int((time.monotonic() - start) * 1000)

        if self._run_ctx is not None:
            self._run_ctx.log_tool_call(name, inputs, result, duration_ms, error)

        return result

    def run(self, initial_message: str, system: str, tools: list,
            max_iterations: int = 20) -> str:
        from run_logger import RunContext

        agent_name = self.__class__.__name__.replace("Agent", "").lower()
        with RunContext(agent_name, self._topic_id, self._topic_title) as ctx:
            self._run_ctx = ctx
            try:
                if self.provider in ("openai", "groq", "ollama"):
                    result = self._run_openai(initial_message, system, tools, max_iterations)
                else:
                    result = self._run_anthropic(initial_message, system, tools, max_iterations)
            except Exception as e:
                ctx.mark_failed(str(e))
                raise
            finally:
                self._run_ctx = None
        return result

    # ── Anthropic path ────────────────────────────────────────────────────────

    def _run_anthropic(self, initial_message, system, tools, max_iterations):
        messages = [{"role": "user", "content": initial_message}]
        final_text = ""

        for _ in range(max_iterations):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
            )
            if self._run_ctx:
                usage = response.usage
                self._run_ctx.add_tokens(
                    getattr(usage, "input_tokens", 0),
                    getattr(usage, "output_tokens", 0),
                )
                self._run_ctx.increment_iteration()

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text = block.text
                break

            if response.stop_reason != "tool_use":
                break

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = self._call_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result) if not isinstance(result, str) else result,
                    })
            messages.append({"role": "user", "content": tool_results})

        return final_text

    # ── OpenAI-compatible path (Groq, Ollama, etc.) ───────────────────────────

    def _run_openai(self, initial_message, system, tools, max_iterations):
        oai_tools = _to_openai_tools(tools)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": initial_message},
        ]
        final_text = ""

        for _ in range(max_iterations):
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=8192,
                tools=oai_tools,
                messages=messages,
                tool_choice="auto",
            )
            if self._run_ctx:
                usage = response.usage
                if usage:
                    self._run_ctx.add_tokens(
                        getattr(usage, "prompt_tokens", 0),
                        getattr(usage, "completion_tokens", 0),
                    )
                self._run_ctx.increment_iteration()

            msg = response.choices[0].message
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": msg.tool_calls,
            })

            if not msg.tool_calls:
                final_text = msg.content or ""
                break

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    inputs = json.loads(tc.function.arguments)
                except Exception:
                    inputs = {}
                result = self._call_tool(name, inputs)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result) if not isinstance(result, str) else result,
                })

        return final_text


def _to_openai_tools(tools: list) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _log_tool_console(name: str, inputs: dict):
    preview = ", ".join(
        f"{k}={repr(v)[:40]}"
        for k, v in inputs.items()
        if k not in ("content", "research_brief", "edit_notes", "original_content",
                     "refreshed_content")
    )
    console.print(f"  [dim]→ {name}({preview})[/dim]")
