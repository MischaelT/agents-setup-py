"""Hermetic test setup: HOME is a scratch folder, upstream repos are local git repos, claude/codex are fakes. No network."""
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def git_run(repo, *args):
    return subprocess.run([*GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return Path(path)


def commit(repo, msg="c"):
    git_run(repo, "add", "-A")
    git_run(repo, "commit", "-qm", msg)
    return git_run(repo, "rev-parse", "HEAD")


def make_skill(repo, rel, name, body="v1"):
    d = Path(repo) / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}\n", encoding="utf-8")


def make_claude_mkt(path, name, version="1.0.0"):
    make_repo(path)
    (path / ".claude-plugin").mkdir(exist_ok=True)
    (path / "plugins/astral/.claude-plugin").mkdir(parents=True, exist_ok=True)
    (path / ".claude-plugin/marketplace.json").write_text(
        '{"name":"%s","owner":{"name":"t"},"plugins":[{"name":"astral","source":"./plugins/astral"}]}' % name, encoding="utf-8")
    (path / "plugins/astral/.claude-plugin/plugin.json").write_text('{"name":"astral","version":"%s"}' % version, encoding="utf-8")


def make_codex_mkt(path, name, version="1.0.0"):
    make_repo(path)
    (path / ".agents/plugins").mkdir(parents=True, exist_ok=True)
    (path / "plugins/hello/.codex-plugin").mkdir(parents=True, exist_ok=True)
    (path / ".agents/plugins/marketplace.json").write_text(
        '{"name":"%s","plugins":[{"name":"hello","source":{"source":"local","path":"./plugins/hello"}}]}' % name, encoding="utf-8")
    (path / "plugins/hello/.codex-plugin/plugin.json").write_text('{"name":"hello","version":"%s"}' % version, encoding="utf-8")


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    """Whatever a test does, it can never reach the real home folder or the real CLIs."""
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    for var in ("XDG_STATE_HOME", "CODEX_HOME", "AGENTS_MANIFEST", "AGENTS_FORCE_COPY", "FAKE_FAIL", "FAKE_NAMES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("FAKE_STATE_DIR", str(tmp_path / "fake-state"))
    (tmp_path / "fake-state").mkdir()
    # a PATH without the real claude/codex: only python's folder and git
    bindir = tmp_path / "bin"
    bindir.mkdir()
    git_dir = Path(shutil.which("git")).parent
    monkeypatch.setenv("PATH", os.pathsep.join([str(bindir), str(git_dir), str(Path(sys.executable).parent)]))
    assert shutil.which("claude") is None and shutil.which("codex") is None, "a real claude/codex is reachable"
    return home


@pytest.fixture
def fakes(tmp_path):
    """Install the fake claude/codex CLIs on PATH; returns a helper to read their state and call log."""
    bindir = tmp_path / "bin"

    def install(tool):
        script = HERE / "fakes" / f"{tool}_cli.py"
        if os.name == "nt":
            (bindir / f"{tool}.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            p = bindir / tool
            p.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)

    class Fakes:
        state_dir = tmp_path / "fake-state"

        def __init__(self):
            self.installed = set()

        def add(self, *tools):
            for t in tools:
                install(t)
                self.installed.add(t)
            return self

        def calls(self):
            f = self.state_dir / "calls.log"
            return f.read_text(encoding="utf-8").splitlines() if f.exists() else []

        def mutating(self):
            import re
            rx = re.compile(r"plugin (marketplace (add|remove)|install|uninstall|add|remove)")
            return [c for c in self.calls() if rx.search(c)]

        def state(self, tool):
            import json
            f = self.state_dir / f"{tool}.json"
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"marketplaces": {}, "plugins": {}}

    return Fakes()


class World:
    """A scratch copy of a project root with a manifest, plus local upstream repos."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.root = tmp_path / "root"
        for d in ("config/claude", "config/codex", "skills/shared", "skills/claude", "skills/codex"):
            (self.root / d).mkdir(parents=True)
        (self.root / "config/claude/CLAUDE.md").write_text("# shared\n", encoding="utf-8")
        (self.root / "config/codex/AGENTS.md").write_text("", encoding="utf-8")
        self.fix = tmp_path / "fix"
        self.fix.mkdir()
        self.py = make_repo(self.fix / "py")
        make_skill(self.py, "skills/a", "alpha")
        make_skill(self.py, "skills/b", "beta")
        self.sha_py = commit(self.py)
        self.cmkt = self.fix / "cmkt"
        make_claude_mkt(self.cmkt, "astral-sh")
        self.sha_cmkt = commit(self.cmkt)
        self.xmkt = self.fix / "xmkt"
        make_codex_mkt(self.xmkt, "cx")
        self.sha_xmkt = commit(self.xmkt)

    @property
    def manifest(self):
        return self.root / "manifest.yaml"

    def write(self, text):
        self.manifest.write_text(textwrap.dedent(text).replace("{py_url}", self.py.as_uri()).replace("{py}", self.py.as_posix()).replace("{cmkt}", self.cmkt.as_posix())
                                 .replace("{xmkt}", self.xmkt.as_posix()).replace("{sha_py}", self.sha_py)
                                 .replace("{sha_cmkt}", self.sha_cmkt).replace("{sha_xmkt}", self.sha_xmkt), encoding="utf-8")

    def base(self, extra=""):
        self.write(textwrap.dedent("""
            skills:
              - repo: {py_url}
                ref: "{sha_py}"
                tool: claude
                skills:
                  alpha: skills/a
                  beta: skills/b
            config:
              - source: config/codex/AGENTS.md
                mode: link
                target: ~/.codex/AGENTS.md
              - source: config/claude/CLAUDE.md
                mode: import
                target: ~/.claude/CLAUDE.md
            """) + textwrap.dedent(extra))

    def sync(self, **kw):
        from agents_setup.sync import Sync
        return Sync(self.root, **kw)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)
