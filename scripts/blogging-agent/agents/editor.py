from .base import BaseAgent
from storage.db import save_edited_draft

TOOLS = [
    {
        "name": "save_edited_draft",
        "description": "Save the edited article. Call this once your edits are complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer"},
                "content": {"type": "string", "description": "Full edited article in markdown"},
                "edit_notes": {
                    "type": "string",
                    "description": "Bullet-point summary of changes made and the reasoning behind each change",
                },
                "title": {"type": "string", "description": "Updated title if you improved it (optional)"},
                "meta_description": {"type": "string", "description": "Updated meta description if you improved it (optional)"},
            },
            "required": ["draft_id", "content", "edit_notes"],
        },
    },
]

SYSTEM = """You are an expert blog editor. Substantially improve the draft — not just typos.

Review and fix:
1. **Hook** — is the opening compelling? If not, rewrite the first paragraph.
2. **Structure** — does the article flow logically? Reorder sections if needed.
3. **Clarity** — simplify jargon, shorten long sentences, cut padding.
4. **Depth** — flag or fill sections that are vague or generic with more specific language.
5. **SEO** — primary keyword should appear naturally in the opening, one H2, and conclusion. Not stuffed.
6. **Conclusion** — must be actionable. Remove "in conclusion" phrasing.
7. **Formatting** — fix any markdown issues, ensure headers are hierarchical.

Write edit_notes as a concise bullet list: what you changed and why."""


class EditorAgent(BaseAgent):
    def edit_article(self, draft: dict) -> None:
        self._topic_id = draft.get("topic_id")
        self._topic_title = draft.get("title")
        prompt = f"""Edit and improve this draft article (draft_id: {draft['id']}).

**Title:** {draft['title']}
**Primary keyword:** {draft['keyword']}
**Meta description:** {draft['meta_description']}

---

{draft['content']}

---

Review carefully, make substantive improvements, then save with save_edited_draft."""

        self.run(prompt, SYSTEM, TOOLS)

    def _execute_tool(self, name: str, inputs: dict):
        if name == "save_edited_draft":
            save_edited_draft(
                draft_id=inputs["draft_id"],
                content=inputs["content"],
                edit_notes=inputs["edit_notes"],
                title=inputs.get("title"),
                meta_description=inputs.get("meta_description"),
            )
            return {"success": True}
        return {"error": f"Unknown tool: {name}"}
