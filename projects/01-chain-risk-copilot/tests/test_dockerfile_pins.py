"""The venv runs the tests; the container runs the DAG. They must be the same versions.

bronze.py leans on pyarrow behaviour (iter_batches row-group semantics, schema_arrow field
order, the S3 endpoint/scheme split), so a container that resolves a newer pyarrow than the
one under test would run code nobody tested. The same shape let an unpinned ruff hook pass
CRC-13 unformatted: one dependency declared twice, nothing checking they agree.
"""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT = Path(__file__).resolve().parents[1]
DOCKERFILE = (PROJECT / "infra" / "airflow" / "Dockerfile").read_text(encoding="utf-8")
LOCK = (PROJECT / "uv.lock").read_text(encoding="utf-8")
ENV_EXAMPLE = (PROJECT / "infra" / ".env.example").read_text(encoding="utf-8")
FILL_SCRIPT = PROJECT / "scripts" / "fill_env_secrets.py"

# Imported by chainrisk.ingest.bronze / chainrisk.settings and absent from the stock image.
# pydantic ships with Airflow but at Airflow's version, so it is pinned too.
PINNED = ("pyarrow", "pydantic", "pydantic-settings")


def locked_version(name: str) -> str:
    match = re.search(rf'^name = "{re.escape(name)}"\nversion = "([^"]+)"', LOCK, re.MULTILINE)
    assert match, f"{name} not found in uv.lock"
    return match.group(1)


def dockerfile_pins() -> dict[str, str]:
    run = re.search(r"^RUN pip install(?:.*\\\n)*.*$", DOCKERFILE, re.MULTILINE)
    assert run, "Dockerfile has no `RUN pip install`"
    return dict(re.findall(r'"([A-Za-z0-9_.-]+)==([^"]+)"', run.group(0)))


@pytest.mark.parametrize("name", PINNED)
def test_dockerfile_pins_match_the_lockfile(name: str) -> None:
    pins = dockerfile_pins()
    assert name in pins, f"{name} is not pinned with == in the Dockerfile"
    assert pins[name] == locked_version(name)


def test_dockerfile_pins_only_what_it_checks() -> None:
    """A new pin added to the Dockerfile must be added to PINNED, or it escapes the check."""
    assert set(dockerfile_pins()) - set(PINNED) == {"apache-airflow"}


def test_base_image_is_pinned_by_digest_and_matches_the_airflow_pin() -> None:
    base = re.search(r"^FROM apache/airflow:([\d.]+)@sha256:[0-9a-f]{64}$", DOCKERFILE, re.M)
    assert base, "FROM must be apache/airflow:<version>@sha256:<digest>"
    assert dockerfile_pins()["apache-airflow"] == base.group(1)


# ── fill_env_secrets: the example keeps placeholders, setup generates the real values ──


@pytest.fixture(scope="module")
def fill_env() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fill_env_secrets_script", FILL_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_env_example_ships_placeholders_for_every_generated_secret(fill_env: ModuleType) -> None:
    for name in fill_env.GENERATORS:
        assert re.search(rf"^{name}={fill_env.PLACEHOLDER}$", ENV_EXAMPLE, re.MULTILINE)


def test_fill_replaces_placeholders_with_valid_distinct_secrets(fill_env: ModuleType) -> None:
    text, filled = fill_env.fill(ENV_EXAMPLE)
    assert filled == list(fill_env.GENERATORS)
    assert fill_env.PLACEHOLDER not in text
    values = dict(re.findall(r"^(AIRFLOW_(?:FERNET_KEY|JWT_SECRET))=(.*)$", text, re.MULTILINE))
    # Fernet wants 32 urlsafe-base64 bytes: 44 characters ending in "=".
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}=", values["AIRFLOW_FERNET_KEY"])
    assert re.fullmatch(r"[0-9a-f]{64}", values["AIRFLOW_JWT_SECRET"])
    again, _ = fill_env.fill(ENV_EXAMPLE)
    assert again != text  # two clones never share a key


def test_fill_never_rotates_a_secret_already_in_use(fill_env: ModuleType) -> None:
    text, _ = fill_env.fill(ENV_EXAMPLE)
    text2, filled = fill_env.fill(text)
    assert filled == []
    assert text2 == text
