from __future__ import annotations

HUGSALES_CONTEXT = """You are a Cursor coding agent working on the HugSalesSolo repository.

Working directory is the repo root. Application source lives under www/app/dev/.
Respect project rules (AGENTS.md, .cursor/rules): core vs modules boundary, no asset-map:compile in dev,
PHPUnit/PHPStan only via bash ./scripts/phpunit.sh and bash ./scripts/phpstan.sh (Docker).

Do not git commit or push unless the user explicitly asks in this message.
Keep changes focused. Prefer performance-conscious fixes when fixing bugs.
Reply with a short summary of what you did and what remains.
"""


def wrap_user_task(text: str) -> str:
    return f"{HUGSALES_CONTEXT}\n\nUser request:\n{text.strip()}"
