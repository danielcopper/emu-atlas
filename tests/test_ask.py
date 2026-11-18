"""Tests for ``scripts/ask.py`` — ``mise run ask`` and ``mise run probe``, here and with ``--target``.

The client runs through its ``main``. Without ``--target`` it runs the question
or the probe script on this interpreter, with the atlas of the tree it asks
from — a test tree here, whose atlas says what it was asked. With ``--target``
every program it starts is a stand-in of ``tests/remote_target.py`` on a
``PATH`` that holds nothing else, so no case runs a real ``ssh`` or contacts
anything: each case sets a target up with the real setup first, then asks it
through the entry that setup wrote, the key's real ``authorized_keys`` line and
the dispatcher it installed. That dispatcher names the ``bwrap`` stand-in,
which runs what it is handed with no sandbox; the probe script's run through
the real sandbox is in :class:`TestTheFirstProbe`.

``HOME``, ``XDG_CONFIG_HOME`` and ``PATH`` are moved for the whole module
(``isolated_module``), and this process and each stand-in guard the real home
as ``tests/remote_target.py`` describes. "Nothing done on the target" is checked
as every file, directory and link under the target's home, with its bytes and
its mode, before and after.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts import ask, dev_dispatch, setup_remote_target
from scripts.probes import answers_agree
from tests.remote_target import (
    BROKEN_BWRAP_MESSAGE,
    Target,
    World,
    fake_path,
    log_lines,
    ssh_calls,
)
from tests.remote_target_suite import (
    DISPATCHER,
    FLOOR,
    REPO,
    ROOT,
    Entry,
    contents,
    install_dispatcher,
    isolated_module,
    sandbox_home,
    snapshot,
)

pytestmark = pytest.mark.usefixtures(isolated_module.__name__)

_ADDRESS = "192.0.2.20"
_NEW_ADDRESS = "192.0.2.30"
_ELSEWHERE = "192.0.2.99"
_CONSOLE = "192.0.2.40"
_SEEN_FROM = "198.51.100.6"
_RETRODECK = Path("/var/lib/flatpak/app/net.retrodeck.retrodeck")

# An atlas that says where it was imported from and what it was asked, writes
# a line on stderr, and ends with the status a word ``--exit=<n>`` names.
_SAYING_ATLAS = {
    "atlas/__init__.py": "",
    "atlas/__main__.py": (
        "import json, sys\n"
        "import atlas\n"
        "sys.stderr.write('atlas said this on stderr\\n')\n"
        "print(json.dumps({'file': atlas.__file__, 'argv': sys.argv[1:]}))\n"
        "codes = [word[len('--exit='):] for word in sys.argv[1:] if word.startswith('--exit=')]\n"
        "sys.exit(int(codes[0]) if codes else 0)\n"
    ),
    "atlas/data/table.json": "{}\n",
    # A probe that says where its atlas came from and what it was given, and
    # ends with the status its first argument names.
    "scripts/probes/say.py": (
        "import json, sys\n"
        "import atlas\n"
        "sys.stderr.write('the probe said this on stderr\\n')\n"
        "print(json.dumps({'file': atlas.__file__, 'argv': sys.argv[1:]}))\n"
        "sys.exit(int(sys.argv[1]) if sys.argv[1:] and sys.argv[1].isdigit() else 0)\n"
    ),
}


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@dataclass
class There:
    """A target set up with the real setup, and the test tree this machine asks from."""

    world: World
    world_file: Path
    tools: Path
    target_home: Path
    tree: Path
    config: Path

    def save(self) -> None:
        self.world.save(self.world_file)

    def target(self) -> Target:
        return next(iter(self.world.targets.values()))

    @property
    def root(self) -> Path:
        return self.target_home / ROOT


@pytest.fixture
def there(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> There:
    tools = Path(fake_path(tmp_path))
    build_home = tmp_path / "build"
    target_home = tmp_path / "target"
    build_home.mkdir()
    target_home.mkdir()
    world = World(
        targets={_ADDRESS: Target(home=str(target_home), client_address=_SEEN_FROM)}, log=str(tmp_path / "log")
    )
    world_file = tmp_path / "world.json"
    world.save(world_file)
    monkeypatch.setenv("HOME", str(build_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(build_home / ".config"))
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setenv("FAKE_REMOTE_WORLD", str(world_file))
    monkeypatch.setattr(setup_remote_target, "TARGET_PYTHON", sys.executable)
    monkeypatch.setattr(setup_remote_target, "TARGET_BWRAP", str(tools / "bwrap"))
    monkeypatch.setattr(setup_remote_target, "build_host_name", lambda: "buildhost")
    assert setup_remote_target.main(["handheld", f"player@{_ADDRESS}"]) == 0
    capfd.readouterr()
    install_dispatcher(target_home, bwrap=str(tools / "bwrap"))
    tree = write_tree(tmp_path / "tree", _SAYING_ATLAS)
    monkeypatch.setattr(ask, "TREE", tree)
    config = build_home / ".config" / "emu-atlas-dev" / "ssh_config"
    return There(world, world_file, tools, target_home, tree, config)


@pytest.fixture
def here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The test tree asked from without a target; nothing on ``PATH`` but an empty directory."""
    tree = write_tree(tmp_path / "tree", _SAYING_ATLAS)
    monkeypatch.setattr(ask, "TREE", tree)
    return tree


def called(capfd: pytest.CaptureFixture[str], mode: ask.Mode, *argv: str) -> tuple[int, str, str]:
    status = ask.main(mode, list(argv))
    captured = capfd.readouterr()
    return status, captured.out, captured.err


def without_caches(entries: dict[str, Entry]) -> dict[str, Entry]:
    return {name: entry for name, entry in entries.items() if "__pycache__" not in Path(name).parts}


def said(out: str) -> dict[str, Any]:
    return json.loads(out)


class TestTheTasks:
    """The task lines, run as mise runs them: the arguments appended to the one line, each quoted as one word."""

    @pytest.mark.parametrize("name", ["ask", "probe"])
    def test_each_task_is_one_raw_line_with_no_usage_spec(self, name: str) -> None:
        task = tomllib.loads((REPO / "mise.toml").read_text(encoding="utf-8"))["tasks"][name]

        assert task["run"] == f"python scripts/ask.py {name}"
        assert "usage" not in task
        assert task["raw"] is True

    @pytest.mark.parametrize(
        ("name", "argv", "target", "words"),
        [
            pytest.param(
                "ask",
                ["--target", "deck", "save-portability", "gba", "--source", "a", "--target", "b", "--content",
                 "/x/Some Game (USA).chd"],
                "deck",
                ["save-portability", "gba", "--source", "a", "--target", "b", "--content", "/x/Some Game (USA).chd"],
                id="a question with a --target of its own",
            ),
            pytest.param("ask", ["--target=deck", "--", "detect"], "deck", ["detect"], id="--target= and a --"),
            pytest.param("ask", ["save-portability", "--target", "b"], None, ["save-portability", "--target", "b"],
                         id="no target: everything is the question"),
            pytest.param("probe", ["--target", "deck", "answers_agree.py", "a b"], "deck", ["answers_agree.py", "a b"],
                         id="a probe and its argument"),
        ],
    )
    def test_the_script_gets_the_words_and_reads_only_a_leading_target(
        self, tmp_path: Path, name: str, argv: list[str], target: str | None, words: list[str]
    ) -> None:
        task = tomllib.loads((REPO / "mise.toml").read_text(encoding="utf-8"))["tasks"][name]
        record = tmp_path / "argv.json"
        python = tmp_path / "python"
        python.write_text(
            f"#!{sys.executable} -I\nimport json, sys\nopen({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        python.chmod(0o755)

        ran = subprocess.run(
            ["/bin/sh", "-c", f"{task['run']} {shlex.join(argv)}"],
            env={"PATH": str(tmp_path)},
            capture_output=True,
            text=True,
            check=False,
        )

        assert ran.returncode == 0, ran.stderr
        recorded = json.loads(record.read_text(encoding="utf-8"))
        assert recorded == ["scripts/ask.py", name, *argv]
        assert ask.split_target(recorded[2:]) == (target, words)


class TestUsage:
    """A call that cannot be put ends with 2 and contacts nothing."""

    @pytest.mark.parametrize(
        ("mode", "argv", "said_there"),
        [
            ("ask", [], "no question to ask."),
            ("ask", ["--"], "no question to ask."),
            ("ask", ["--target", "handheld"], "no question to ask."),
            ("probe", ["--target", "handheld"], "no probe script to run."),
            ("ask", ["--target"], "a target's name is letters, digits, '.', '_' and '-' — not ''."),
            ("ask", ["--target=", "detect"], "a target's name is letters, digits, '.', '_' and '-' — not ''."),
            ("ask", ["--target", "", "detect"], "a target's name is letters, digits, '.', '_' and '-' — not ''."),
            ("ask", ["--target", "-oProxyCommand=x", "detect"], "— not '-oProxyCommand=x'."),
            ("ask", ["--target", "a b", "detect"], "— not 'a b'."),
            ("probe", ["../escape.py"], "a probe script is a file name of scripts/probes/"),
            ("probe", ["say.sh"], "a probe script is a file name of scripts/probes/"),
            ("probe", ["absent.py"], "scripts/probes/ holds no absent.py; it holds: say.py."),
        ],
    )
    def test_it_ends_with_2_before_anything_is_contacted(
        self, there: There, capfd: pytest.CaptureFixture[str], mode: ask.Mode, argv: list[str], said_there: str
    ) -> None:
        before = snapshot(there.target_home)
        logins = len(ssh_calls(there.world))

        status, out, err = called(capfd, mode, *argv)

        assert status == ask.USAGE == 2
        assert out == ""
        assert said_there in err
        assert err.endswith(f"{ask.USAGE_LINES}\n")
        assert len(ssh_calls(there.world)) == logins
        assert snapshot(there.target_home) == before


class TestHere:
    """Without ``--target``, the question and the probe run here, with the asking tree's atlas."""

    @pytest.mark.parametrize("status", [0, 2])
    def test_a_question_passes_atlas_stdout_stderr_and_status_through(
        self, here: Path, capfd: pytest.CaptureFixture[str], status: int
    ) -> None:
        code, out, err = called(capfd, "ask", "--", "detect", f"--exit={status}")

        assert code == status
        assert said(out) == {"file": str(here / "atlas" / "__init__.py"), "argv": ["detect", f"--exit={status}"]}
        assert err == "atlas said this on stderr\n"

    def test_a_question_with_a_target_of_its_own_keeps_it(self, here: Path, capfd: pytest.CaptureFixture[str]) -> None:
        question = ["save-portability", "gba", "--source", "a", "--target", "b", "--content", "/x/Some Game (USA).chd"]

        code, out, _ = called(capfd, "ask", *question)

        assert code == 0
        assert said(out)["argv"] == question

    def test_a_probe_runs_with_the_trees_atlas_importable(self, here: Path, capfd: pytest.CaptureFixture[str]) -> None:
        code, out, err = called(capfd, "probe", "say.py", "1", "a b")

        assert code == 1
        assert said(out) == {"file": str(here / "atlas" / "__init__.py"), "argv": ["1", "a b"]}
        assert err == "the probe said this on stderr\n"

    @pytest.mark.parametrize(
        ("source", "error"),
        [("print('before')\nraise KeyError('x')\n", "KeyError: 'x'"), ("def broken(:\n", "SyntaxError")],
        ids=["at run time", "at compile time"],
    )
    def test_a_probe_that_crashes_ends_with_2_not_1(
        self, here: Path, capfd: pytest.CaptureFixture[str], source: str, error: str
    ) -> None:
        """A crash is a question not put, never "does not hold": the bootstrap the dispatcher runs probes with."""
        write_tree(here, {"scripts/probes/crashes.py": source})

        code, out, err = called(capfd, "probe", "crashes.py")

        assert code == dev_dispatch.PROBE_NOT_PUT == 2
        assert out == ("before\n" if "KeyError" in source else "")
        assert error in err
        assert err.endswith(
            "emu-atlas-dev: the probe crashes.py crashed (the traceback is above): a question it could not put.\n"
        )

    def test_the_real_atlas_answers_as_python_m_atlas_does(
        self, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """#813: ``save-portability --source <a> --target <b>`` is asked here as it is asked directly."""
        monkeypatch.setattr(ask, "TREE", REPO)
        question = ["save-portability", "gba", "--source", "a", "--target", "b", "--home", str(tmp_path)]
        direct = subprocess.run(
            [sys.executable, "-m", "atlas", *question], cwd=REPO, capture_output=True, text=True, check=False
        )

        code, out, err = called(capfd, "ask", *question)

        assert (code, out, err) == (direct.returncode, direct.stdout, direct.stderr)

    def test_nothing_is_written_into_the_tree(self, here: Path, capfd: pytest.CaptureFixture[str]) -> None:
        """What the run returns is printed; the tree gains nothing but the interpreter's own bytecode cache."""
        before = without_caches(snapshot(here))

        called(capfd, "ask", "detect")
        called(capfd, "probe", "say.py")

        assert without_caches(snapshot(here)) == before


class TestThere:
    """With ``--target``: the copy, then the question or the probe there, its output and status here."""

    @pytest.mark.parametrize("status", [0, 2, 1])
    def test_a_question_passes_stdout_stderr_and_status_through(
        self, there: There, capfd: pytest.CaptureFixture[str], status: int
    ) -> None:
        code, out, err = called(capfd, "ask", "--target", "handheld", "--", "detect", f"--exit={status}")

        assert code == status
        tree = (there.root / "current").resolve()
        assert said(out) == {"file": str(tree / "atlas" / "__init__.py"), "argv": ["detect", f"--exit={status}"]}
        assert err == "atlas said this on stderr\n"

    def test_a_question_with_a_target_of_its_own_keeps_it_and_its_words_whole(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """#813: ``save-portability --source <a> --target <b>`` is asked there with its own ``--target``."""
        content = "/x/A Game (Europe) 'x' \"y\".chd"
        question = ["save-portability", "gba", "--source", "a", "--target", "b", "--content", content, "ü"]

        code, out, _ = called(capfd, "ask", "--target", "handheld", *question)

        assert code == 0
        assert said(out)["argv"] == question

    def test_the_real_atlas_answers_there_as_it_does_here(
        self, there: There, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """#813: the same question, here and on the target, gives the same contract JSON and status."""
        monkeypatch.setattr(ask, "TREE", REPO)
        empty = tmp_path / "empty home"
        empty.mkdir()
        question = ["save-portability", "gba", "--source", "a", "--target", "b", "--home", str(empty)]

        here_code, here_out, _ = called(capfd, "ask", *question)
        there_code, there_out, _ = called(capfd, "ask", "--target", "handheld", *question)

        assert (there_code, there_out) == (here_code, here_out)
        assert json.loads(there_out) is not None

    def test_a_probe_runs_there_with_its_words(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        code, out, err = called(capfd, "probe", "--target", "handheld", "say.py", "1", "a (b)")

        assert code == 1
        tree = (there.root / "current").resolve()
        assert said(out) == {"file": str(tree / "atlas" / "__init__.py"), "argv": ["1", "a (b)"]}
        assert err == "the probe said this on stderr\n"

    def test_a_probe_without_words_gets_none(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        code, out, _ = called(capfd, "probe", "--target", "handheld", "say.py")

        assert code == 0
        assert said(out)["argv"] == []

    def test_every_call_copies_the_tree_first(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        called(capfd, "ask", "--target", "handheld", "detect")
        first = (there.root / "current").resolve()

        called(capfd, "ask", "--target", "handheld", "detect")

        assert (there.root / "current").resolve() != first
        assert log_lines(there.world)[-4:] == [f"login {_ADDRESS} key"] * 4

    def test_an_edit_made_since_the_last_call_reaches_the_target(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The working tree as it stands, not a commit: the test tree is no repository at all."""
        called(capfd, "ask", "--target", "handheld", "detect")
        main = there.tree / "atlas" / "__main__.py"
        main.write_text("print('edited')\n" + main.read_text(encoding="utf-8"), encoding="utf-8")

        code, out, _ = called(capfd, "ask", "--target", "handheld", "detect")

        assert code == 0
        assert out.splitlines()[0] == "edited"
        assert ((there.root / "current").resolve() / "atlas" / "__main__.py").read_text(encoding="utf-8") == (
            main.read_text(encoding="utf-8")
        )

    def test_nothing_is_written_into_the_tree_and_the_copy_stays_as_sent(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        before = without_caches(snapshot(there.tree))

        called(capfd, "ask", "--target", "handheld", "detect")
        tree = (there.root / "current").resolve()
        sent = contents(without_caches(snapshot(tree)))
        called(capfd, "probe", "--target", "handheld", "say.py")

        assert without_caches(snapshot(there.tree)) == before
        assert (there.root / "current").resolve() != tree
        assert contents(without_caches(snapshot((there.root / "current").resolve()))) == sent

    def test_the_entry_written_here_is_read_alone(
        self, there: There, capfd: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """The user's own SSH config would send ``handheld`` elsewhere; the call reaches the entry's target."""
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        there.world.targets[_ELSEWHERE] = Target(home=str(decoy), client_address=_SEEN_FROM)
        there.world.configs["handheld"] = {"hostname": [_ELSEWHERE], "user": ["player"], "port": ["22"]}
        there.save()

        code, _, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert code == 0, err
        assert list(decoy.iterdir()) == []
        assert log_lines(there.world)[-2:] == [f"login {_ADDRESS} key"] * 2
        calls = ssh_calls(there.world)[-2:]
        assert [call["_config"] for call in calls] == [[str(there.config)]] * 2
        assert [call["_offered"] for call in calls] == [[str(there.config.with_name("id_ed25519"))]] * 2

    @pytest.mark.skipif(os.geteuid() == 0, reason="root removes a file whatever its directory's mode")
    def test_a_note_the_copy_leaves_is_shown_and_the_question_still_asked(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        write_tree(there.tree, {"atlas/stuck/inside.py": ""})
        called(capfd, "ask", "--target", "handheld", "detect")
        stuck = (there.root / "current").resolve() / "atlas" / "stuck"
        stuck.chmod(0o500)
        try:
            code, out, err = called(capfd, "ask", "--target", "handheld", "detect")
        finally:
            for each in there.root.glob("trees/*/atlas/stuck"):
                each.chmod(0o700)

        assert code == 0
        assert said(out)["argv"] == ["detect"]
        note, answer = err.split("\n", 1)
        assert note.startswith("emu-atlas-dev: the copy is current, but could not remove all of ")
        assert answer == "atlas said this on stderr\n"


class TestTheCopy:
    """What a copy carries, built from a test tree."""

    def test_it_holds_atlas_and_the_probes_without_caches_and_the_manifest_last(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str]
    ) -> None:
        tree = write_tree(
            tmp_path / "tree",
            {
                **_SAYING_ATLAS,
                "atlas/__pycache__/__init__.cpython-314.pyc": "x",
                "atlas/data/stray.pyc": "x",
                "scripts/probes/__pycache__/say.cpython-314.pyc": "x",
                "scripts/other.py": "",
                "tests/test_x.py": "",
                "README.md": "",
            },
        )
        (tree / "atlas" / "link.py").symlink_to("__init__.py")

        stream = ask.build_copy(tree, "3.12")

        with tarfile.open(fileobj=io.BytesIO(stream), mode="r:") as archive:
            members = archive.getmembers()
            manifest = archive.extractfile(members[-1])
            assert manifest is not None
            assert json.loads(manifest.read()) == {"requires_python": "3.12"}
        assert [member.name for member in members] == [
            "atlas",
            "atlas/__init__.py",
            "atlas/__main__.py",
            "atlas/data",
            "atlas/data/table.json",
            "scripts/probes",
            "scripts/probes/say.py",
            "tree.json",
        ]
        assert all(member.isfile() or member.isdir() for member in members)
        assert {(member.uid, member.gid, member.uname, member.gname) for member in members} == {(0, 0, "", "")}
        assert capfd.readouterr().err == "emu-atlas-dev: not copied, being no file or directory: atlas/link.py\n"

    def test_its_floor_is_the_packages_and_its_manifest_name_the_dispatchers(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        called(capfd, "ask", "--target", "handheld", "detect")

        manifest = (there.root / "current" / dev_dispatch.MANIFEST).read_text(encoding="utf-8")
        assert json.loads(manifest) == {"requires_python": FLOOR}

    def test_the_target_holds_what_was_sent(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        called(capfd, "ask", "--target", "handheld", "detect")

        held = contents(without_caches(snapshot((there.root / "current").resolve())))
        assert held == {
            "atlas": None,
            "atlas/__init__.py": b"",
            "atlas/__main__.py": _SAYING_ATLAS["atlas/__main__.py"].encode(),
            "atlas/data": None,
            "atlas/data/table.json": b"{}\n",
            "scripts": None,
            "scripts/probes": None,
            "scripts/probes/say.py": _SAYING_ATLAS["scripts/probes/say.py"].encode(),
            "tree.json": json.dumps({"requires_python": FLOOR}).encode(),
        }

    def test_a_file_this_machine_cannot_read_ends_the_call_before_anything_is_contacted(
        self, there: There, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unreadable(tree: Path, floor: str) -> bytes:
            raise PermissionError(13, "Permission denied", str(tree / "atlas" / "__init__.py"))

        monkeypatch.setattr(ask, "build_copy", unreadable)
        logins = len(ssh_calls(there.world))

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == ask.NOT_ASKED
        assert out == ""
        assert err.startswith("emu-atlas-dev: the copy could not be built here: [Errno 13] Permission denied")
        assert err.endswith("Nothing was contacted.\n")
        assert len(ssh_calls(there.world)) == logins


class TestWhenTheTargetCannotBeUsed:
    """#810: each failure says why and how to repair it, ends with NOT_ASKED, and does nothing on the target."""

    def _not_asked(self, there: There, capfd: pytest.CaptureFixture[str]) -> str:
        there.save()
        before = snapshot(there.target_home)

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == ask.NOT_ASKED
        assert out == ""
        assert snapshot(there.target_home) == before
        return err

    def test_no_such_target(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        logins = len(ssh_calls(there.world))
        before = snapshot(there.target_home)

        status, out, err = called(capfd, "ask", "--target", "other", "detect")

        assert (status, out) == (ask.NOT_ASKED, "")
        assert err == (
            f"emu-atlas-dev: no target named other is set up here — {there.config} has no entry for it.\n"
            "  Set it up first:\n    mise run setup:remote-target other <destination>\n"
            "  Nothing was contacted.\n"
        )
        assert len(ssh_calls(there.world)) == logins
        assert snapshot(there.target_home) == before

    def test_no_ssh_on_this_machine(
        self, there: There, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        empty = tmp_path / "no-programs"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))

        err = self._not_asked(there, capfd)

        assert err == (
            "emu-atlas-dev: asking a target needs ssh, and this machine has none on its PATH.\n"
            "  Nothing was contacted.\n"
        )

    def test_no_config_at_all(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        there.config.unlink()

        status, _, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == ask.NOT_ASKED
        assert f"no target named handheld is set up here — there is no {there.config}.\n" in err

    def test_a_name_this_machine_cannot_resolve(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        there.world.targets = {_NEW_ADDRESS: there.target()}

        err = self._not_asked(there, capfd)

        assert err == (
            f"emu-atlas-dev: this machine cannot resolve {_ADDRESS}, where handheld's entry says it is.\n"
            "  Run the setup with the target's address, or a name the network resolves:\n"
            "    mise run setup:remote-target handheld player@<address>\n"
            "  Nothing was done on handheld.\n"
        )

    def test_no_answer_at_its_address(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        there.target().serving = False

        err = self._not_asked(there, capfd)

        assert err == (
            f"emu-atlas-dev: no SSH server answers at {_ADDRESS} port 22, where handheld's entry says it is.\n"
            "  handheld may be asleep: wake it and ask again. Or it has a new address: run the setup with that one:\n"
            "    mise run setup:remote-target handheld player@<new address>\n"
            "  Nothing was done on handheld.\n"
        )

    def test_a_host_key_that_is_not_the_recorded_one(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        there.target().host_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAnotherMachine"
        known_hosts = there.config.with_name("known_hosts")

        err = self._not_asked(there, capfd)

        assert err.startswith(
            f"emu-atlas-dev: the host key at {_ADDRESS} is not the one recorded for handheld — another machine"
            " may be answering at that address. No connection was made.\n"
        )
        assert f"ssh-keygen -R emu-atlas-dev.handheld -f {known_hosts}\n" in err
        assert err.endswith("  Nothing was done on handheld.\n")

    def test_a_host_key_this_machine_holds_no_record_of(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        there.config.with_name("known_hosts").write_text("", encoding="utf-8")

        err = self._not_asked(there, capfd)

        assert err == (
            "emu-atlas-dev: this machine holds no record of handheld's host key, so no connection was made.\n"
            "  Run the setup again, which shows the fingerprint and records it:\n"
            f"    mise run setup:remote-target handheld player@{_ADDRESS}\n"
            "  Nothing was done on handheld.\n"
        )

    def test_the_build_hosts_key_refused(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        there.target().client_address = "198.51.100.7"
        logged = len(log_lines(there.world))

        err = self._not_asked(there, capfd)

        assert err == (
            "emu-atlas-dev: handheld refused this machine's key. It takes the key only from the address it saw this"
            " machine at when it was set up, and that may have changed — or the key's line there was removed.\n"
            f"  Run the setup again:\n    mise run setup:remote-target handheld player@{_ADDRESS}\n"
            "  Nothing was done on handheld.\n"
        )
        assert log_lines(there.world)[logged:] == []

    def test_a_hop_that_fails_is_worded_as_the_setup_words_it(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        text = there.config.read_text(encoding="utf-8")
        there.config.write_text(
            text.replace("    Port 22\n", "    Port 22\n    ProxyJump jumper@203.0.113.5:22\n"), encoding="utf-8"
        )
        there.world.hop_failure = "refused"

        err = self._not_asked(there, capfd)

        assert err == (
            "emu-atlas-dev: no SSH server answers at the hop jumper@203.0.113.5:22.\n"
            "  Check that hop with your own ssh.\n"
            "  Nothing was done on handheld.\n"
        )


class TestTheDispatchersOwnStatuses:
    """What the dispatcher refuses or cannot run passes on with its status and its words, told apart from a run's."""

    def test_a_copy_the_target_does_not_take(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        (there.root / "trees").write_text("", encoding="utf-8")
        before = snapshot(there.target_home)

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == dev_dispatch.COPY_FAILED
        assert out == ""
        assert err.startswith("emu-atlas-dev: the copy could not start (")
        assert err.endswith(
            "the current tree is as it was.\n"
            "emu-atlas-dev: that was handheld's dispatcher (5, COPY_FAILED), not atlas or a probe.\n"
            "  Nothing was asked.\n"
        )
        assert snapshot(there.target_home) == before
        assert log_lines(there.world)[-1:] == [f"login {_ADDRESS} key"]

    def test_a_question_it_cannot_run(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        source = (there.root / "dispatch.py").read_text(encoding="utf-8")
        (there.root / "dispatch.py").write_text(
            source.replace(f"BWRAP = {str(there.tools / 'bwrap')!r}", f"BWRAP = {str(there.tools / 'bwrap-broken')!r}"),
            encoding="utf-8",
        )

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == dev_dispatch.CANNOT_RUN
        assert out == ""
        assert err == (
            f"emu-atlas-dev: no sandbox at {there.tools / 'bwrap-broken'} — it said: {BROKEN_BWRAP_MESSAGE};"
            " nothing ran.\n"
            "  To repair: install bubblewrap, or allow unprivileged user namespaces on this target.\n"
            "emu-atlas-dev: that was handheld's dispatcher (4, CANNOT_RUN), not atlas or a probe.\n"
        )

    def test_a_dispatcher_that_refuses_the_request(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        (there.root / "dispatch.py").write_text(
            "import os, sys\n"
            "sys.stderr.write('emu-atlas-dev: refused: %s\\n' % os.environ['SSH_ORIGINAL_COMMAND'])\n"
            "sys.exit(3)\n",
            encoding="utf-8",
        )
        before = snapshot(there.target_home)

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == dev_dispatch.REFUSED
        assert out == ""
        assert err == (
            "emu-atlas-dev: refused: copy\n"
            "emu-atlas-dev: that was handheld's dispatcher (3, REFUSED), not atlas or a probe.\n"
            "  The dispatcher there does not take this client's requests; run the setup again, which installs this"
            " checkout's dispatcher:\n"
            "    mise run setup:remote-target handheld <destination>\n"
            "  Nothing was asked.\n"
        )
        assert snapshot(there.target_home) == before

    def test_a_probes_own_failure_is_passed_on_as_it_is(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        status, _, err = called(capfd, "probe", "--target", "handheld", "say.py", "1")

        assert status == 1
        assert err == "the probe said this on stderr\n"

    def test_a_dispatcher_that_does_not_run_breaks_off_the_copy(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        (there.root / "dispatch.py").write_text("raise SystemExit('a dispatcher that broke')\n", encoding="utf-8")

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == ask.BROKE_OFF
        assert out == ""
        assert err == (
            "a dispatcher that broke\n"
            "emu-atlas-dev: the copy to handheld ended with 1 without a word from its dispatcher — the connection"
            " broke, or the dispatcher there did not run; what ssh said is above.\n"
            "  handheld's current tree is this copy whole, or the one before it. Nothing was asked.\n"
        )

    def test_a_connection_that_breaks_during_the_copy(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        there.world.drop_after_first_line = True
        there.save()

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == ask.BROKE_OFF
        assert out == ""
        assert err.startswith("client_loop: send disconnect: Connection reset by peer\n")
        assert "the copy to handheld ended with 255 without a word from its dispatcher" in err

    def test_a_run_that_ends_with_ssh_s_own_status_is_not_taken_for_an_answer(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        status, _, err = called(capfd, "probe", "--target", "handheld", "say.py", "255")

        assert status == ask.BROKE_OFF
        assert err == (
            "the probe said this on stderr\n"
            "emu-atlas-dev: ssh ended with 255 while the probe say.py ran on handheld: the connection broke, or the"
            " run itself ended with 255; what it printed is above.\n"
        )


def copy_then(there: There, monkeypatch: pytest.MonkeyPatch, after: Callable[[str], None]) -> list[str]:
    """The client's copy as it is, with *after* run between the copy and the question; the ids the copies named."""
    real = ask.copy_tree
    named: list[str] = []

    def copy_tree(target: ask.Target, phase: ask.Phase) -> str:
        tree = real(target, phase)
        named.append(tree)
        after(tree)
        return tree

    monkeypatch.setattr(ask, "copy_tree", copy_tree)
    return named


class TestBetweenTheCopyAndTheQuestion:
    """copy and the question are two connections; what happens on the target in between."""

    def test_another_clients_copy_in_between_ends_the_call_with_tree_gone(
        self, there: There, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The first client's question must not run on the second client's tree."""
        other = write_tree(tmp_path / "other tree", {**_SAYING_ATLAS, "atlas/other.py": ""})
        real_copy = ask.copy_tree
        others: list[str] = []

        def another_client_copies(_: str) -> None:
            monkeypatch.setattr(ask, "TREE", other)
            others.append(real_copy(ask.find_target("handheld"), ask.Phase()))
            monkeypatch.setattr(ask, "TREE", there.tree)

        named = copy_then(there, monkeypatch, another_client_copies)

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        (first,), (second,) = named, others
        assert status == dev_dispatch.TREE_GONE
        assert out == ""
        assert err == (
            f"emu-atlas-dev: the tree {first} is not here: a later copy replaced it, or it was never copied;"
            " nothing ran.\n  Ask again: every call copies first.\n"
            "emu-atlas-dev: that was handheld's dispatcher (8, TREE_GONE), not atlas or a probe.\n"
        )
        assert (there.root / "current").resolve().name == second
        assert ((there.root / "current").resolve() / "atlas" / "other.py").is_file()

    @pytest.mark.parametrize(
        ("mode", "argv", "what"),
        [("ask", ["detect"], "The question"), ("probe", ["say.py"], "The probe say.py")],
    )
    def test_a_target_that_stops_answering_after_the_copy_was_not_asked(
        self,
        there: There,
        capfd: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        mode: ask.Mode,
        argv: list[str],
        what: str,
    ) -> None:
        after_the_copy: list[dict[str, Entry]] = []

        def falls_asleep(_: str) -> None:
            there.target().serving = False
            there.save()
            after_the_copy.append(snapshot(there.target_home))

        copy_then(there, monkeypatch, falls_asleep)

        status, out, err = called(capfd, mode, "--target", "handheld", *argv)

        assert status == ask.NOT_ASKED
        assert out == ""
        assert err == (
            f"ssh: connect to host {_ADDRESS} port 22: Connection refused\n"
            f"emu-atlas-dev: no SSH server answers at {_ADDRESS} port 22, where handheld's entry says it is.\n"
            "  handheld may be asleep: wake it and ask again. Or it has a new address: run the setup with that one:\n"
            "    mise run setup:remote-target handheld player@<new address>\n"
            f"  {what} was not asked; the copy went through.\n"
        )
        assert snapshot(there.target_home) == after_the_copy[0]


class TestTheTargetsShell:
    """What the target's shell prints for a command without a terminal, and a dispatcher that names no tree."""

    def test_output_on_stdout_ends_the_call_before_a_question_is_asked(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        there.target().login_output = "Welcome back.\nLast login: never\n"
        there.save()
        logged = len(log_lines(there.world))

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == ask.NOT_ASKED
        assert out == ""
        assert err == (
            "emu-atlas-dev: handheld wrote this on stdout beside its dispatcher's answer to the copy:\n"
            "    'Welcome back.'\n"
            "    'Last login: never'\n"
            "  The shell there prints from its startup files for a command run without a terminal, and that would"
            " land in front of every answer. Make them silent for non-interactive shells (in ~/.bashrc, say, return"
            " at the top unless $- holds i).\n"
            "  The copy went through; nothing was asked.\n"
        )
        assert log_lines(there.world)[logged:] == [f"login {_ADDRESS} key"]

    def test_a_dispatcher_that_names_no_tree_is_older_than_the_client(
        self, there: There, capfd: pytest.CaptureFixture[str]
    ) -> None:
        (there.root / "dispatch.py").write_text("import sys\nsys.stdin.buffer.read()\n", encoding="utf-8")

        status, out, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == ask.NOT_ASKED
        assert out == ""
        assert err == (
            "emu-atlas-dev: handheld's dispatcher took the copy but named no tree to ask from.\n"
            "  The dispatcher there is older than this client; run the setup again, which installs this checkout's"
            " dispatcher:\n"
            f"    mise run setup:remote-target handheld player@{_ADDRESS}\n"
            "  The copy went through; nothing was asked.\n"
        )

    def test_output_and_no_tree_say_both(self, there: There, capfd: pytest.CaptureFixture[str]) -> None:
        (there.root / "dispatch.py").write_text("import sys\nsys.stdin.buffer.read()\n", encoding="utf-8")
        there.target().login_output = "hello\n"
        there.save()

        status, _, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert status == ask.NOT_ASKED
        assert "    'hello'\n" in err
        assert (
            "  It named no tree either.\n  The dispatcher there is older than this client; run the setup again, which"
            " installs this checkout's dispatcher:\n" in err
        )


class TestACrashOfTheClient:
    """No exception leaves the client with a status a probe or atlas uses."""

    @pytest.mark.parametrize(
        ("name", "phase", "status", "after"),
        [
            ("build_copy", "before anything was contacted", ask.NOT_ASKED, "Nothing was contacted."),
            ("run_ssh", "while copying to the target", ask.BROKE_OFF,
             "handheld's current tree is this copy whole, or the one before it. Nothing was asked."),
            ("tree_named", "after the copy went through", ask.NOT_ASKED, "The copy went through; nothing was asked."),
            ("judge", "while asking the target", ask.BROKE_OFF, "What the run printed is above; its status is lost."),
        ],
    )
    def test_a_crash_there_names_its_phase_and_ends_with_a_status_of_the_clients(
        self,
        there: There,
        capfd: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        phase: str,
        status: int,
        after: str,
    ) -> None:
        def crashes(*_: object) -> object:
            raise ValueError("an unforeseen value")

        monkeypatch.setattr(ask, name, crashes)

        code, _, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert code == status
        assert "Traceback (most recent call last):" in err
        assert "ValueError: an unforeseen value\n" in err
        assert err.endswith(f"emu-atlas-dev: this client failed {phase} (the traceback is above). {after}\n")

    def test_a_crash_here_ends_with_a_status_of_the_clients(
        self, here: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def crashes(*_: object) -> object:
            raise ValueError("an unforeseen value")

        monkeypatch.setattr(dev_dispatch, "status_of", crashes)

        code, _, err = called(capfd, "ask", "detect")

        assert code == ask.NOT_ASKED
        assert err.endswith(
            "emu-atlas-dev: this client failed while running here (the traceback is above). No target was contacted;"
            " whatever ran here printed above, and its status is lost.\n"
        )

    def test_an_ssh_that_cannot_be_started_is_named(
        self, there: There, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def not_permitted(*_: object, **__: object) -> object:
            raise PermissionError(13, "Permission denied", "ssh")

        monkeypatch.setattr(subprocess, "run", not_permitted)

        code, _, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert code == ask.NOT_ASKED
        assert err == "emu-atlas-dev: ssh could not be started: Permission denied.\n  Nothing was contacted.\n"


class TestTwoTargets:
    """Each target's question reaches its own entry's address, and only that target's home changes."""

    def test_each_question_reaches_its_own_target(
        self, there: There, capfd: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        console_home = tmp_path / "console"
        console_home.mkdir()
        there.world.targets[_CONSOLE] = Target(home=str(console_home), client_address=_SEEN_FROM)
        there.save()
        assert setup_remote_target.main(["console", f"player@{_CONSOLE}"]) == 0
        install_dispatcher(console_home, bwrap=str(there.tools / "bwrap"))
        capfd.readouterr()

        targets = (("handheld", there.target_home, console_home), ("console", console_home, there.target_home))
        for name, home, other in targets:
            logged = len(log_lines(there.world))
            other_before = snapshot(other)

            code, out, err = called(capfd, "ask", "--target", name, "detect")

            assert code == 0, err
            assert said(out)["file"].startswith(str(home / ROOT / "trees"))
            address = _ADDRESS if name == "handheld" else _CONSOLE
            assert log_lines(there.world)[logged:] == [f"login {address} key"] * 2
            assert snapshot(other) == other_before

    def test_a_failure_names_the_address_of_its_own_target(
        self, there: There, capfd: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """ssh reads each entry itself; the client's message reads the same entry back, not the next one."""
        console_home = tmp_path / "console"
        console_home.mkdir()
        there.world.targets[_CONSOLE] = Target(home=str(console_home), client_address=_SEEN_FROM)
        there.save()
        assert setup_remote_target.main(["console", f"player@{_CONSOLE}"]) == 0
        there.target().serving = False
        there.save()
        capfd.readouterr()

        code, _, err = called(capfd, "ask", "--target", "handheld", "detect")

        assert code == ask.NOT_ASKED
        assert err.startswith(f"emu-atlas-dev: no SSH server answers at {_ADDRESS} port 22, where handheld's entry")


class TestTheStatuses:
    def test_the_clients_own_are_none_atlas_or_the_dispatcher_ends_with(self) -> None:
        dispatcher = {dev_dispatch.REFUSED, dev_dispatch.CANNOT_RUN, dev_dispatch.COPY_FAILED, dev_dispatch.TREE_GONE}
        assert len(dispatcher) == 4
        assert ask.USAGE == 2
        assert {ask.NOT_ASKED, ask.BROKE_OFF}.isdisjoint({0, 1, 2} | dispatcher)
        assert ask.NOT_ASKED != ask.BROKE_OFF
        assert setup_remote_target.SSH_FAILED not in {ask.NOT_ASKED, ask.BROKE_OFF}

    def test_the_guide_lists_them(self) -> None:
        guide = (REPO / "docs" / "remote-target.md").read_text(encoding="utf-8")

        for name in ("USAGE", "NOT_ASKED", "BROKE_OFF"):
            assert f"- **{getattr(ask, name)} `{name}`** — " in guide, name
        assert f"- **{dev_dispatch.TREE_GONE} `TREE_GONE`** — " in guide

    def test_the_guide_has_the_section_the_client_points_to(self) -> None:
        guide = (REPO / "docs" / "remote-target.md").read_text(encoding="utf-8")
        headings = {line.lstrip("#").strip() for line in guide.splitlines() if line.startswith("#")}
        text = " ".join((REPO / "scripts" / "ask.py").read_text(encoding="utf-8").split())

        assert 'docs/remote-target.md, "Asking a target"' in text
        assert "Asking a target" in headings

    def test_claude_md_says_how_to_ask_a_set_up_installation(self) -> None:
        guide = (REPO / "CLAUDE.md").read_text(encoding="utf-8")

        assert "mise run ask --target" in guide
        assert "mise run probe --target" in guide
        assert "not-set-up" in guide


def _a_deployed_retrodeck() -> None:
    if not _RETRODECK.is_dir():
        pytest.skip(f"no RetroDECK is installed at {_RETRODECK}")


class TestTheFirstProbe:
    """``scripts/probes/answers_agree.py``: both forms of every content-free question, compared."""

    @staticmethod
    def asker(answers: dict[tuple[str, ...], tuple[int, Any]]) -> answers_agree.Asker:
        """Answers by command line, each a status and an answer; atlas says nothing on stderr."""
        return lambda argv: answers_agree.Asked(*answers[tuple(argv)], "")

    @staticmethod
    def every_question(
        installations: list[dict[str, str]], labelled: object, bare: object, *, kind: str = "retrodeck"
    ) -> dict[tuple[str, ...], tuple[int, Any]]:
        answers: dict[tuple[str, ...], tuple[int, Any]] = {("detect",): (0, installations)}
        for question in answers_agree.QUESTIONS:
            answers[(question,)] = (0, labelled)
            answers[(question, "--installation", kind)] = (0, bare)
        return answers

    def _one_retrodeck(self, labelled: object, bare: object) -> answers_agree.Asker:
        installation = {"kind": "retrodeck", "label": "RetroDECK"}
        answers = self.every_question([installation], [{"installation": installation, "answer": {"same": [1, 2]}}],
                                      {"same": [1, 2]})
        answers[("systems",)] = (0, [{"installation": installation, "answer": labelled}])
        answers[("systems", "--installation", "retrodeck")] = (0, bare)
        return self.asker(answers)

    def test_answers_that_agree_end_with_0(self) -> None:
        status, lines = answers_agree.compare(self._one_retrodeck({"a": 1}, {"a": 1}))

        assert status == answers_agree.AGREE == 0
        assert lines == [f"{question} retrodeck: agree" for question in answers_agree.QUESTIONS]

    def test_a_disagreement_names_the_question_the_kind_and_where_the_answers_part(self) -> None:
        labelled = {"systems": [{"name": "a", "roms": "LEFT-VALUE"}, {"name": "b"}], "caveats": []}
        bare = {"systems": [{"name": "a", "roms": "RIGHT-VALUE"}, {"name": "b", "extra": 1}], "caveats": []}

        status, lines = answers_agree.compare(self._one_retrodeck(labelled, bare))

        assert status == answers_agree.DISAGREE == 1
        assert "systems retrodeck: differ at .systems[0].roms, .systems[1].extra (in one only)" in lines
        assert "VALUE" not in " ".join(lines)

    def test_a_key_that_is_no_field_name_is_not_printed(self) -> None:
        """A key can be a path or a title off the device: only a plain field name is shown."""
        labelled = {"options": {"/run/media/card/A Game (Europe).chd": 1, "Some Title": 1, "core_option": 1}}
        bare = {"options": {"/run/media/card/A Game (Europe).chd": 2, "Some Title": 2, "core_option": 2}}

        status, lines = answers_agree.compare(self._one_retrodeck(labelled, bare))

        assert status == answers_agree.DISAGREE
        assert "systems retrodeck: differ at .options.<key>, .options.<key>, .options.core_option" in lines
        assert "Game" not in " ".join(lines) and "Title" not in " ".join(lines)

    def test_a_long_list_of_places_is_cut_and_counted(self) -> None:
        status, lines = answers_agree.compare(self._one_retrodeck(list(range(8)), list(range(1, 9))))

        assert status == 1
        assert "systems retrodeck: differ at [0], [1], [2], [3], [4] and 3 more" in lines

    def test_answers_of_another_type_or_length_differ_as_a_whole(self) -> None:
        assert answers_agree.differences([1], {"a": 1}) == [""]
        assert answers_agree.differences({"a": [1]}, {"a": [1, 2]}) == [".a[] (lengths)"]
        assert answers_agree.verdict("health", "retrodeck", [""]) == "health retrodeck: differ at (the whole answer)"

    def test_a_question_atlas_refuses_with_installation_could_not_be_put_and_says_why(self) -> None:
        installation = {"kind": "retrodeck"}
        answers = self.every_question([installation], [{"installation": installation, "answer": {}}], None)
        refused = {key: (2, None) for key in answers if len(key) == 3}

        def ask(argv: list[str]) -> answers_agree.Asked:
            if tuple(argv) in refused:
                return answers_agree.Asked(2, None, "no detected installation of kind 'retrodeck'")
            return answers_agree.Asked(*answers[tuple(argv)], "")

        status, lines = answers_agree.compare(ask)

        assert status == answers_agree.NOT_PUT == 2
        assert (
            "health retrodeck with --installation: could not be put (atlas ended with 2: no detected installation of"
            " kind 'retrodeck')" in lines
        )

    def test_a_question_atlas_refuses_for_every_installation_could_not_be_put(self) -> None:
        installation = {"kind": "retrodeck"}
        answers = self.every_question([installation], [{"installation": installation, "answer": {}}], {})
        answers[("systems",)] = (2, None)

        status, lines = answers_agree.compare(self.asker(answers))

        assert status == answers_agree.NOT_PUT
        assert "systems put to every installation: could not be put (atlas ended with 2)" in lines
        assert "health retrodeck: agree" in lines

    def test_detect_refused_could_not_be_put(self) -> None:
        status, lines = answers_agree.compare(self.asker({("detect",): (2, None)}))

        assert (status, lines) == (answers_agree.NOT_PUT, ["detect: could not be put (atlas ended with 2)"])

    def test_a_second_installation_of_a_kind_is_named_and_left_out(self) -> None:
        first, second = {"kind": "emudeck", "root": "1"}, {"kind": "emudeck", "root": "2"}
        labelled = [{"installation": first, "answer": {"n": 1}}, {"installation": second, "answer": {"n": 2}}]
        answers = self.every_question([first, second], labelled, {"n": 1}, kind="emudeck")

        status, lines = answers_agree.compare(self.asker(answers))

        assert status == 0
        assert lines[0] == (
            "emudeck: 1 more installation(s) of this kind left out; --installation answers for the first only"
        )

    def test_a_kind_the_labelled_answer_leaves_out_is_a_disagreement(self) -> None:
        """The owner's ruling: detect reported the kind, the answer for every installation left it out."""
        answers = self.every_question([{"kind": "retrodeck"}], [], {})

        status, lines = answers_agree.compare(self.asker(answers))

        assert status == answers_agree.DISAGREE
        assert "health retrodeck: the answer put to every installation holds none of this kind" in lines

    @pytest.mark.parametrize("labelled", [{"not": "a list"}, [{"answer": {}}], ["text"]])
    def test_an_answer_whose_shape_cannot_be_read_could_not_be_put(self, labelled: object) -> None:
        answers = self.every_question([{"kind": "retrodeck"}], labelled, {})

        status, lines = answers_agree.compare(self.asker(answers))

        assert status == answers_agree.NOT_PUT
        assert f"health: {answers_agree.UNREADABLE}" in lines

    def test_a_question_whose_comparison_crashes_keeps_the_verdicts_before_and_after_it(
        self, capfd: pytest.CaptureFixture[str]
    ) -> None:
        installation = {"kind": "retrodeck"}
        answers = self.every_question([installation], [{"installation": installation, "answer": {}}], {})

        def ask(argv: list[str]) -> answers_agree.Asked:
            if argv == ["systems"]:
                raise RuntimeError("atlas broke")
            return answers_agree.Asked(*answers[tuple(argv)], "")

        status, lines = answers_agree.compare(ask)

        assert status == answers_agree.NOT_PUT == 2
        assert lines == [
            "health retrodeck: agree",
            "systems: could not be put (the comparison crashed: RuntimeError)",
            "rom-locations retrodeck: agree",
            "firmware-inventory retrodeck: agree",
        ]
        err = capfd.readouterr().err
        assert "Traceback (most recent call last):" in err
        assert err.endswith("RuntimeError: atlas broke\n")

    def test_no_installation_is_nothing_to_compare(self) -> None:
        nothing = answers_agree.compare(self.asker({("detect",): (0, [])}))

        assert nothing == (0, ["no installation detected; nothing to compare"])

    def test_it_takes_no_arguments(self, capfd: pytest.CaptureFixture[str]) -> None:
        assert answers_agree.main(["--home", "x"]) == answers_agree.NOT_PUT
        assert capfd.readouterr().err == "answers_agree.py takes no arguments, not ['--home', 'x']\n"

    @pytest.mark.parametrize(
        ("ends", "out", "asked"),
        [
            (SystemExit(2), "", (2, None, "usage: emu-atlas")),
            (SystemExit(None), "[]", (0, [], "usage: emu-atlas")),
            (SystemExit("text"), "", (1, None, "usage: emu-atlas")),
            (0, "not json", (2, None, "its answer is no JSON")),
            (0, '{"a": 1}', (0, {"a": 1}, "usage: emu-atlas")),
        ],
        ids=["exit 2", "exit None", "exit with text", "no JSON", "an answer"],
    )
    def test_ask_atlas_reads_what_atlas_did(
        self, monkeypatch: pytest.MonkeyPatch, ends: object, out: str, asked: tuple[int, Any, str]
    ) -> None:
        import atlas.cli

        def run(argv: list[str]) -> int:
            print(out, end="")
            print("usage: emu-atlas\nmore", file=sys.stderr)
            if isinstance(ends, SystemExit):
                raise ends
            assert isinstance(ends, int)
            return ends

        monkeypatch.setattr(atlas.cli, "run", run)

        assert answers_agree.ask_atlas(["detect"]) == answers_agree.Asked(*asked)

    def test_it_reports_through_the_client_here(
        self, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Against whatever this machine has installed: the probe's own run, here."""
        _a_deployed_retrodeck()
        monkeypatch.setattr(ask, "TREE", REPO)

        status, out, err = called(capfd, "probe", "answers_agree.py")

        assert status == 0, out + err
        assert out.splitlines()[-1] == "answers_agree: every answer agrees."
        assert any(line.endswith("retrodeck: agree") for line in out.splitlines())

    def test_it_runs_in_the_dispatchers_real_sandbox_and_answers_as_here(
        self, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """atlas asked in the probe's own process, under the dispatcher's bootstrap, through ``/usr/bin/bwrap``."""
        _a_deployed_retrodeck()
        installed = sandbox_home / ROOT / "dispatch.py"
        installed.parent.mkdir(parents=True)
        installed.write_text(DISPATCHER.read_text(encoding="utf-8"), encoding="utf-8")
        env = {"HOME": str(sandbox_home), "PATH": os.environ["PATH"]}
        stream = ask.build_copy(REPO, FLOOR)
        copied = subprocess.run(
            [sys.executable, "-I", str(installed)], input=stream, capture_output=True, check=False,
            env={**env, "SSH_ORIGINAL_COMMAND": "copy"}, cwd=sandbox_home,
        )
        assert copied.returncode == 0, copied.stderr
        monkeypatch.setattr(ask, "TREE", REPO)
        monkeypatch.setenv("HOME", str(sandbox_home))
        here_status, here_out, _ = called(capfd, "probe", "answers_agree.py")

        result = subprocess.run(
            [sys.executable, "-I", str(installed)], capture_output=True, text=True, check=False,
            env={**env, "SSH_ORIGINAL_COMMAND": f"probe {copied.stdout.decode().strip()} answers_agree.py"},
            cwd=sandbox_home,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        marker = dev_dispatch.RUN_MARKER + "\n"
        assert result.stdout.startswith(marker)
        assert (result.returncode, result.stdout.removeprefix(marker)) == (here_status, here_out)
