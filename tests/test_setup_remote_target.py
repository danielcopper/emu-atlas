"""Tests for ``scripts/setup_remote_target.py`` — ``mise run setup:remote-target``.

The script runs through its ``main``. Every program it starts is a stand-in
from ``tests/remote_target.py`` on a ``PATH`` that holds nothing else, so no
case runs a real ``ssh``, ``ssh-keygen`` or ``ip``, or contacts anything. The
build host is the test's ``build`` directory; the target is a directory of its
own, which the ``ssh`` stand-in logs into and whose real ``authorized_keys`` it
honours — so a second run with the build host's key really goes through the
``from=`` and the dispatcher the first run installed.

The target's ``/usr/bin/python3`` is this interpreter and its
``/usr/bin/bwrap`` the ``bwrap`` stand-in, which runs what it is handed with no
sandbox: the setup only checks that one starts. ``HOME``, ``XDG_CONFIG_HOME``
and ``PATH`` are moved for the whole module (``isolated_module``), and each
test moves ``HOME`` again to its own build directory. This process and each
stand-in guard the real home as ``tests/remote_target.py`` describes, with
what that leaves out. The install script and the dispatcher carry no such
hook: the ``ssh``
stand-in starts them with ``HOME`` set to the target's own directory under the
test's ``tmp_path``, and that is what keeps them there.

"Both machines as they were" is checked as every file, directory and link
under both homes, with its bytes and its mode, before and after.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import dev_dispatch, setup_remote_target
from tests.remote_target import (
    AGENT,
    BROKEN_BWRAP_MESSAGE,
    GUARDED,
    GUARDED_VARIABLE,
    NOTHING,
    OLD_PYTHON_VERSION,
    PASSWORD,
    WORLD_VARIABLE,
    OwnAccess,
    Target,
    World,
    fake_path,
    guard_real_home,
    log_lines,
    ssh_calls,
)
from tests.remote_target_suite import (
    DISPATCHER,
    FLOOR,
    INSTALL_SCRIPT,
    REPO,
    ROOT,
    isolated_module,
    snapshot,
)

pytestmark = pytest.mark.usefixtures(isolated_module.__name__)

_ADDRESS = "192.0.2.20"
_NEW_ADDRESS = "192.0.2.30"
# Where the target sees the build host — deliberately not the address the build
# host's route names (World.route_source), so a test can tell which one a line got.
_SEEN_FROM = "198.51.100.6"
_MOVED_FROM = "198.51.100.7"


def _host_key(seed: int) -> str:
    """An ed25519 public key line in OpenSSH's format, its 32 key bytes made from *seed*."""
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes((seed + index) % 256 for index in range(32))
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


_HOST_KEY = _host_key(1)
_BUILD_HOST = "buildhost"


@dataclass
class Remote:
    world: World
    world_file: Path
    tools: Path
    target_home: Path
    build_home: Path
    host_keys: Path

    def save(self) -> None:
        self.world.save(self.world_file)

    def target(self, address: str = _ADDRESS) -> Target:
        return self.world.targets[address]

    def move_target(self, address: str) -> None:
        self.world.targets = {address: next(iter(self.world.targets.values()))}
        self.save()

    def setup(self, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
        self.save()
        code = setup_remote_target.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    def run_on_target(self, script: str, *argv: str) -> subprocess.CompletedProcess[str]:
        """Run a printed script on the target, as its user would in a terminal there: no ``SSH_CLIENT``."""
        path = self.target_home / "setup.py"
        path.write_text(script, encoding="utf-8")
        env = {
            "HOME": str(self.target_home),
            "PATH": str(self.tools),
            WORLD_VARIABLE: str(self.world_file),
            GUARDED_VARIABLE: os.environ[GUARDED_VARIABLE],
        }
        result = subprocess.run(
            [sys.executable, str(path), *argv], cwd=self.target_home, capture_output=True, text=True, check=False, env=env
        )
        path.unlink()
        return result

    @property
    def dev_dir(self) -> Path:
        return self.build_home / ".config" / "emu-atlas-dev"

    @property
    def authorized_keys(self) -> Path:
        return self.target_home / ".ssh" / "authorized_keys"

    @property
    def root(self) -> Path:
        return self.target_home / ROOT

    def key_lines(self) -> list[str]:
        return [line for line in self.authorized_keys.read_text(encoding="utf-8").splitlines() if line]

    def public_key(self) -> str:
        return (self.dev_dir / "id_ed25519.pub").read_text(encoding="utf-8").strip()

    def config(self) -> str:
        return (self.dev_dir / "ssh_config").read_text(encoding="utf-8")

    def dispatcher_line(self, from_address: str) -> str:
        command = f"{shlex.quote(sys.executable)} -I {shlex.quote(str(self.root / 'dispatch.py'))}"
        return f'from="{from_address}",command="{command}",restrict {self.public_key()}'


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Remote:
    tools = Path(fake_path(tmp_path))
    build_home = tmp_path / "build"
    build_home.mkdir()
    target_home = tmp_path / "target"
    target_home.mkdir()
    host_keys = tmp_path / "etc-ssh"
    host_keys.mkdir()
    (host_keys / "ssh_host_ed25519_key.pub").write_text(f"{_HOST_KEY} root@handheld\n", encoding="utf-8")
    world = World(
        targets={_ADDRESS: Target(home=str(target_home), client_address=_SEEN_FROM, host_key=_HOST_KEY)},
        log=str(tmp_path / "log"),
    )
    monkeypatch.setenv("HOME", str(build_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(build_home / ".config"))
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setenv(WORLD_VARIABLE, str(tmp_path / "world.json"))
    monkeypatch.setattr(setup_remote_target, "HOST_KEY_DIR", str(host_keys))
    monkeypatch.setattr(setup_remote_target, "TARGET_PYTHON", sys.executable)
    monkeypatch.setattr(setup_remote_target, "TARGET_BWRAP", str(tools / "bwrap"))
    monkeypatch.setattr(setup_remote_target, "build_host_name", lambda: _BUILD_HOST)
    return Remote(world, tmp_path / "world.json", tools, target_home, build_home, host_keys)


def fails(remote: Remote, capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    """The setup run with *argv*, failing, with nothing on stdout and both machines as they were; its message."""
    build_before = snapshot(remote.build_home)
    target_before = snapshot(remote.target_home)

    code, out, err = remote.setup(capsys, *argv)

    assert code == 1
    assert out == ""
    assert snapshot(remote.build_home) == build_before
    assert snapshot(remote.target_home) == target_before
    return err


def fingerprint_of(host_key: str) -> str:
    """What ``ssh`` shows for *host_key* at first contact, worked out independently of the script."""
    digest = hashlib.sha256(base64.b64decode(host_key.split()[1])).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class TestTheSetupLogin:
    def test_a_first_run_installs_the_dispatcher_and_the_one_restricted_line(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert sorted(path.name for path in remote.root.iterdir()) == ["dispatch.py"]
        assert (remote.root / "dispatch.py").read_bytes() == DISPATCHER.read_bytes()
        assert (remote.root / "dispatch.py").stat().st_mode & 0o077 == 0
        comment, line = remote.key_lines()
        assert comment.startswith(f"# emu-atlas-dev@{_BUILD_HOST}: written by setup:remote-target from {_BUILD_HOST}, ")
        assert comment.endswith(". Running it again changes it.")
        assert line == remote.dispatcher_line(_SEEN_FROM)
        assert remote.authorized_keys.stat().st_mode & 0o077 == 0
        assert log_lines(remote.world) == [
            "host key shown emu-atlas-dev.handheld",
            f"password asked {_ADDRESS}",
            f"login {_ADDRESS} own",
        ]

    def test_the_line_restricts_the_key_to_the_address_the_target_sees(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#812 D1: the address ``SSH_CLIENT`` shows, not the one this machine's route names."""
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert remote.key_lines()[1].startswith(f'from="{_SEEN_FROM}",')
        assert f"from {_SEEN_FROM} only" in err

    def test_the_setup_request_is_the_targets_interpreter_reading_the_install_script_from_stdin(self) -> None:
        assert setup_remote_target.setup_request() == ["/usr/bin/python3", "-I", "-"]

    def test_it_writes_the_target_into_its_own_config_and_known_hosts(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        directory = remote.dev_dir
        lines = remote.config().splitlines()
        entry = lines[lines.index("Host handheld") :]
        assert lines[lines.index("Host handheld") - 1].startswith(
            f"# handheld: written by setup:remote-target on {_BUILD_HOST}, "
        )
        assert entry == [
            "Host handheld",
            f"    HostName {_ADDRESS}",
            "    Port 22",
            "    User player",
            "    HostKeyAlias emu-atlas-dev.handheld",
            f'    IdentityFile "{directory / "id_ed25519"}"',
            "    IdentitiesOnly yes",
            f'    UserKnownHostsFile "{directory / "known_hosts"}"',
            "    GlobalKnownHostsFile /dev/null",
            "    StrictHostKeyChecking yes",
            "    BatchMode yes",
        ]
        assert (directory / "known_hosts").read_text(encoding="utf-8") == f"emu-atlas-dev.handheld {_HOST_KEY}\n"
        assert (directory / "id_ed25519").stat().st_mode & 0o077 == 0
        assert (directory / "ssh_config").stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o077 == 0
        assert not (remote.build_home / ".ssh").exists()

    def test_it_honours_xdg_config_home(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "elsewhere"))

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert (tmp_path / "elsewhere" / "emu-atlas-dev" / "ssh_config").is_file()
        assert not remote.dev_dir.exists()

    def test_an_alias_from_the_users_config_is_written_resolved_with_its_jump_host(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.configs = {
            "handheld-alias": {"user": ["player"], "hostname": [_ADDRESS], "port": ["2222"], "proxyjump": ["bastion"]},
            "bastion": {"user": ["jumper"], "hostname": ["203.0.113.5"], "port": ["22022"]},
        }

        code, _, err = remote.setup(capsys, "handheld", "handheld-alias")

        assert code == 0, err
        assert f"    HostName {_ADDRESS}\n    Port 2222\n    User player\n" in remote.config()
        assert "    ProxyJump jumper@203.0.113.5:22022\n" in remote.config()

    @pytest.mark.parametrize(
        ("hop", "written"),
        [
            ("bastion:2222", "jumper@203.0.113.5:2222"),
            ("ops@bastion:2222", "ops@203.0.113.5:2222"),
            ("ssh://bastion:2222", "jumper@203.0.113.5:2222"),
            ("[2001:db8::5]:2200", "builder@[2001:db8::5]:2200"),
            ("ops@[2001:db8::5]", "ops@[2001:db8::5]:22"),
            ("bastion,ops@[2001:db8::5]:2200", "jumper@203.0.113.5:22022,ops@[2001:db8::5]:2200"),
        ],
    )
    def test_each_hop_is_resolved_with_its_own_user_and_port(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], hop: str, written: str
    ) -> None:
        remote.world.configs = {
            "handheld": {"user": ["player"], "hostname": [_ADDRESS], "proxyjump": [hop]},
            "bastion": {"user": ["jumper"], "hostname": ["203.0.113.5"], "port": ["22022"]},
        }

        code, _, err = remote.setup(capsys, "handheld", "handheld")

        assert code == 0, err
        assert f"    ProxyJump {written}\n" in remote.config()

    def test_a_proxy_command_from_the_users_config_is_written_into_the_entry(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        proxy = "nc -X connect -x proxy.example:3128 %h %p"
        remote.world.configs = {"handheld": {"user": ["player"], "hostname": [_ADDRESS], "proxycommand": [proxy]}}

        code, _, err = remote.setup(capsys, "handheld", "handheld")

        assert code == 0, err
        assert f"    User player\n    ProxyCommand {proxy}\n    HostKeyAlias" in remote.config()

    def test_first_contact_asks_even_where_the_users_config_checks_strictly(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.configs = {
            "handheld": {"user": ["player"], "hostname": [_ADDRESS], "stricthostkeychecking": ["yes"]}
        }

        code, _, err = remote.setup(capsys, "handheld", "handheld")

        assert code == 0, err
        assert log_lines(remote.world)[0] == "host key shown emu-atlas-dev.handheld"
        assert "first contact with handheld:" in err

    def test_the_users_own_login_asks_for_a_password_where_the_users_config_says_batch_mode(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.configs = {"handheld": {"user": ["player"], "hostname": [_ADDRESS], "batchmode": ["yes"]}}

        code, _, err = remote.setup(capsys, "handheld", "handheld")

        assert code == 0, err
        assert f"password asked {_ADDRESS}" in log_lines(remote.world)

    def test_other_lines_of_authorized_keys_stay_and_an_old_line_of_this_build_host_goes(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#812 D2: a romm-tender line, or any other, stays byte for byte."""
        comment = f"emu-atlas-dev@{_BUILD_HOST}"
        remote.authorized_keys.parent.mkdir()
        others = [
            "ssh-ed25519 AAAAothersKey someone@laptop",
            "# romm-tender-dev@buildhost: written by setup:remote-target from buildhost. Running it again changes it.",
            'from="198.51.100.6",command="/x/dispatch.sh",restrict ssh-ed25519 AAAAtheOtherTool romm-tender-dev@buildhost',
            "ssh-ed25519 AAAAanotherOne other@desktop\t ",
            f"# {comment}x: written by setup:remote-target from elsewhere. Running it again changes it.",
            f'from="198.51.100.2",command="/y",restrict ssh-ed25519 AAAAanotherBuildHost {comment}x',
        ]
        remote.authorized_keys.write_text(
            f"{others[0]}\n"
            f"# {comment}: written by setup:remote-target from somewhere, long ago. Running it again changes it.\n"
            f'from="198.51.100.1",command="/x",restrict ssh-ed25519 AAAAanOldKeyOfThisHost {comment}\n'
            + "".join(f"{line}\n" for line in others[1:]),
            encoding="utf-8",
        )

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        lines = remote.authorized_keys.read_text(encoding="utf-8").splitlines()
        assert lines[:6] == others
        assert lines[7] == remote.dispatcher_line(_SEEN_FROM)
        assert len(lines) == 8

    def test_a_last_line_without_its_newline_keeps_its_bytes(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.write_bytes(b"ssh-ed25519 AAAAothersKey someone@laptop")

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert remote.authorized_keys.read_bytes().startswith(b"ssh-ed25519 AAAAothersKey someone@laptop\n# ")

    def test_an_unrestricted_line_of_the_same_key_goes(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A line that would let the key past the dispatcher, however it got there."""
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        blob = remote.public_key().split()[1]
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.write_text(f"ssh-ed25519 {blob} copied-by-hand\n", encoding="utf-8")
        remote.target().own_access = AGENT

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert [line for line in remote.key_lines() if blob in line] == [remote.dispatcher_line(_SEEN_FROM)]

    def test_a_noisy_login_file_is_not_taken_for_ssh(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        """The remote shell's stderr is ssh's stderr: a refusal it prints is no refusal of the key."""
        first, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        assert first == 0, err
        remote.target().login_noise = "player@handheld: Permission denied (publickey).\n"
        logged = len(log_lines(remote.world))

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert log_lines(remote.world)[logged:] == [f"login {_ADDRESS} key"]

    @pytest.mark.parametrize("access", [PASSWORD, "key"])
    def test_a_login_file_that_prints_no_utf_8_does_not_end_the_setup(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], access: str
    ) -> None:
        """The target's login files and motd write what they like: one byte that is no UTF-8 among it."""
        if access == "key":
            first, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
            assert first == 0, err
        remote.target().login_noise = "Welcome to caf\udce9 handheld\n"

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert "Host handheld\n" in remote.config()

    def test_a_failure_quotes_what_the_login_printed_with_the_byte_replaced(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        remote.target().login_noise = "caf\udce9\n"
        monkeypatch.setattr(setup_remote_target, "TARGET_PYTHON", str(tmp_path / "no-python3"))

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert "caf\ufffd" in err

    def test_a_noisy_login_file_under_a_failing_forced_command_blames_no_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Only ssh's own failure status makes a ``Permission denied (`` on stderr a refusal of the key."""
        first, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        assert first == 0, err
        (remote.root / "dispatch.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        remote.target().login_noise = "player@handheld: Permission denied (publickey).\n"
        remote.target().own_access = NOTHING

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert "handheld accepted this machine's key, but its forced command failed (ssh ended with 3)" in err
        assert "refused this machine's key" not in err


class TestRunAgain:
    @pytest.mark.parametrize("own_access", [AGENT, PASSWORD])
    def test_run_twice_it_leaves_one_key_one_line_and_one_entry(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], own_access: OwnAccess
    ) -> None:
        remote.target().own_access = own_access
        first, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        assert first == 0, err
        key = remote.public_key()

        second, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert second == 0, err
        assert remote.public_key() == key
        assert sorted(p.name for p in remote.dev_dir.iterdir()) == [
            "id_ed25519",
            "id_ed25519.pub",
            "known_hosts",
            "ssh_config",
        ]
        assert len(remote.key_lines()) == 2
        assert remote.key_lines()[1] == remote.dispatcher_line(_SEEN_FROM)
        assert sorted(path.name for path in remote.root.iterdir()) == ["dispatch.py"]
        assert remote.config().count("Host handheld\n") == 1
        assert len((remote.dev_dir / "known_hosts").read_text(encoding="utf-8").splitlines()) == 1

    def test_a_second_run_after_the_targets_address_changed_needs_no_password(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        first, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        assert first == 0, err
        remote.move_target(_NEW_ADDRESS)
        logged = len(log_lines(remote.world))

        second, _, err = remote.setup(capsys, "handheld", f"player@{_NEW_ADDRESS}")

        assert second == 0, err
        assert log_lines(remote.world)[logged:] == [f"login {_NEW_ADDRESS} key"]
        assert f"    HostName {_NEW_ADDRESS}\n" in remote.config()
        assert _ADDRESS not in remote.config()
        assert remote.config().count("Host handheld\n") == 1
        assert len(remote.key_lines()) == 2

    def test_a_rerun_repairs_the_target_through_the_key_without_a_password(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#810 D8: a user whose own login is a password repairs the target with the build host's key."""
        first, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        assert first == 0, err
        with open(remote.root / "dispatch.py", "a", encoding="utf-8") as dispatcher:
            dispatcher.write("# edited on the target\n")
        remote.target().client_address = _MOVED_FROM
        remote.authorized_keys.write_text(
            remote.authorized_keys.read_text(encoding="utf-8").replace(_SEEN_FROM, _MOVED_FROM), encoding="utf-8"
        )
        logged = len(log_lines(remote.world))

        second, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert second == 0, err
        assert log_lines(remote.world)[logged:] == [f"login {_ADDRESS} key"]
        assert (remote.root / "dispatch.py").read_bytes() == DISPATCHER.read_bytes()
        assert remote.key_lines()[1] == remote.dispatcher_line(_MOVED_FROM)
        assert "is set up" in err

    def test_an_unchanged_entry_keeps_its_comment(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        config = remote.config().replace("written by setup:remote-target on", "written by hand on")
        (remote.dev_dir / "ssh_config").write_text(config, encoding="utf-8")

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert remote.config() == config

    def test_a_second_target_gets_an_entry_beside_the_first(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.targets[_NEW_ADDRESS] = Target(home=str(remote.target_home), client_address=_SEEN_FROM)
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        code, _, err = remote.setup(capsys, "other", f"player@{_NEW_ADDRESS}")

        assert code == 0, err
        assert remote.config().count("Host handheld\n") == 1
        assert remote.config().count("Host other\n") == 1


class TestNoPasswordLogin:
    def test_it_is_set_up_through_an_existing_key_of_the_users(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = AGENT

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert remote.key_lines()[1] == remote.dispatcher_line(_SEEN_FROM)
        assert not any(line.startswith("password asked") for line in log_lines(remote.world))

    def test_it_is_set_up_through_print_and_the_second_run(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = NOTHING
        remote.target().client_address = remote.world.route_source

        code, script, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        assert code == 0, err
        assert f"It needs Python {FLOOR} or newer at {sys.executable} there" in err
        assert log_lines(remote.world) == []
        assert sorted(p.name for p in remote.dev_dir.iterdir()) == ["id_ed25519", "id_ed25519.pub"]
        on_target = remote.run_on_target(script)
        assert on_target.returncode == 0, on_target.stderr
        assert f"accepted from {remote.world.route_source} only" in on_target.stderr
        assert f"  {fingerprint_of(_HOST_KEY)} (ssh-ed25519)\n" in on_target.stdout
        assert remote.key_lines()[1] == remote.dispatcher_line(remote.world.route_source)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert log_lines(remote.world) == ["host key shown emu-atlas-dev.handheld", f"login {_ADDRESS} key"]
        assert "Host handheld\n" in remote.config()
        assert (remote.dev_dir / "known_hosts").read_text(encoding="utf-8") == f"emu-atlas-dev.handheld {_HOST_KEY}\n"

    def test_the_printed_script_takes_another_address_as_its_argument(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script, "198.51.100.99")

        assert on_target.returncode == 0, on_target.stderr
        assert "accepted from 198.51.100.99 only" in on_target.stderr
        assert remote.key_lines()[1] == remote.dispatcher_line("198.51.100.99")

    def test_the_printed_script_run_twice_leaves_one_line(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        remote.run_on_target(script)
        on_target = remote.run_on_target(script, "198.51.100.99")

        assert on_target.returncode == 0, on_target.stderr
        assert remote.key_lines()[1:] == [remote.dispatcher_line("198.51.100.99")]
        assert len(remote.key_lines()) == 2

    def test_a_refused_key_after_print_names_the_printed_address_as_the_likely_cause(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#812 D1: the target sees the build host at another address than its route names."""
        remote.target().own_access = NOTHING
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        remote.run_on_target(script)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert "The target refused this machine's key as well." in err
        assert (
            "the likely cause is the address that script restricts the key to — by default"
            f" {remote.world.route_source}" in err
        )

    @pytest.mark.parametrize(
        "hop", [{"proxyjump": ["bastion"]}, {"proxycommand": ["nc %h %p"]}], ids=["jump", "proxy"]
    )
    def test_print_refuses_a_hop_and_writes_nothing(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], hop: dict[str, list[str]]
    ) -> None:
        remote.world.configs = {"handheld": {"user": ["player"], "hostname": [_ADDRESS], **hop}}

        code, script, err = remote.setup(capsys, "handheld", "handheld", "--print")

        assert code == 1
        assert script == ""
        assert f"--print cannot set up a target reached through {next(iter(hop.values()))[0]}" in err
        assert snapshot(remote.build_home) == {}

    def test_print_keeps_an_existing_key(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        public_key = remote.public_key()

        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        assert remote.public_key() == public_key
        assert public_key in script


class TestTheLoginFailures:
    """Each ends with its message, and leaves both machines as they were."""

    def _fails(self, remote: Remote, capsys: pytest.CaptureFixture[str], destination: str) -> str:
        err = fails(remote, capsys, "handheld", destination)

        assert err.endswith("Nothing was changed, here or on the target.\n")
        return err

    def test_a_name_this_machine_cannot_resolve(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        err = self._fails(remote, capsys, "player@handheld.invalid")

        assert "this machine cannot resolve handheld.invalid" in err
        assert "Give the target's address instead, or a name the network resolves." in err

    def test_no_ssh_server_answering(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.target().serving = False

        err = self._fails(remote, capsys, f"player@{_ADDRESS}")

        assert f"no SSH server answers at {_ADDRESS} port 22" in err
        assert "that the target runs an SSH server" in err

    def test_no_login_accepted(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.target().own_access = NOTHING

        err = self._fails(remote, capsys, f"player@{_ADDRESS}")

        assert f"player@{_ADDRESS} accepted no login from this machine" in err
        assert "Run the setup with --print" in err
        assert "likely cause" not in err

    @pytest.mark.parametrize(
        "hop", [{"proxyjump": ["jumper@203.0.113.5:22"]}, {"proxycommand": ["nc %h %p"]}], ids=["jump", "proxy"]
    )
    def test_no_login_behind_a_hop_names_a_step_that_works_there(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], hop: dict[str, list[str]]
    ) -> None:
        remote.target().own_access = NOTHING
        remote.world.configs = {"handheld": {"user": ["player"], "hostname": [_ADDRESS], **hop}}

        err = self._fails(remote, capsys, "handheld")

        assert "handheld accepted no login from this machine" in err
        assert "Run the setup with --print" not in err
        assert "a key of yours in its ~/.ssh/authorized_keys, or a password" in err

    def test_a_host_key_that_is_not_the_recorded_one(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        first, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        assert first == 0, err
        remote.target().host_key = _host_key(2)

        err = self._fails(remote, capsys, f"player@{_ADDRESS}")

        assert f"the host key at {_ADDRESS} is not the one recorded for handheld" in err
        assert "No connection was made." in err
        assert f"ssh-keygen -R emu-atlas-dev.handheld -f {remote.dev_dir / 'known_hosts'}" in err

    def test_a_failure_after_an_earlier_setup_leaves_its_files_as_they_were(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        remote.target().serving = False

        err = self._fails(remote, capsys, f"player@{_ADDRESS}")

        assert "no SSH server answers" in err

    def test_a_host_key_declined_at_first_contact(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.accept_host_key = False

        err = self._fails(remote, capsys, f"player@{_ADDRESS}")

        assert f"the host key at {_ADDRESS} was not accepted" in err

    def test_too_many_authentication_failures_without_the_key_blames_no_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().refused_agent_keys = 6

        err = self._fails(remote, capsys, f"player@{_ADDRESS}")

        assert f"player@{_ADDRESS} accepted no login from this machine" in err
        assert "this machine's key" not in err

    def test_a_failure_no_pattern_knows_quotes_ssh(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.fail_with = "ssh: a failure nobody has seen before"

        err = self._fails(remote, capsys, f"player@{_ADDRESS}")

        assert "the setup did not start on handheld (ssh ended with 255):\nssh: a failure nobody has seen before" in err


class TestWhatTheTargetLacks:
    """A target that lacks what a probe needs is refused before anything is written, on either machine."""

    def _lacks(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> str:
        build_before = snapshot(remote.build_home)
        target_before = snapshot(remote.target_home)

        code, out, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert out == ""
        assert err.endswith(".\n  Nothing was changed, here or on the target.\n")
        assert snapshot(remote.build_home) == build_before
        assert snapshot(remote.target_home) == target_before
        return err

    def test_a_python_below_the_floor_is_refused_naming_both_versions(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        old = str(remote.tools / "python-old")
        monkeypatch.setattr(setup_remote_target, "TARGET_PYTHON", old)

        err = self._lacks(remote, capsys)

        assert f"handheld lacks Python {FLOOR} or newer at {old} — it has {OLD_PYTHON_VERSION}." in err

    def test_a_target_that_is_set_up_names_its_python(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        """#810 D4: the setup names the target's Python version."""
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        version = ".".join(str(part) for part in sys.version_info[:3])
        assert f"  Its {sys.executable} is Python {version}." in err

    @pytest.mark.parametrize(("version", "sealed"), [("3.12.3", True), ("3.13.5", True), ("3.14.0", False)])
    def test_below_3_14_it_says_the_zstd_answers_stay_sealed(self, version: str, sealed: bool) -> None:
        result = subprocess.CompletedProcess(
            [],
            0,
            f"{setup_remote_target.STARTED}\n{setup_remote_target.PYTHON_FOUND}{version}\n"
            f"{setup_remote_target.INSTALLED}{_SEEN_FROM}\n",
            "",
        )

        message = setup_remote_target.set_up_message("handheld", result)

        assert f"is Python {version}." in message
        assert ("no zstd codec" in message) is sealed

    def test_the_floor_is_the_packages_own(self) -> None:
        pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        assert f">={setup_remote_target.python_floor()}" == pyproject["project"]["requires-python"]
        assert setup_remote_target.python_floor() == FLOOR

    def test_no_python_at_all_is_refused(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        missing = str(tmp_path / "no-python3")
        monkeypatch.setattr(setup_remote_target, "TARGET_PYTHON", missing)

        build_before = snapshot(remote.build_home)
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert f"handheld could not start {missing} (the shell there ended with 127" in err
        assert f"The setup needs Python {FLOOR} or newer at {missing} there." in err
        assert err.endswith("Nothing was changed, here or on the target.\n")
        assert snapshot(remote.build_home) == build_before
        assert snapshot(remote.target_home) == {}

    def test_no_bwrap_is_refused(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        missing = str(tmp_path / "no-bwrap")
        monkeypatch.setattr(setup_remote_target, "TARGET_BWRAP", missing)

        err = self._lacks(remote, capsys)

        assert f"handheld lacks a {missing} that starts a sandbox — there is none; install bubblewrap." in err

    def test_a_bwrap_that_cannot_start_a_sandbox_is_refused_with_what_it_said(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broken = str(remote.tools / "bwrap-broken")
        monkeypatch.setattr(setup_remote_target, "TARGET_BWRAP", broken)

        err = self._lacks(remote, capsys)

        assert (
            f"handheld lacks a {broken} that starts a sandbox — it said: {BROKEN_BWRAP_MESSAGE};"
            " install bubblewrap, or allow unprivileged user namespaces." in err
        )

    @pytest.mark.parametrize("lacking", ["python", "bwrap"])
    def test_the_printed_script_refuses_the_same_way(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, lacking: str
    ) -> None:
        old = str(remote.tools / "python-old")
        missing = str(remote.tools / "no-bwrap")
        if lacking == "python":
            monkeypatch.setattr(setup_remote_target, "TARGET_PYTHON", old)
        else:
            monkeypatch.setattr(setup_remote_target, "TARGET_BWRAP", missing)
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script)

        assert on_target.returncode == 1
        if lacking == "python":
            expected = f"lacks: Python {FLOOR} or newer at {old} — it has {OLD_PYTHON_VERSION}"
        else:
            expected = f"lacks: a {missing} that starts a sandbox — there is none; install bubblewrap"
        assert f"emu-atlas-dev-setup: {expected}\n" in on_target.stderr
        assert on_target.stderr.endswith("emu-atlas-dev-setup: nothing was changed.\n")
        assert snapshot(remote.target_home) == {}

    def test_a_python_that_does_not_say_its_version_is_named_with_what_it_said(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        broken = tmp_path / "python3"
        broken.write_text(
            f"#!{sys.executable} -I\nimport sys\nsys.stderr.write('no module named encodings\\n')\nsys.exit(1)\n",
            encoding="utf-8",
        )
        broken.chmod(0o755)
        monkeypatch.setattr(setup_remote_target, "TARGET_PYTHON", str(broken))
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script)

        assert on_target.returncode == 1
        assert f"at {broken} — it did not say its version: no module named encodings\n" in on_target.stderr
        assert snapshot(remote.target_home) == {}

    def test_an_unforeseen_error_before_the_install_says_nothing_was_changed(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        value = f", 'bwrap': '{remote.tools / 'bwrap'}'"
        assert script.count(value) == 1

        on_target = remote.run_on_target(script.replace(value, ""))

        assert on_target.returncode == 1
        assert on_target.stderr.endswith("emu-atlas-dev-setup: failed: 'bwrap'; nothing was changed.\n")
        assert snapshot(remote.target_home) == {}


class TestAFailureOnTheTarget:
    """The login held and the install started: no pre-login message, and this machine unchanged."""

    def test_a_connection_dropped_after_the_install_started_says_it_stopped_part_way(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.drop_after_first_line = True
        build_before = snapshot(remote.build_home)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert "stopped part-way on handheld" in err
        assert "Connection reset by peer" in err
        assert "This machine is unchanged; run the setup again to finish it." in err
        assert "Nothing was changed" not in err
        assert snapshot(remote.build_home) == build_before

        remote.world.drop_after_first_line = False
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert remote.key_lines()[1:] == [remote.dispatcher_line(_SEEN_FROM)]
        assert remote.config().count("Host handheld\n") == 1

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode")
    def test_a_step_failing_after_the_dispatcher_is_built_leaves_the_target_as_it_was(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.write_text("ssh-ed25519 AAAAothersKey someone@laptop\n", encoding="utf-8")
        remote.authorized_keys.chmod(0o600)
        target_before = snapshot(remote.target_home)
        build_before = snapshot(remote.build_home)
        remote.authorized_keys.chmod(0o000)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        remote.authorized_keys.chmod(0o600)
        target_after = snapshot(remote.target_home)
        assert code == 1
        assert f"the setup failed on handheld: [Errno 13] Permission denied: '{remote.authorized_keys}'" in err
        assert f"nothing was moved into place; {remote.authorized_keys} is as it was.\n" in err
        assert "run the setup again once the cause above is fixed." in err
        assert target_after == target_before
        assert snapshot(remote.build_home) == build_before

    @pytest.mark.parametrize(
        ("set_up_before", "before_line"),
        [
            (False, '    progress.move_into_place(dispatcher + ".new", dispatcher)\n'),
            (False, "    progress.move_into_place(keys + KEYS_SUFFIX, keys)\n"),
            (True, "    progress.move_into_place(keys + KEYS_SUFFIX, keys)\n"),
        ],
        ids=["before the first rename", "between the two", "between the two, set up before"],
    )
    def test_an_error_on_the_way_names_what_it_leaves_and_a_rerun_finishes(
        self,
        remote: Remote,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        set_up_before: bool,
        before_line: str,
    ) -> None:
        """A full disk: a dispatcher put where none was is taken back, one that replaced another is named."""
        remote.authorized_keys.parent.mkdir(mode=0o700)
        remote.authorized_keys.write_text("ssh-ed25519 AAAAothersKey someone@laptop\n", encoding="utf-8")
        if set_up_before:
            assert remote.setup(capsys, "handheld", f"player@{_ADDRESS}")[0] == 0
        template = INSTALL_SCRIPT.read_text(encoding="utf-8")
        assert template.count(before_line) == 1
        failing = tmp_path / "dev_target_install.py"
        failing.write_text(
            template.replace(before_line, '    raise OSError(28, "No space left on device")\n' + before_line),
            encoding="utf-8",
        )
        monkeypatch.setattr(setup_remote_target, "INSTALL_SCRIPT", failing)
        target_before = snapshot(remote.target_home)
        build_before = snapshot(remote.build_home)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert "the setup failed on handheld: [Errno 28] No space left on device; " in err
        assert "This machine is unchanged; run the setup again once the cause above is fixed." in err
        assert "stopped part-way" not in err
        if set_up_before:
            assert f"{remote.root / 'dispatch.py'} was moved into place; {remote.authorized_keys} was not moved," in err
        else:
            assert f"; nothing was moved into place; {remote.authorized_keys} is as it was.\n" in err
        assert snapshot(remote.target_home) == target_before
        assert snapshot(remote.build_home) == build_before

        monkeypatch.setattr(setup_remote_target, "INSTALL_SCRIPT", INSTALL_SCRIPT)
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert remote.key_lines()[-1] == remote.dispatcher_line(_SEEN_FROM)
        assert remote.config().count("Host handheld\n") == 1

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode")
    def test_the_mode_of_ssh_changes_only_once_the_install_went_through(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ssh = remote.authorized_keys.parent
        ssh.mkdir()
        ssh.chmod(0o755)
        remote.authorized_keys.write_text("ssh-ed25519 AAAAothersKey someone@laptop\n", encoding="utf-8")
        remote.authorized_keys.chmod(0o644)
        target_before = snapshot(remote.target_home)
        remote.authorized_keys.chmod(0o000)

        failed, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        remote.authorized_keys.chmod(0o644)
        assert failed == 1
        assert "the setup failed on handheld" in err
        assert snapshot(remote.target_home) == target_before
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        assert code == 0, err
        assert ssh.stat().st_mode & 0o777 == 0o700


@pytest.fixture
def target_side(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The install script loaded in this process, filled in as the setup fills it; a home for it to install in."""
    from scripts import dev_target_install

    values = {
        "target_root": str(ROOT),
        "python": sys.executable,
        "public_key": "ssh-ed25519 AAAAbuildHostsKey emu-atlas-dev@buildhost",
        "build_host": "buildhost",
        "written": "now",
        "stopped": setup_remote_target.STOPPED,
        "failed": setup_remote_target.FAILED,
    }
    monkeypatch.setattr(dev_target_install, "VALUES", values)
    monkeypatch.setattr(dev_target_install, "DISPATCHER_SOURCE", "print()\n")
    home = tmp_path / "target-home"
    home.mkdir()
    return home


def _failing_fsync_of(directory: Path, error: int) -> object:
    real_fsync = os.fsync

    def fsync(descriptor: int) -> None:
        if os.readlink(f"/proc/self/fd/{descriptor}") == str(directory):
            raise OSError(error, os.strerror(error))
        real_fsync(descriptor)

    return fsync


class TestTheInstallInThisProcess:
    """The install script's own steps, with a file system that fails where a test says."""

    def test_the_renames_into_place_reach_the_disk_in_order(
        self, target_side: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each new file is written whole and synced before its rename, and its directory synced after it."""
        from scripts import dev_target_install

        events: list[tuple[str, str, int | str]] = []
        real_fsync, real_replace = os.fsync, os.replace

        def recording_fsync(descriptor: int) -> None:
            events.append(("fsync", os.readlink(f"/proc/self/fd/{descriptor}"), os.fstat(descriptor).st_size))
            real_fsync(descriptor)

        def recording_replace(source: str, destination: str) -> None:
            events.append(("replace", source, destination))
            real_replace(source, destination)

        monkeypatch.setattr(os, "fsync", recording_fsync)
        monkeypatch.setattr(os, "replace", recording_replace)

        dev_target_install.install(str(target_side), "192.0.2.1")

        root, ssh = target_side / ROOT, target_side / ".ssh"
        keys = ssh / "authorized_keys"
        assert [(kind, first, last) for kind, first, last in events if kind == "replace"] == [
            ("replace", f"{root}/dispatch.py.new", f"{root}/dispatch.py"),
            ("replace", f"{keys}.emu-atlas-dev.new", f"{keys}"),
        ]
        assert [event[:2] for event in events] == [
            ("fsync", f"{root}/dispatch.py.new"),
            ("fsync", f"{keys}.emu-atlas-dev.new"),
            ("replace", f"{root}/dispatch.py.new"),
            ("fsync", str(root)),
            ("replace", f"{keys}.emu-atlas-dev.new"),
            ("fsync", str(ssh)),
        ]
        assert events[0][2] == len("print()\n")
        assert events[1][2] == keys.stat().st_size

    @pytest.mark.parametrize("unconfirmed", ["root", "ssh"])
    def test_a_rename_whose_directory_cannot_be_synced_is_named_as_moved(
        self, target_side: Path, monkeypatch: pytest.MonkeyPatch, unconfirmed: str
    ) -> None:
        from scripts import dev_target_install

        root, ssh = target_side / ROOT, target_side / ".ssh"
        root.mkdir(parents=True)
        (root / "dispatch.py").write_text("an older dispatcher\n", encoding="utf-8")
        monkeypatch.setattr(os, "fsync", _failing_fsync_of(root if unconfirmed == "root" else ssh, errno.EIO))

        with pytest.raises(dev_target_install.Stop) as stopped:
            dev_target_install.install(str(target_side), "192.0.2.1")

        dispatcher, keys = root / "dispatch.py", ssh / "authorized_keys"
        assert stopped.value.marker == setup_remote_target.FAILED
        if unconfirmed == "root":
            assert stopped.value.line.endswith(
                f"{dispatcher} was moved into place, not confirmed on disk; {keys} was not moved, and is as it was."
            )
            assert not ssh.exists()
        else:
            assert stopped.value.line.endswith(
                f"{dispatcher} was moved into place; {keys} was moved into place, not confirmed on disk;"
                f" it created {ssh}, which stays."
            )

    def test_a_fresh_dispatcher_whose_rename_cannot_be_confirmed_is_taken_back(
        self, target_side: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts import dev_target_install

        monkeypatch.setattr(os, "fsync", _failing_fsync_of(target_side / ROOT, errno.EIO))

        with pytest.raises(dev_target_install.Stop) as stopped:
            dev_target_install.install(str(target_side), "192.0.2.1")

        assert stopped.value.line.endswith(f"nothing was moved into place; {target_side}/.ssh/authorized_keys is as it was.")
        assert list(target_side.iterdir()) == []

    @pytest.mark.parametrize("error", [errno.EINVAL, errno.EBADF], ids=["EINVAL", "EBADF"])
    def test_a_directory_the_file_system_cannot_sync_is_no_failure(
        self, target_side: Path, monkeypatch: pytest.MonkeyPatch, error: int
    ) -> None:
        from scripts import dev_target_install

        monkeypatch.setattr(os, "fsync", _failing_fsync_of(target_side / ROOT, error))

        dev_target_install.install(str(target_side), "192.0.2.1")

        assert (target_side / ROOT / "dispatch.py").read_text(encoding="utf-8") == "print()\n"

    def test_a_directory_it_created_that_cannot_be_taken_back_is_named(
        self, target_side: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts import dev_target_install

        root = target_side / ROOT
        real_write_new = dev_target_install.write_new

        def write_new(path: str, data: bytes, mode: int) -> None:
            if path.endswith(dev_target_install.KEYS_SUFFIX):
                (root / "left-by-someone-else").write_text("", encoding="utf-8")
                raise OSError(errno.ENOSPC, "No space left on device")
            real_write_new(path, data, mode)

        monkeypatch.setattr(dev_target_install, "write_new", write_new)

        with pytest.raises(dev_target_install.Stop) as stopped:
            dev_target_install.install(str(target_side), "192.0.2.1")

        assert stopped.value.line == (
            f"[Errno {errno.ENOSPC}] No space left on device; nothing was moved into place;"
            f" {target_side}/.ssh/authorized_keys is as it was; it created {target_side}/.local,"
            f" {target_side}/.local/lib, {root}, which stay."
        )
        assert sorted(path.name for path in root.iterdir()) == ["left-by-someone-else"]
        assert not (target_side / ".ssh").exists()


class TestWhatTheInstallLeaves:
    """What an install that does not finish names, in this process."""

    def test_a_new_file_that_cannot_be_removed_is_named_as_left_behind(
        self, target_side: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts import dev_target_install

        real_write_new, real_unlink = dev_target_install.write_new, os.unlink

        def write_new(path: str, data: bytes, mode: int) -> None:
            real_write_new(path, data, mode)
            if path.endswith(dev_target_install.KEYS_SUFFIX):
                raise OSError(errno.ENOSPC, "No space left on device")

        def unlink(path: str, *args: object, **kwargs: object) -> None:
            if str(path).endswith(dev_target_install.KEYS_SUFFIX):
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            real_unlink(path)

        monkeypatch.setattr(dev_target_install, "write_new", write_new)
        monkeypatch.setattr(os, "unlink", unlink)

        with pytest.raises(dev_target_install.Stop) as stopped:
            dev_target_install.install(str(target_side), "192.0.2.1")

        keys = target_side / ".ssh" / "authorized_keys"
        assert stopped.value.marker == setup_remote_target.FAILED
        assert stopped.value.line == (
            f"[Errno {errno.ENOSPC}] No space left on device; nothing was moved into place; {keys} is as it was;"
            f" it created {target_side / '.ssh'}, which stays, and left {keys}.emu-atlas-dev.new behind"
            " (Permission denied)."
        )

    def test_a_refusal_after_a_move_names_what_was_moved(
        self, target_side: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts import dev_target_install

        def refused(home: str) -> None:
            raise dev_target_install.stopped("a refusal after the renames.")

        monkeypatch.setattr(dev_target_install, "close_ssh_directory", refused)

        with pytest.raises(dev_target_install.Stop) as stopped:
            dev_target_install.install(str(target_side), "192.0.2.1")

        root, keys = target_side / ROOT, target_side / ".ssh" / "authorized_keys"
        assert stopped.value.line.startswith(
            f"a refusal after the renames. {root / 'dispatch.py'} was moved into place; {keys} was moved into place;"
        )

    def test_an_error_after_the_install_went_through_does_not_say_nothing_was_changed(
        self, target_side: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from scripts import dev_target_install

        values = {**dev_target_install.VALUES, "started": "started", "python_found": "python=", "installed": "in="}
        values.update({"requires_python": "3.0", "bwrap": "/unused", "default_from": "192.0.2.1", "lacks": "lacks: "})
        monkeypatch.setattr(dev_target_install, "VALUES", values)
        monkeypatch.setattr(dev_target_install, "check_sandbox", lambda bwrap, python: None)
        monkeypatch.setattr(dev_target_install, "install", lambda home, address: None)

        status = dev_target_install.main([])

        assert status == 1
        assert capsys.readouterr().err.endswith(
            f"{setup_remote_target.FAILED}the install went through, but then: 'show_host_keys'\n"
        )


class TestAFailureOnTheTargetEnds:
    """The install refused to go on: what it created is gone again."""

    def test_a_new_authorized_keys_that_did_not_come_out_whole_is_left_unused(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        before = "ssh-ed25519 AAAAothersKey someone@laptop\nssh-ed25519 AAAAanotherOne other@desktop\n"
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.write_text(before, encoding="utf-8")
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        writing = '    with open(path, "wb") as handle:\n        handle.write(data)\n'
        assert script.count(writing) == 1
        short = writing.replace("handle.write(data)", "handle.write(data[: data.rstrip().rfind(b'\\n') + 1])")

        on_target = remote.run_on_target(script.replace(writing, short), _SEEN_FROM)

        assert on_target.returncode == 1
        assert "emu-atlas-dev-setup: stopped: the new" in on_target.stderr
        assert "did not come out whole, so it was left as it was." in on_target.stderr
        assert remote.authorized_keys.read_text(encoding="utf-8") == before
        assert sorted(path.name for path in remote.authorized_keys.parent.iterdir()) == ["authorized_keys"]
        assert not (remote.target_home / ".local").exists()


class TestTheAddressCheck:
    """The printed script's address is the one guard against key options smuggled into ``from="…"``."""

    @pytest.mark.parametrize(
        "address",
        ['192.0.2.1",no-restrict', "192.0.2.1 192.0.2.2", "192.0.2.1/24", "*", "handheld.local", "192.0.2.1,192.0.2.2"],
    )
    def test_an_address_that_is_not_one_is_refused_and_writes_nothing(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], address: str
    ) -> None:
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script, address)

        assert on_target.returncode == 1
        assert "no address to restrict the key to" in on_target.stderr
        assert snapshot(remote.target_home) == {}

    def test_without_ssh_client_and_without_a_default_it_is_refused_and_writes_nothing(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        default = f"'default_from': '{remote.world.route_source}'"
        assert script.count(default) == 1

        on_target = remote.run_on_target(script.replace(default, "'default_from': ''"))

        assert on_target.returncode == 1
        assert 'no address to restrict the key to (got "")' in on_target.stderr
        assert snapshot(remote.target_home) == {}

    def test_an_ipv6_address_is_taken(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script, "2001:db8::6")

        assert on_target.returncode == 0, on_target.stderr
        assert remote.key_lines()[1] == remote.dispatcher_line("2001:db8::6")

    def test_an_address_the_target_sees_that_is_no_address_stops_the_setup(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().client_address = "fe80::6%wlan0"

        err = fails(remote, capsys, "handheld", f"player@{_ADDRESS}")

        assert "the setup stopped on handheld: no address to restrict the key to" in err
        assert "run the setup again" not in err
        assert "This machine is unchanged." in err


class TestTheWrittenEntryAlone:
    """The entry written here, read with this file alone, as every later call reads it."""

    def test_it_reaches_the_dispatcher_with_the_key_and_the_recorded_host_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        config_home = tmp_path / "config home"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        assert code == 0, err
        config = config_home / "emu-atlas-dev" / "ssh_config"
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            manifest = f'{{"requires_python": "{FLOOR}"}}'.encode()
            member = tarfile.TarInfo("tree.json")
            member.size = len(manifest)
            archive.addfile(member, io.BytesIO(manifest))
        logged = len(log_lines(remote.world))

        def ssh(request: str, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                [str(remote.tools / "ssh"), "-F", str(config), "-T", "handheld", request],
                input=stdin,
                capture_output=True,
                check=False,
            )

        copied = ssh("copy", stream.getvalue())
        refused = ssh("sh")

        assert copied.returncode == 0, copied.stderr
        assert (remote.root / "current" / "tree.json").read_bytes() == manifest
        assert refused.returncode == 3
        assert refused.stderr.decode().endswith(f"emu-atlas-dev: refused: sh\n{dev_dispatch.MAY}\n")
        assert log_lines(remote.world)[logged:] == [f"login {_ADDRESS} key", f"login {_ADDRESS} key"]


class TestASymlinkedAuthorizedKeys:
    def test_it_is_written_through_and_stays_a_symlink(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        managed = remote.target_home / "dotfiles" / "authorized_keys"
        managed.parent.mkdir()
        managed.write_text("ssh-ed25519 AAAAothersKey someone@laptop\n", encoding="utf-8")
        remote.authorized_keys.parent.mkdir()
        remote.authorized_keys.symlink_to(managed)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert remote.authorized_keys.is_symlink()
        lines = managed.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "ssh-ed25519 AAAAothersKey someone@laptop"
        assert lines[2] == remote.dispatcher_line(_SEEN_FROM)
        assert sorted(path.name for path in managed.parent.iterdir()) == ["authorized_keys"]


class TestAHomeTheLineCannotCarry:
    def test_a_quote_in_the_home_directory_is_refused_rather_than_escaped(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        quoted = tmp_path / 'player "home"'
        quoted.mkdir()
        remote.target().home = str(quoted)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert "cannot be written into the key's command= option safely" in err
        assert snapshot(quoted) == {}

    def test_a_blank_in_the_home_directory_is_quoted_for_the_shell(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        blank = tmp_path / "player home"
        blank.mkdir()
        remote.target().home = str(blank)
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        logged = len(log_lines(remote.world))

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert log_lines(remote.world)[logged:] == [f"login {_ADDRESS} key"]
        assert f"'{blank}/{ROOT}/dispatch.py'" in (blank / ".ssh" / "authorized_keys").read_text(encoding="utf-8")


class TestTheKeyFirst:
    """#812 D1: with the build host's key here, the setup logs in with that key alone first."""

    def test_a_rerun_logs_in_with_the_key_alone_and_asks_for_nothing(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        calls = len(ssh_calls(remote.world))

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        (login,) = ssh_calls(remote.world)[calls:]
        assert login["_offered"] == [str(remote.dev_dir / "id_ed25519")]
        assert login["identitiesonly"] == ["yes"]
        assert login["preferredauthentications"] == ["publickey"]
        assert login["batchmode"] == ["yes"]
        assert "identityfile" not in login

    def test_a_target_whose_max_auth_tries_ends_a_login_of_the_users_own_keys_is_set_up_with_the_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = NOTHING
        remote.target().refused_agent_keys = 6
        remote.target().client_address = remote.world.route_source
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        remote.run_on_target(script)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert log_lines(remote.world)[-1] == f"login {_ADDRESS} key"
        assert "Host handheld\n" in remote.config()

    def test_the_first_key_login_after_print_may_ask_about_the_host_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = NOTHING
        remote.target().client_address = remote.world.route_source
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        remote.run_on_target(script)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        (login,) = ssh_calls(remote.world)
        assert login["batchmode"] == ["no"]
        assert login["identitiesonly"] == ["yes"]
        assert login["preferredauthentications"] == ["publickey"]

    def test_a_key_refused_after_print_asks_for_no_password_before_the_users_own_login(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        remote.run_on_target(script)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        key_login, own_login = ssh_calls(remote.world)
        assert key_login["preferredauthentications"] == ["publickey"]
        assert "preferredauthentications" not in own_login
        assert log_lines(remote.world) == [
            "host key shown emu-atlas-dev.handheld",
            f"password asked {_ADDRESS}",
            f"login {_ADDRESS} own",
        ]

    def test_a_refused_key_hands_over_to_the_users_own_login_which_repairs_the_target(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        remote.target().client_address = _MOVED_FROM
        calls = len(ssh_calls(remote.world))

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert "emu-atlas-dev: handheld refused this machine's key; logging in with your own access instead.\n" in err
        key_login, own_login = ssh_calls(remote.world)[calls:]
        assert key_login["identitiesonly"] == ["yes"]
        assert "identityfile" not in own_login
        assert own_login["batchmode"] == ["no"]
        assert remote.key_lines()[1] == remote.dispatcher_line(_MOVED_FROM)

    def test_every_login_opens_a_connection_of_its_own(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        remote.target().client_address = _MOVED_FROM
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        calls = ssh_calls(remote.world)

        assert len(calls) == 3
        for login in calls:
            assert login["controlmaster"] == ["no"]
            assert login["controlpath"] == ["none"]
            assert login["stricthostkeychecking"] == ["ask"]

    def test_a_key_without_its_public_half_is_refused_before_any_login(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        (remote.dev_dir / "id_ed25519.pub").unlink()

        err = fails(remote, capsys, "handheld", f"player@{_ADDRESS}")

        assert "has no id_ed25519.pub beside it" in err
        assert ssh_calls(remote.world) == []

    def test_it_offers_the_build_hosts_key_and_no_identity_from_the_users_config(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        own_key = remote.build_home / ".ssh" / "id_user"
        own_key.parent.mkdir()
        own_key.write_text("private\n", encoding="utf-8")
        own_key.with_name("id_user.pub").write_text("ssh-ed25519 AAAAusersOwnKey me@laptop\n", encoding="utf-8")
        remote.world.configs = {
            "handheld": {"user": ["player"], "hostname": [_ADDRESS], "identityfile": ["~/.ssh/id_user"]}
        }
        remote.setup(capsys, "handheld", "handheld")

        code, _, err = remote.setup(capsys, "handheld", "handheld")

        assert code == 0, err
        first, second = ssh_calls(remote.world)
        assert first["_offered"] == ["~/.ssh/id_user"]
        assert "_config" not in first
        assert second["_offered"] == [str(remote.dev_dir / "id_ed25519")]
        assert "_config" in second


class TestAKeyWhoseForcedCommandFails:
    """The target takes the key, but what the key's line runs does not run the install."""

    @pytest.mark.parametrize(
        "dispatcher", [None, "import sys\nsys.stderr.write('emu-atlas-dev: refused: older\\n')\nsys.exit(3)\n"]
    )
    def test_the_users_own_login_repairs_it(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], dispatcher: str | None
    ) -> None:
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        installed = remote.root / "dispatch.py"
        if dispatcher is None:
            installed.unlink()
        else:
            installed.write_text(dispatcher, encoding="utf-8")
        logged = len(log_lines(remote.world))

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert "handheld took this machine's key, but its forced command ran no install; logging in with your own" in err
        assert log_lines(remote.world)[logged:] == [
            f"login {_ADDRESS} key",
            f"password asked {_ADDRESS}",
            f"login {_ADDRESS} own",
        ]
        assert installed.read_bytes() == DISPATCHER.read_bytes()

    def test_without_a_login_of_the_users_own_the_message_names_the_forced_command(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.setup(capsys, "handheld", f"player@{_ADDRESS}")
        (remote.root / "dispatch.py").write_text(
            "import sys\nsys.stderr.write('emu-atlas-dev: refused: an older dispatcher\\n')\nsys.exit(3)\n",
            encoding="utf-8",
        )
        remote.target().own_access = NOTHING
        build_before = snapshot(remote.build_home)

        err = fails(remote, capsys, "handheld", f"player@{_ADDRESS}")

        assert f"player@{_ADDRESS} accepted no login from this machine" in err
        assert "handheld accepted this machine's key, but its forced command failed (ssh ended with 3)" in err
        assert "an older dispatcher" in err
        assert "refused this machine's key" not in err
        assert snapshot(remote.build_home) == build_before


class TestHops:
    def _behind_bastion(self, remote: Remote, hop: str = "bastion") -> None:
        remote.world.configs = {"handheld": {"user": ["player"], "hostname": [_ADDRESS], "proxyjump": [hop]}}

    @pytest.mark.parametrize("hop", ["bastion", "jumper@bastion:2222"])
    def test_a_hop_this_machine_cannot_resolve_is_named_as_the_hop(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], hop: str
    ) -> None:
        self._behind_bastion(remote, hop)
        remote.world.hop_failure = "unresolved"

        err = fails(remote, capsys, "handheld", "handheld")

        assert f"this machine cannot resolve bastion, a hop on the way to handheld ({hop})" in err
        assert f"cannot resolve {_ADDRESS}" not in err

    def test_a_hop_whose_host_key_changed_is_named_as_the_hop_and_its_record_left_alone(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._behind_bastion(remote)
        remote.world.hop_failure = "host-key"

        err = fails(remote, capsys, "handheld", "handheld")

        assert "a hop on the way to handheld (bastion) has a host key that is not the one" in err
        assert str(remote.build_home / ".ssh" / "known_hosts") in err
        assert "ssh-keygen -R" not in err

    def test_a_hop_that_refuses_the_login_is_named_and_blames_no_key(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._behind_bastion(remote)
        remote.setup(capsys, "handheld", "handheld")
        remote.world.hop_failure = "denied"

        err = fails(remote, capsys, "handheld", "handheld")

        assert "the hop bastion accepted no login from this machine" in err
        assert "refused this machine's key" not in err
        assert "handheld accepted no login" not in err

    def test_a_hop_with_no_server_answering_is_named(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        self._behind_bastion(remote)
        remote.world.hop_failure = "refused"

        err = fails(remote, capsys, "handheld", "handheld")

        assert "no SSH server answers at the hop bastion" in err
        assert f"answers at {_ADDRESS}" not in err

    def test_an_unaccepted_host_key_ssh_does_not_place_names_the_target_or_the_hop(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._behind_bastion(remote)
        remote.world.hop_failure = "host-key-unknown"

        err = fails(remote, capsys, "handheld", "handheld")

        assert f"the host key of {_ADDRESS} or of the hop bastion was not accepted" in err


class TestTheBuildHostsSide:
    def test_a_write_here_that_fails_after_the_target_is_set_up_says_so(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (remote.dev_dir / "ssh_config").mkdir(parents=True)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert "handheld is set up, but this machine's side could not be written" in err
        directory = remote.dev_dir
        assert (
            f"  Already written here: {directory / 'id_ed25519.pub'}, {directory / 'id_ed25519'},"
            f" {directory / 'known_hosts'}.\n" in err
        )
        assert "Run the setup again to finish it." in err
        assert "Traceback" not in err

        (remote.dev_dir / "ssh_config").rmdir()
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert remote.config().count("Host handheld\n") == 1
        assert remote.key_lines()[1:] == [remote.dispatcher_line(_SEEN_FROM)]

    @pytest.mark.parametrize("during", ["log_in_and_install", "record_here"])
    def test_an_interrupted_setup_says_which_side_may_be_part_way(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, during: str
    ) -> None:
        def interrupted(*_: object, **__: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(setup_remote_target, during, interrupted)

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        if during == "log_in_and_install":
            assert "interrupted during the setup login to handheld. The target may be set up part-way;" in err
            assert not remote.dev_dir.exists()
        else:
            assert "interrupted while recording handheld here, after the target was set up." in err
        assert "Traceback" not in err

    def test_its_key_reaches_this_machine_public_half_first(
        self, remote: Remote, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A private half that could not be written leaves no private key without its public half."""
        new = tmp_path / "new"
        new.mkdir()
        (new / "id_ed25519").write_text("private\n", encoding="utf-8")
        (new / "id_ed25519.pub").write_text("ssh-ed25519 AAAAkey emu-atlas-dev@buildhost\n", encoding="utf-8")
        real = setup_remote_target.write_atomically

        def full_disk_for_the_private_half(path: Path, data: str | bytes, mode: int = 0o600) -> None:
            if path.name == "id_ed25519":
                raise OSError(errno.ENOSPC, "No space left on device")
            real(path, data, mode)

        monkeypatch.setattr(setup_remote_target, "write_atomically", full_disk_for_the_private_half)

        with pytest.raises(OSError):
            setup_remote_target.install_key(new / "id_ed25519", remote.dev_dir)

        assert sorted(path.name for path in remote.dev_dir.iterdir()) == ["id_ed25519.pub"]
        assert setup_remote_target.existing_key(remote.dev_dir) is None

    def test_a_partial_file_that_cannot_be_removed_leaves_the_first_error_standing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "ssh_config").mkdir()
        (tmp_path / "ssh_config" / "in-the-way").write_text("", encoding="utf-8")

        def cannot_unlink(self: Path, missing_ok: bool = False) -> None:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))

        monkeypatch.setattr(Path, "unlink", cannot_unlink)

        with pytest.raises(OSError) as raised:
            setup_remote_target.write_atomically(tmp_path / "ssh_config", "Host handheld\n")

        assert raised.value.errno != errno.EACCES

    def test_a_file_written_here_leaves_nothing_half_written_when_its_rename_fails(self, tmp_path: Path) -> None:
        (tmp_path / "ssh_config").mkdir()
        (tmp_path / "ssh_config" / "in-the-way").write_text("", encoding="utf-8")

        with pytest.raises(OSError):
            setup_remote_target.write_atomically(tmp_path / "ssh_config", "Host handheld\n")

        assert sorted(path.name for path in tmp_path.iterdir()) == ["ssh_config"]

    def test_a_target_whose_name_another_target_starts_with_still_gets_the_first_contact_notice(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.world.targets[_NEW_ADDRESS] = Target(home=str(remote.target_home), client_address=_SEEN_FROM)
        remote.setup(capsys, "handheld2", f"player@{_NEW_ADDRESS}")

        _, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert "first contact with handheld:" in err

    @pytest.mark.parametrize(("tool", "argv"), [("ssh", ()), ("ssh-keygen", ()), ("ip", ("--print",))])
    def test_a_tool_this_machine_lacks_is_named(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], tool: str, argv: tuple[str, ...]
    ) -> None:
        (remote.tools / tool).unlink()

        err = fails(remote, capsys, "handheld", f"player@{_ADDRESS}", *argv)

        assert f"the setup needs {tool}, and this machine has none on its PATH" in err
        assert not remote.dev_dir.exists()


class TestPrintWritesNothingWhenItCannotPrint:
    def test_a_name_this_machine_cannot_resolve(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        code, script, err = remote.setup(capsys, "handheld", "player@handheld.invalid", "--print")

        assert (code, script) == (1, "")
        assert "this machine cannot resolve handheld.invalid" in err
        assert snapshot(remote.build_home) == {}

    def test_a_route_that_names_no_source_address(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.route_source = ""

        code, script, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        assert (code, script) == (1, "")
        assert f"`ip route get {_ADDRESS}` named no address" in err
        assert snapshot(remote.build_home) == {}

    @pytest.mark.parametrize("argv", [("--print",), ()], ids=["print", "login"])
    def test_a_key_ssh_keygen_could_not_make(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
    ) -> None:
        remote.world.keygen_fails = True

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", *argv)

        assert code == 1
        assert "ssh-keygen could not create a key" in err
        assert snapshot(remote.build_home) == {}


class TestOtherFailures:
    def test_a_refused_key_whose_route_cannot_be_read_gets_the_shorter_message(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        remote.target().own_access = NOTHING
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")
        remote.run_on_target(script)
        remote.world.route_source = ""

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert (
            "The target refused this machine's key as well. (The address a script --print printed would restrict the"
            f" key to cannot be shown here: `ip route get {_ADDRESS}` named no address this machine sends from;"
            f" it said: {_ADDRESS} dev wlan0 uid 1000 cache)" in err
        )
        assert "likely cause" not in err
        assert "Traceback" not in err

    def test_an_ssh_that_cannot_read_its_config(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.ssh_g_fails = True

        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 1
        assert f"ssh could not read its configuration for player@{_ADDRESS}:\n/home/u/.ssh/config line 3" in err
        assert snapshot(remote.build_home) == {}

    def test_a_destination_that_resolves_to_no_host(self, remote: Remote, capsys: pytest.CaptureFixture[str]) -> None:
        remote.world.configs = {"handheld": {"user": ["player"]}}

        err = fails(remote, capsys, "handheld", "handheld")

        assert "ssh resolved handheld to no host or no user" in err

    def test_a_host_key_file_that_cannot_be_read_is_named_and_the_install_stands(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (remote.host_keys / "ssh_host_rsa_key.pub").write_bytes(b"\xff\xfe no text\n")
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script)

        assert on_target.returncode == 0, on_target.stderr
        assert f"  {remote.host_keys / 'ssh_host_rsa_key.pub'}: could not be read (" in on_target.stdout
        assert f"  {fingerprint_of(_HOST_KEY)} (ssh-ed25519)\n" in on_target.stdout

    def test_an_install_script_that_cannot_be_sent_as_utf_8_is_not_sent(self) -> None:
        with pytest.raises(setup_remote_target.SetupError, match="what the setup would send to ssh is no UTF-8"):
            setup_remote_target.run(["ssh"], "caf\udce9")

    def test_an_install_that_reports_no_python_version_says_so(self) -> None:
        result = subprocess.CompletedProcess([], 0, f"{setup_remote_target.INSTALLED}{_SEEN_FROM}\n", "")

        message = setup_remote_target.set_up_message("handheld", result)

        assert "The install did not report the version of its" in message
        assert "zstd answers stay sealed there is not known" in message

    @pytest.mark.parametrize(
        "stderr",
        [
            "Connection closed by 192.0.2.20 port 22\r\n",
            "client_loop: send disconnect: Connection reset by peer\r\nConnection reset by 192.0.2.20 port 22\r\n",
        ],
        ids=["closed", "reset"],
    )
    def test_a_session_that_dropped_after_the_login_is_no_server_missing(self, stderr: str) -> None:
        destination = setup_remote_target.Destination(
            text=f"player@{_ADDRESS}", user="player", hostname=_ADDRESS, port="22", proxy_jump=None, proxy_command=None
        )

        said = setup_remote_target.classify(stderr, name="handheld", destination=destination, known_hosts=())

        assert said is None

    def test_print_on_a_target_with_no_host_keys_says_so(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (remote.host_keys / "ssh_host_ed25519_key.pub").unlink()
        _, script, _ = remote.setup(capsys, "handheld", f"player@{_ADDRESS}", "--print")

        on_target = remote.run_on_target(script)

        assert on_target.returncode == 0, on_target.stderr
        assert f"none found in {remote.host_keys} — is an SSH server set up here?" in on_target.stdout


class TestArguments:
    @pytest.mark.parametrize("name", ["", "-handheld", "my handheld", "handheld/1", "handheld;x"])
    def test_a_name_ssh_could_not_carry_is_refused(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], name: str
    ) -> None:
        err = fails(remote, capsys, "--", name, f"player@{_ADDRESS}")

        assert "a target's name is letters" in err
        assert log_lines(remote.world) == []

    def test_a_destination_that_reads_as_an_option_is_refused(
        self, remote: Remote, capsys: pytest.CaptureFixture[str]
    ) -> None:
        err = fails(remote, capsys, "--", "handheld", "-oProxyCommand=touch /tmp/x")

        assert "no destination ssh could reach" in err
        assert log_lines(remote.world) == []


class TestTheInstallScriptAsItStands:
    def test_unfilled_it_refuses_and_writes_nothing(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(INSTALL_SCRIPT)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
        )

        assert result.returncode == 2
        assert "this is the template; setup:remote-target fills it in." in result.stderr
        assert list(tmp_path.iterdir()) == []

    def test_the_setup_fills_in_both_marked_lines(self) -> None:
        script = setup_remote_target.install_script(
            public_key="ssh-ed25519 AAAA emu-atlas-dev@buildhost",
            build_host="buildhost",
            written="now",
            default_from="",
            show_host_keys=False,
        )

        assert "@VALUES@" not in script
        assert "@DISPATCHER@" not in script
        assert f"DISPATCHER_SOURCE = {DISPATCHER.read_text(encoding='utf-8')!r}\n" in script


class TestTheRealHomeIsOutOfReach:
    def test_every_path_the_setup_writes_lies_under_the_tests_own_directories(
        self, remote: Remote, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        code, _, err = remote.setup(capsys, "handheld", f"player@{_ADDRESS}")

        assert code == 0, err
        assert setup_remote_target.config_dir().is_relative_to(tmp_path)
        written = [path for root in (remote.build_home, remote.target_home) for path in root.rglob("*")]
        assert written
        assert all(path.is_relative_to(tmp_path) for path in written)

    def test_the_guard_names_the_real_home_and_not_the_one_the_tests_run_in(self) -> None:
        home = os.environ["HOME"]
        assert json.loads(os.environ[GUARDED_VARIABLE]) == GUARDED
        assert [Path(path).name for path in GUARDED] == [".ssh", "emu-atlas-dev", "emu-atlas-dev"]
        assert not any(path.startswith(home + os.sep) for path in GUARDED)

    def test_the_guard_refuses_a_touch_in_this_process_before_it_happens(self, tmp_path: Path) -> None:
        """Shown on a decoy: a guard that failed to refuse would write there, never into the real home."""
        decoy = tmp_path / "decoy"
        decoy.mkdir()

        with guard_real_home([str(decoy)]) as touched, pytest.raises(PermissionError):
            open(decoy / "written", "wb")

        assert touched == [f"open {decoy / 'written'}"]
        assert not (decoy / "written").exists()

    def test_a_stand_in_refuses_a_touch_before_it_happens(self, remote: Remote, tmp_path: Path) -> None:
        """Shown on a decoy, for the same reason."""
        remote.save()
        decoy = tmp_path / "decoy"
        decoy.mkdir()

        result = subprocess.run(
            [str(remote.tools / "ssh-keygen"), "-q", "-C", "x", "-f", str(decoy / "key")],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, GUARDED_VARIABLE: json.dumps([str(decoy)])},
        )

        assert result.returncode != 0
        assert f"a stand-in reached the guarded {decoy / 'key'}" in result.stderr
        assert list(decoy.iterdir()) == []


class TestTheTaskAndTheGuide:
    def test_the_task_hands_its_name_destination_and_print_flag_to_the_script(self) -> None:
        task = tomllib.loads((REPO / "mise.toml").read_text(encoding="utf-8"))["tasks"]["setup:remote-target"]

        assert task["raw"] is True
        assert 'arg "<name>"' in task["usage"]
        assert 'arg "<destination>"' in task["usage"]
        assert 'flag "--print"' in task["usage"]

    @pytest.mark.parametrize("print_flag", [None, "true"], ids=["login", "print"])
    def test_the_task_passes_name_and_destination_after_a_double_dash_as_one_word_each(
        self, tmp_path: Path, print_flag: str | None
    ) -> None:
        task = tomllib.loads((REPO / "mise.toml").read_text(encoding="utf-8"))["tasks"]["setup:remote-target"]
        record = tmp_path / "argv.json"
        python = tmp_path / "python"
        python.write_text(
            f"#!{sys.executable} -I\nimport json, sys\nopen({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        python.chmod(0o755)
        env = {"PATH": str(tmp_path), "usage_name": "-handheld", "usage_destination": "player@192.0.2.20 x"}
        if print_flag is not None:
            env["usage_print"] = print_flag

        ran = subprocess.run(["/bin/sh", "-c", task["run"]], env=env, capture_output=True, text=True, check=False)

        assert ran.returncode == 0, ran.stderr
        flag = ["--print"] if print_flag else []
        assert json.loads(record.read_text(encoding="utf-8")) == [
            "scripts/setup_remote_target.py",
            *flag,
            "--",
            "-handheld",
            "player@192.0.2.20 x",
        ]

    def test_the_guide_covers_the_setup_where_the_scripts_point(self) -> None:
        guide = (REPO / "docs" / "remote-target.md").read_text(encoding="utf-8")
        headings = {line.lstrip("#").strip() for line in guide.splitlines() if line.startswith("#")}

        assert "mise run setup:remote-target <name> <destination> --print" in guide
        for failure in ("cannot resolve", "no SSH server answering", "no login accepted", "not the recorded one"):
            assert failure in guide
        for script in ("setup_remote_target.py", "dev_dispatch.py", "dev_target_install.py"):
            text = " ".join((REPO / "scripts" / script).read_text(encoding="utf-8").replace("#", " ").split())
            pointers = re.findall(r'docs/remote-target\.md, "([^"]+)"', text)
            assert pointers, script
            assert set(pointers) <= headings, script

    def test_the_guide_names_the_sandboxs_options_as_the_dispatcher_starts_it(self) -> None:
        from scripts import dev_dispatch

        guide = (REPO / "docs" / "remote-target.md").read_text(encoding="utf-8")

        assert f"(`{' '.join(dev_dispatch.SANDBOX_OPTIONS)}`)" in guide

    def test_the_guide_names_the_dispatchers_statuses(self) -> None:
        from scripts import dev_dispatch

        guide = (REPO / "docs" / "remote-target.md").read_text(encoding="utf-8")

        for name in ("REFUSED", "CANNOT_RUN", "COPY_FAILED"):
            assert f"- **{getattr(dev_dispatch, name)} `{name}`** — " in guide, name
