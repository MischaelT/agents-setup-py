import json
import os
import shutil
from pathlib import Path

import pytest

import conftest
from agents_setup import manifest, paths
from agents_setup.common import Fail, fetch_snapshot, remove_dir_checked
from agents_setup.project import run_project
from agents_setup.update import run_update, units_of


# ---------- fetch_snapshot ----------
def test_fetch_snapshot_copies_skill_and_writes_marker(world, tmp_path):
    (world.py / "skills/a/refs").mkdir()
    (world.py / "skills/a/refs/x.md").write_text("x")
    (world.py / "skills/a/.gitignore").write_text("secret\n")
    (world.py / "skills/a/__pycache__").mkdir()
    (world.py / "skills/a/__pycache__/m.pyc").write_text("x")
    (world.py / "LICENSE").write_text("MIT")
    sha = conftest.commit(world.py)
    dest = tmp_path / "out" / "alpha"
    fetch_snapshot("alpha", world.py.as_uri(), "skills/a", sha, dest)
    assert (dest / "SKILL.md").is_file() and (dest / "refs/x.md").is_file()
    assert not (dest / ".gitignore").exists() and not (dest / "__pycache__").exists()
    assert (dest / "LICENSE.upstream").read_text() == "MIT"
    assert (dest / ".upstream").read_text() == f"repo={world.py.as_uri()}\npath=skills/a\nref={sha}\n"


def test_fetch_snapshot_rejects_bad_input(world, tmp_path):
    uri = world.py.as_uri()
    with pytest.raises(Fail, match="no SKILL.md"):
        fetch_snapshot("x", uri, "skills", world.sha_py, tmp_path / "d1")
    with pytest.raises(Fail, match="not inside"):
        fetch_snapshot("x", uri, "nope", world.sha_py, tmp_path / "d2")
    with pytest.raises(Fail, match="bad name"):
        fetch_snapshot("../x", uri, "skills/a", world.sha_py, tmp_path / "d3")
    with pytest.raises(Fail):
        fetch_snapshot("x", "file:///nonexistent/repo.git", "skills/a", world.sha_py, tmp_path / "d4")
    assert not (tmp_path / "d1").exists() and not (tmp_path / "d4").exists()


def test_fetch_snapshot_rejects_a_symlink_escaping_the_clone(world, tmp_path):
    if os.name == "nt":
        pytest.skip("symlinks in git need extra setup on Windows")
    os.symlink("/tmp", world.py / "link")
    sha = conftest.commit(world.py)
    with pytest.raises(Fail, match="not inside"):
        fetch_snapshot("x", world.py.as_uri(), "link", sha, tmp_path / "d")


def test_remove_dir_checked(tmp_path):
    (tmp_path / "ok").mkdir()
    remove_dir_checked(tmp_path / "ok", tmp_path)
    (tmp_path / "a" / "b").mkdir(parents=True)
    with pytest.raises(Fail):
        remove_dir_checked(tmp_path / "a" / "b", tmp_path)       # not a direct child
    with pytest.raises(Fail):
        remove_dir_checked(tmp_path / ".hidden", tmp_path)       # odd name


# ---------- update ----------
def test_update_moves_group_as_one_unit_and_edits_pin(world, capsys):
    world.base()
    world.sync().do_sync()
    make = conftest
    make.make_skill(world.py, "skills/a", "alpha", "v2")
    make.make_skill(world.py, "skills/b", "beta", "v2")
    (world.py / "skills/a/run.sh").write_text("echo")
    new = make.commit(world.py)
    capsys.readouterr()
    assert run_update(world.sync(), [], check=True) == 0
    assert "outdated" in capsys.readouterr().out and world.sha_py in world.manifest.read_text()
    assert run_update(world.sync(), [], check=False) == 0
    out = capsys.readouterr().out
    assert "updated" in out and "skills/a/run.sh".replace("skills/a", "alpha") in out      # executable review
    assert new in world.manifest.read_text() and world.sha_py not in world.manifest.read_text()
    assert "v2" in (world.root / "vendor/alpha/SKILL.md").read_text() and "v2" in (world.root / "vendor/beta/SKILL.md").read_text()
    assert not [p for p in (world.root / "vendor").iterdir() if ".old." in p.name]
    capsys.readouterr()
    run_update(world.sync(), [], check=False)
    assert "current" in capsys.readouterr().out


def test_update_group_stays_together_when_one_skill_breaks(world, capsys):
    world.base()
    world.sync().do_sync()
    shutil.rmtree(world.py / "skills/b")
    conftest.commit(world.py)
    before = world.manifest.read_text()
    run_update(world.sync(), [], check=False)
    out = capsys.readouterr().out
    assert "kept" in out and "the whole group stays together" in out
    assert world.manifest.read_text() == before
    assert (world.root / "vendor/alpha/SKILL.md").is_file() and "v1" in (world.root / "vendor/alpha/SKILL.md").read_text()


def test_update_unreachable_and_unknown_name(world, capsys):
    world.base()
    world.sync().do_sync()
    world.write(world.manifest.read_text().replace(world.py.as_uri(), "file:///nonexistent/repo.git"))
    run_update(world.sync(), ["nothing"], check=False)
    cap = capsys.readouterr()
    assert "not a vendored skill" in cap.err
    run_update(world.sync(), [], check=False)
    assert "upstream unreachable" in capsys.readouterr().out


def test_update_vendored_marketplace(world, capsys):
    world.base("""
        claude_plugins:
          marketplaces:
            - name: astral-sh
              repo: CMKT
              ref: "SHA"
              vendored: true
          plugins: []
        """)
    world.write(world.manifest.read_text().replace("CMKT", world.cmkt.as_uri()).replace("SHA", world.sha_cmkt))
    world.sync().do_sync()
    (world.cmkt / "plugins/astral/.claude-plugin/plugin.json").write_text('{"name":"astral","version":"2"}')
    new = conftest.commit(world.cmkt)
    run_update(world.sync(), [], check=False)
    assert "updated  claude marketplace astral-sh" in capsys.readouterr().out
    assert new in world.manifest.read_text()
    assert '"2"' in (world.root / "vendor/marketplaces/claude/astral-sh/plugins/astral/.claude-plugin/plugin.json").read_text()


def test_units_group_keys(world):
    world.base()
    d = manifest.load(world.manifest)
    assert [(u.key, u.names) for u in units_of(d)] == [("g1", ["alpha", "beta"])]


# ---------- project ----------
PROJ = """
    skills:
      - repo: {py_url}
        ref: "{sha_py}"
        tool: both
        global: false
        project_ignore: [notes.md, data/]
        skills:
          alpha: skills/a
      - repo: {py_url}
        ref: "{sha_py}"
        tool: claude
        skills:
          beta: skills/b
    """


@pytest.fixture
def proj(world, tmp_path):
    world.write(PROJ)
    d = tmp_path / "myproject"
    d.mkdir()
    return world, d


def test_project_installs_flagged_skills_and_ignore_patterns(proj, capsys):
    world, d = proj
    assert run_project(world.sync(), d, [], False, False) == 0
    for sub in (".claude/skills", ".agents/skills"):
        assert (d / sub / "alpha/SKILL.md").is_file()
        assert not (d / sub / "alpha/.upstream").exists()           # marker stays out of projects
        assert not os.path.islink(d / sub / "alpha")                # real files
    assert not (d / ".claude/skills/beta").exists()                 # global skill: not copied
    assert (d / ".gitignore").read_text().splitlines() == ["notes.md", "data/"]
    assert not (paths.state_dir() / "managed.txt").exists() or "alpha" not in (paths.state_dir() / "managed.txt").read_text()
    # a second run skips what is there and does not duplicate ignore lines
    capsys.readouterr()
    run_project(world.sync(), d, [], False, False)
    assert "already there" in capsys.readouterr().out
    assert (d / ".gitignore").read_text().splitlines() == ["notes.md", "data/"]


def test_project_force_replaces_and_dry_run_changes_nothing(proj):
    world, d = proj
    run_project(world.sync(), d, [], False, False)
    (d / ".claude/skills/alpha/SKILL.md").write_text("local")
    run_project(world.sync(dry=True), d, [], True, False)
    assert (d / ".claude/skills/alpha/SKILL.md").read_text() == "local"
    run_project(world.sync(), d, [], True, False)
    assert "v1" in (d / ".claude/skills/alpha/SKILL.md").read_text()


def test_project_only_and_unknown_names(proj, capsys):
    world, d = proj
    assert run_project(world.sync(), d, ["beta"], False, False) == 0
    assert (d / ".claude/skills/beta/SKILL.md").is_file() and not (d / ".claude/skills/alpha").exists()
    assert run_project(world.sync(), d, ["nope"], False, False) == 2
    assert "unknown name(s): nope" in capsys.readouterr().err


def test_project_requires_a_folder(proj):
    world, _ = proj
    with pytest.raises(Fail):
        run_project(world.sync(), world.tmp / "missing", [], False, False)


def test_project_list_does_not_download(proj, capsys):
    world, _ = proj
    assert run_project(world.sync(), None, [], False, True) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "project-only" in out and "global" in out
    assert not (world.root / "vendor").exists()


def test_project_plugin_env_and_gitignore(world, fakes, tmp_path, capsys):
    fakes.add("claude")
    world.base("""
        claude_plugins:
          marketplaces:
            - name: astral-sh
              repo: CMKT
              ref: "SHA"
              vendored: true
          plugins:
            - id: astral@astral-sh
              global: false
              project_env:
                DATA_DIR: "{project}/.data"
              project_ignore: [".data/"]
        """)
    world.write(world.manifest.read_text().replace("CMKT", world.cmkt.as_uri()).replace("SHA", world.sha_cmkt))
    world.sync().do_sync()                                        # registers the marketplace (the plugin itself is project-only)
    assert "astral@astral-sh" not in fakes.state("claude")["plugins"]
    d = tmp_path / "p"
    d.mkdir()
    (d / ".gitignore").write_text("existing")                     # no trailing newline
    assert run_project(world.sync(), d, [], False, False) == 0
    assert (d / ".gitignore").read_text() == "existing\n.data/\n"
    env = json.loads((d / ".claude/settings.local.json").read_text())["env"]
    assert env == {"DATA_DIR": f"{d.resolve()}/.data"}
    assert any("plugin install astral@astral-sh --scope project" in c for c in fakes.calls())
    # a different existing value is kept unless --force
    (d / ".claude/settings.local.json").write_text('{"env":{"DATA_DIR":"mine"}}')
    run_project(world.sync(), d, [], False, False)
    assert json.loads((d / ".claude/settings.local.json").read_text())["env"]["DATA_DIR"] == "mine"
    assert "kept" in capsys.readouterr().err
    run_project(world.sync(), d, [], True, False)
    assert json.loads((d / ".claude/settings.local.json").read_text())["env"]["DATA_DIR"].endswith(".data")


def test_project_codex_plugin_is_skipped(world, tmp_path, capsys):
    world.base("""
        codex_plugins:
          marketplaces:
            - name: cx
              source: owner/cx
          plugins:
            - id: hello@cx
              global: false
        """)
    d = tmp_path / "p"
    d.mkdir()
    assert run_project(world.sync(), d, [], False, False) == 0
    assert "no project scope" in capsys.readouterr().err
