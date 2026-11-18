"""Tests for scripts/smoke_release_bundle.py — what a failed check shows.

The checks themselves need a built bundle and run in CI's ``bundle`` job.
What these tests guard is that a failed check's long output keeps its end as
well as its start, and that the cut is stated.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import smoke_release_bundle as smoke

PROGRESS = "." * 72 + " [ 50%]\n"
FAILURE = "FAILED tests/test_x.py::test_y - AssertionError: assert 1 == 2\n"
SUMMARY = "1 failed, 9999 passed in 84.00s\n"


def _shown(stdout: str, stderr: str = "") -> str:
    result = subprocess.CompletedProcess(["pytest"], 1, stdout=stdout, stderr=stderr)
    with pytest.raises(SystemExit) as raised:
        smoke._fail("the full suite", result)  # pyright: ignore[reportPrivateUsage] - the message a failure shows
    return str(raised.value)


class TestAFailureNamesWhatFailed:
    def test_a_long_outputs_last_lines_survive(self):
        shown = _shown(PROGRESS * 1000 + FAILURE + SUMMARY)
        assert FAILURE in shown
        assert SUMMARY in shown

    def test_a_long_outputs_first_lines_survive(self):
        shown = _shown("ImportError: no module named x\n" + PROGRESS * 1000)
        assert "ImportError: no module named x" in shown

    def test_the_cut_is_stated(self):
        stdout = PROGRESS * 1000
        omitted = len(stdout) - smoke.SHOWN_HEAD - smoke.SHOWN_TAIL
        assert f"[... {omitted} characters omitted ...]" in _shown(stdout)

    def test_a_short_output_is_shown_whole(self):
        stdout = PROGRESS * 3 + FAILURE + SUMMARY
        shown = _shown(stdout, stderr="a warning\n")
        assert stdout in shown
        assert "a warning" in shown
        assert "omitted" not in shown

    def test_a_long_stderr_keeps_its_end_too(self):
        shown = _shown("", stderr="x" * 50000 + "\nTraceback: the last line\n")
        assert "Traceback: the last line" in shown

    def test_the_suite_asks_pytest_for_a_summary_line_per_failure(self, monkeypatch, tmp_path):
        commands: list[list[str]] = []

        def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="1 passed in 0.01s\n", stderr="")

        monkeypatch.setattr(smoke, "_run", run)
        python = tmp_path / "python3"
        smoke._check_full_suite(python, tmp_path)  # pyright: ignore[reportPrivateUsage] - the command it runs
        suite = next(command for command in commands if "pytest" in command and "-q" in command)
        assert "-rfE" in suite
        assert "--tb=short" in suite
