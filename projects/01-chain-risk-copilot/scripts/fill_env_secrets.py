"""Replace placeholder secrets in infra/.env with freshly generated ones.

    uv run python scripts/fill_env_secrets.py

Run by `make setup` / `dev.ps1 setup` right after infra/.env is copied from .env.example.
The example keeps placeholders, so no two clones share a Fernet key; this fills them in.
Idempotent: only a value still equal to PLACEHOLDER is replaced, so a re-run never rotates a
key already in use (which would make stored connection secrets undecryptable).

Standard library only, so it runs before or without `uv sync`.
"""

import base64
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

PLACEHOLDER = "generated-by-make-setup"
ENV_FILE = Path(__file__).resolve().parents[1] / "infra" / ".env"


def fernet_key() -> str:
    """Same format as cryptography's Fernet.generate_key(): 32 random bytes, urlsafe base64."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def jwt_secret() -> str:
    return secrets.token_hex(32)


GENERATORS: dict[str, Callable[[], str]] = {
    "AIRFLOW_FERNET_KEY": fernet_key,
    "AIRFLOW_JWT_SECRET": jwt_secret,
}


def fill(text: str) -> tuple[str, list[str]]:
    """Return the text with placeholder values replaced, and the names that were filled."""
    filled: list[str] = []
    lines = []
    for line in text.splitlines(keepends=True):
        name, sep, value = line.partition("=")
        if name in GENERATORS and sep and value.strip() == PLACEHOLDER:
            newline = line[len(line.rstrip("\r\n")) :]
            line = f"{name}={GENERATORS[name]()}{newline}"
            filled.append(name)
        lines.append(line)
    return "".join(lines), filled


def main() -> int:
    if not ENV_FILE.exists():
        print(f"{ENV_FILE} not found; run setup first", file=sys.stderr)
        return 1
    new, filled = fill(ENV_FILE.read_text(encoding="utf-8"))
    if filled:
        ENV_FILE.write_text(new, encoding="utf-8", newline="")
        print(f"generated {', '.join(filled)} in infra/.env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
