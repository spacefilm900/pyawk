"""Guards so CI cannot silently run almost nothing.

The golden corpus and regex differential records are committed and must
always be collected.  Reference-awk smoke tests may skip when the
programs are missing; that is fine and must not hide an empty suite.
``scripts/regress.sh`` is the exact local recipe matching GitHub Actions.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import harness

REPO = Path(__file__).resolve().parent.parent
REGRESS = REPO / "scripts" / "regress.sh"


def test_core_suites_are_present() -> None:
    """Golden + imported cases and the differential records must be non-trivial."""
    cases = harness.discover()
    assert len(cases) >= 500, f"only {len(cases)} golden/imported cases"
    assert any(c.category == "errors" for c in cases)
    # Differential records are loaded by the differential tests; count files.
    import json

    records = json.loads((REPO / "awk_tests" / "regex_differential.json").read_text(encoding="utf-8"))["records"]
    utf8 = json.loads((REPO / "awk_tests" / "regex_differential_utf8.json").read_text(encoding="utf-8"))["records"]
    assert len(records) >= 500, f"only {len(records)} C-locale differential patterns"
    assert len(utf8) >= 50, f"only {len(utf8)} UTF-8 differential patterns"


def test_regress_script_exists_and_is_executable() -> None:
    assert REGRESS.is_file(), "scripts/regress.sh missing"
    assert REGRESS.stat().st_mode & stat.S_IXUSR, "scripts/regress.sh must be executable"
    text = REGRESS.read_text(encoding="utf-8")
    assert "ruff check" in text
    assert "pytest" in text
    assert "run_golden.py" in text
    assert "regex_fuzz.py --check" in text


def test_regress_exits_nonzero_when_a_step_fails(tmp_path: Path) -> None:
    """A failing step must fail the script (set -e), not report success."""
    assert REGRESS.is_file(), "scripts/regress.sh missing"
    # Override the ruff step with a command that fails immediately.
    env = {**os.environ, "REGRESS_RUFF": "false"}
    proc = subprocess.run(
        ["bash", str(REGRESS), "quick"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr


def test_workflow_matches_regress_recipe() -> None:
    """The Actions workflow definition must invoke the same regress.sh modes.

    The canonical file is ``ci/github-ci.yml`` (always in the repo).  A copy
    at ``.github/workflows/ci.yml`` enables GitHub Actions; pushing that path
    needs a token with the ``workflow`` scope, so CI may be enabled in a
    follow-up.  When both exist, they must be identical.
    """
    canonical = REPO / "ci" / "github-ci.yml"
    assert canonical.is_file(), "missing ci/github-ci.yml"
    text = canonical.read_text(encoding="utf-8")
    assert "scripts/regress.sh quick" in text
    assert "scripts/regress.sh thorough" in text
    assert "pull_request" in text
    assert "enhancements" in text
    # No secrets required for the normal jobs.
    assert "secrets." not in text
    enabled = REPO / ".github" / "workflows" / "ci.yml"
    if enabled.is_file():
        assert enabled.read_text(encoding="utf-8") == text
