"""The Makefile and dev.ps1 must expose the same targets; nothing else keeps them in sync."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
DEV_PS1 = (REPO_ROOT / "dev.ps1").read_text(encoding="utf-8")
COMPOSE = (REPO_ROOT / "projects/01-chain-risk-copilot/infra/docker-compose.yml").read_text(
    encoding="utf-8"
)

# Deliberately hardcoded, not derived from the Makefile: adding a target means editing the
# Makefile, dev.ps1 and this set, so it cannot happen by accident. Do not "simplify" it away.
EXPECTED = {
    "help", "setup", "up", "down", "ps", "logs", "smoke", "ingest", "profile", "fmt", "lint",
    "test", "verify", "clean",
}  # fmt: skip


def make_targets() -> set[str]:
    """Targets that `make help` lists: those carrying a `## description`."""
    return set(re.findall(r"^([a-z][a-z-]*):.*## ", MAKEFILE, re.MULTILINE))


def ps1_targets() -> set[str]:
    match = re.search(r"ValidateSet\(([^)]*)\)", DEV_PS1)
    assert match, "dev.ps1 has no ValidateSet"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def make_descriptions() -> dict[str, str]:
    return dict(re.findall(r"^([a-z][a-z-]*):.*## (.*)$", MAKEFILE, re.MULTILINE))


def ps1_help() -> dict[str, str]:
    block = re.search(r"'help' \{\s*Write-Host @'\r?\n(.*?)\r?\n'@", DEV_PS1, re.DOTALL)
    assert block, "dev.ps1 help block not found"
    rows = re.findall(r"^\s+([a-z][a-z-]*)\s+(.*)$", block.group(1), re.MULTILINE)
    return dict(rows)


def test_makefile_lists_every_required_target() -> None:
    assert make_targets() == EXPECTED


def test_dev_ps1_exposes_the_same_targets_as_the_makefile() -> None:
    assert ps1_targets() == make_targets()


def test_dev_ps1_help_lists_every_target() -> None:
    assert set(ps1_help()) == ps1_targets()


def test_clean_is_the_only_target_documented_as_destructive() -> None:
    destructive = {t for t, d in make_descriptions().items() if "DESTROYS DATA" in d}
    assert destructive == {"clean"}
    assert {t for t, d in ps1_help().items() if "DESTROYS DATA" in d} == {"clean"}
    # `down -v` wipes volumes: only the clean recipe / branch may run it.
    recipes = [ln for ln in MAKEFILE.splitlines() if ln.startswith("	") and "down -v" in ln]
    assert recipes == ["	$(RUN) $(COMPOSE) down -v"]
    assert len(re.findall(r"'down', '-v'", DEV_PS1)) == 1


def test_verify_is_composed_from_lint_and_test_in_both_entrypoints() -> None:
    assert re.search(r"^verify: lint test ##", MAKEFILE, re.MULTILINE)
    assert re.search(r"'verify'\s*\{\s*Invoke-Steps \(\$LintSteps \+ \$TestSteps\)", DEV_PS1)
    assert re.search(r"'lint'\s*\{\s*Invoke-Steps \$LintSteps\s*\}", DEV_PS1)
    assert re.search(r"'test'\s*\{\s*Invoke-Steps \$TestSteps\s*\}", DEV_PS1)
    assert re.search(r"'fmt'\s*\{\s*Invoke-Steps \$FmtSteps\s*\}", DEV_PS1)


def compose_long_running() -> set[str]:
    """Services in docker-compose.yml that are not one-shots (`restart: "no"`)."""
    body = re.split(r"^volumes:", COMPOSE.split("\nservices:\n", 1)[1], flags=re.MULTILINE)[0]
    blocks = re.split(r"^  (?=[a-z][a-z-]*:\s*$)", body, flags=re.MULTILINE)[1:]
    return {block.split(":", 1)[0] for block in blocks if 'restart: "no"' not in block}


def test_up_waits_on_exactly_the_long_running_services_in_both_entrypoints() -> None:
    """A new long-running service must be added to `up` in both entrypoints, or it is never
    started or waited on; a one-shot must stay out, or `--wait` fails when it exits."""
    make = re.search(r"^LONG_RUNNING := ((?:.*\\\r?\n)*.*)$", MAKEFILE, re.MULTILINE)
    assert make, "Makefile has no LONG_RUNNING"
    in_make = set(make.group(1).replace("\\", " ").split())
    ps1 = re.search(r"\$LongRunning = @\(([^)]*)\)", DEV_PS1)
    assert ps1, "dev.ps1 has no $LongRunning"
    in_ps1 = set(re.findall(r"'([^']+)'", ps1.group(1)))
    assert in_make == in_ps1 == compose_long_running()
