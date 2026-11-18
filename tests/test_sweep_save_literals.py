"""The save-literal sweep's command-line bound on the cores directory it reads.

The sweep itself is a triage tool whose output is read by hand; what is held
here is the argument it takes: a cores directory is read only where one can
sit, and anything else is refused before a single byte is read.
"""

from __future__ import annotations

import json

import pytest

from scripts import sweep_save_literals as sweep


class TestTheCoresDirectory:
    """The four roots are set here, so the cases do not depend on where pytest keeps tmp_path."""

    @pytest.fixture
    def roots(self, tmp_path, monkeypatch):
        names = ("repository", "home", "temporary", "flatpak", "outside")
        repository, home, temporary, flatpak, outside = (tmp_path / n for n in names)
        for directory in (repository, home, temporary, flatpak, outside):
            directory.mkdir()
        monkeypatch.setattr(sweep, "REPO_ROOT", repository)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(sweep.tempfile, "tempdir", str(temporary))
        monkeypatch.setattr(sweep, "SYSTEM_FLATPAK", flatpak)
        monkeypatch.chdir(repository)
        return repository, home, temporary, flatpak, outside

    def test_a_directory_inside_the_repository_the_home_the_temporary_or_the_flatpak_root_is_taken(self, roots):
        repository, home, temporary, flatpak, _ = roots
        assert sweep.resolve_cores_dir(str(repository / "cores")) == repository / "cores"
        assert sweep.resolve_cores_dir(str(home / ".local" / "share" / "flatpak" / "cores")) == (
            home / ".local" / "share" / "flatpak" / "cores"
        )
        assert sweep.resolve_cores_dir(str(temporary / "cores")) == temporary / "cores"
        assert sweep.resolve_cores_dir(str(flatpak / "app" / "cores")) == flatpak / "app" / "cores"

    def test_a_relative_directory_is_resolved_against_the_working_directory(self, roots):
        repository, *_ = roots
        assert sweep.resolve_cores_dir("copy/cores") == repository / "copy" / "cores"

    def test_the_default_lies_inside_the_system_flatpak_root(self):
        assert sweep.resolve_cores_dir(str(sweep.DEFAULT_CORES_DIR)).is_relative_to(sweep.SYSTEM_FLATPAK.resolve())

    @pytest.mark.parametrize(
        "raw",
        [
            lambda repository, outside: str(outside / "cores"),
            lambda repository, outside: "../outside/cores",
            lambda repository, outside: str(repository / ".." / ".." / "etc"),
            lambda repository, outside: str(repository / "escape" / "cores"),
        ],
        ids=["absolute", "relative", "climbs-out-of-the-repository", "through-a-symlink"],
    )
    def test_a_directory_anywhere_else_is_refused(self, roots, raw):
        repository, *_, outside = roots
        (repository / "escape").symlink_to(outside)
        with pytest.raises(ValueError, match="cores directory .* lies outside the repository, the home directory,"):
            sweep.resolve_cores_dir(raw(repository, outside))

    def test_main_refuses_an_outside_directory_before_it_reads_anything(self, roots, monkeypatch, capsys):
        *_, outside = roots
        (outside / "cores").mkdir()
        (outside / "cores" / "fake_libretro.so").write_bytes(b"\0%s.srm\0")

        class _Unread:
            def open(self, *_args, **_kwargs):
                raise AssertionError("reached")

        def reached(*_args, **_kwargs):
            raise AssertionError("reached")

        monkeypatch.setattr(sweep, "RECORDS_PATH", _Unread())
        monkeypatch.setattr(sweep, "hits_in", reached)
        assert sweep.main(["sweep", str(outside / "cores")]) == 2
        captured = capsys.readouterr()
        assert "cores directory " in captured.err
        assert "lies outside" in captured.err
        assert captured.out == ""

    def test_main_answers_a_symlink_loop_with_an_argument_error(self, roots, capsys):
        """Python 3.12's ``resolve()`` raises on the loop, 3.13 and later resolve past it; both end in exit 2."""
        repository, *_ = roots
        (repository / "loop").mkdir()
        (repository / "loop" / "self").symlink_to(repository / "loop" / "self")
        argument = str(repository / "loop" / "self" / "cores")
        assert sweep.main(["sweep", argument]) == 2
        err = capsys.readouterr().err
        assert err.startswith("cores directory ")
        assert argument in err

    def test_main_names_an_argument_with_an_embedded_nul(self, roots, capsys):
        repository, *_ = roots
        argument = str(repository / "cor\0es")
        assert sweep.main(["sweep", argument]) == 2
        assert f"cores directory {argument!r} cannot be resolved: " in capsys.readouterr().err

    def test_main_refuses_a_missing_directory(self, roots, capsys):
        repository, *_ = roots
        assert sweep.main(["sweep", str(repository / "absent")]) == 2
        assert "cores directory not found: " in capsys.readouterr().err

    def test_main_sweeps_a_directory_it_takes(self, roots, monkeypatch, capsys):
        _, _, temporary, _, _ = roots
        records = temporary / "save_memory.json"
        records.write_text(json.dumps({"cores": {"fake": {}, "absent": {}}}), encoding="utf-8")
        cores = temporary / "cores"
        cores.mkdir()
        (cores / "fake_libretro.so").write_bytes(b"\0%s%c%s.eep\0")
        monkeypatch.setattr(sweep, "RECORDS_PATH", records)
        assert sweep.main(["sweep", str(cores)]) == 0
        out = capsys.readouterr().out
        assert "fake: '%s%c%s.eep'" in out
        assert "1 of 1 scanned record cores carry save-looking literals" in out
        assert "no binary deployed for: absent" in out
