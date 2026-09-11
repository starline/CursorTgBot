from __future__ import annotations

AGENT_CONTEXT = """You are a Cursor coding agent working in the target repository (REPO_CWD).

Working directory is the repo root. Respect project rules if present (AGENTS.md, .cursor/rules, CONTRIBUTING, etc.).

Do not git commit or push unless the user explicitly asks in this message.
Keep changes focused. Prefer clear, minimal diffs.
Reply with a short summary of what you did and what remains.
"""


def wrap_user_task(text: str) -> str:
    return f"{AGENT_CONTEXT}\n\nUser request:\n{text.strip()}"
