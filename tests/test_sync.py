import os
import time
from pathlib import Path

import pytest

from agents_setup import links, paths


def skills_dir():
    return paths.claude_dir()


def managed_text(home):
    f = paths.state_dir() / "managed.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def test_first_sync_fetches_links_and_writes_import(world, scratch_home, capsys):
    world.base()
    s = world.sync()
    assert s.do_sync() == 0
    out = capsys.readouterr().out
    assert "fetched      alpha" in out
    for n in ("alpha", "beta"):
        assert os.path.islink(skills_dir() / n)
        assert Path(os.readlink(skills_dir() / n)) == world.root / "vendor" / n
        assert (world.root / "vendor" / n / ".upstream").read_text().splitlines()[2] == f"ref={world.sha_py}"
    assert not (paths.codex_dir() / "alpha").exists()          # tool: claude only
    assert os.path.islink(scratch_home / ".codex" / "AGENTS.md")
    imp = (scratch_home / ".claude" / "CLAUDE.md").read_text()
    assert imp.splitlines()[-1] == f"@{(world.root / 'config/claude/CLAUDE.md').as_posix()}"
    rec = managed_text(scratch_home)
    assert f"link\t{(skills_dir() / 'alpha').as_posix()}" in rec and "import\t" in rec


def test_second_sync_changes_nothing(world, capsys):
    world.base()
    world.sync().do_sync()
    capsys.readouterr()
    s = world.sync()
    assert s.do_sync() == 0
    out = capsys.readouterr().out
    assert "installed/updated 0, removed 0, skipped 0, failed 0" in out
    assert "Restart" not in out


def test_dry_run_touches_nothing(world, scratch_home, capsys):
    world.base()
    assert world.sync(dry=True).do_sync() == 0
    out = capsys.readouterr().out
    assert "would fetch  alpha" in out and "[dry-run]" in out
    assert not (world.root / "vendor").exists()
    assert not skills_dir().exists() and not (scratch_home / ".claude" / "CLAUDE.md").exists()
    assert not (paths.state_dir() / "managed.txt").exists()


def test_removed_skill_unlinks_only_what_we_made(world, scratch_home, capsys):
    world.base()
    world.sync().do_sync()
    mine = skills_dir() / "handmade"
    mine.mkdir()
    # the manifest drops beta
    world.write(world.manifest.read_text().replace("      beta: skills/b\n", ""))
    s = world.sync()
    assert s.do_sync() == 0
    assert os.path.islink(skills_dir() / "alpha") and not os.path.lexists(skills_dir() / "beta")
    assert mine.is_dir()
    assert s.n_rm == 1


def test_unmanaged_real_folder_and_foreign_link_are_left_alone(world, capsys):
    world.base()
    (skills_dir() / "alpha").mkdir(parents=True)
    os.symlink(str(world.tmp), str(skills_dir() / "beta"))
    s = world.sync()
    assert s.do_sync() == 0
    err = capsys.readouterr().err
    assert "exists and is not managed" in err and "points elsewhere and is not managed" in err
    assert (skills_dir() / "alpha").is_dir() and not os.path.islink(skills_dir() / "alpha")
    assert os.readlink(skills_dir() / "beta") == str(world.tmp)
    assert s.n_skip == 2


def test_managed_link_pointing_elsewhere_is_relinked(world):
    world.base()
    world.sync().do_sync()
    os.unlink(skills_dir() / "alpha")
    os.symlink(str(world.tmp), str(skills_dir() / "alpha"))
    # recorded as ours, so it gets fixed
    s = world.sync()
    s.do_sync()
    assert Path(os.readlink(skills_dir() / "alpha")) == world.root / "vendor" / "alpha"


def test_own_skills_are_linked_and_may_not_shadow_manifest_skills(world, capsys):
    world.base()
    (world.root / "skills/shared/mine").mkdir()
    (world.root / "skills/shared/mine/SKILL.md").write_text("x")
    s = world.sync()
    s.do_sync()
    assert os.path.islink(paths.claude_dir() / "mine") and os.path.islink(paths.codex_dir() / "mine")
    (world.root / "skills/claude/alpha").mkdir()
    (world.root / "skills/claude/alpha/SKILL.md").write_text("x")
    s = world.sync()
    assert s.do_sync() == 1
    assert "an own skill in skills/ has the same name" in capsys.readouterr().out


def test_unreachable_upstream_uses_committed_snapshot_and_keeps_links(world, capsys):
    world.base()
    world.sync().do_sync()
    world.write(world.manifest.read_text().replace(world.sha_py, "a" * 40).replace(world.py.as_uri(), "file:///nonexistent/repo.git"))
    s = world.sync()
    assert s.do_sync() == 0                      # committed snapshot (SKILL.md) is still there
    assert "using committed snapshot" in capsys.readouterr().err
    assert os.path.islink(skills_dir() / "alpha")


def test_unfetchable_skill_without_snapshot_fails_but_keeps_existing_link(world, capsys):
    world.base()
    world.sync().do_sync()
    import shutil
    shutil.rmtree(world.root / "vendor")
    world.write(world.manifest.read_text().replace(world.py.as_uri(), "file:///nonexistent/repo.git"))
    s = world.sync()
    assert s.do_sync() == 1
    assert os.path.islink(skills_dir() / "alpha")                    # a network blip must not uninstall it
    assert "cannot fetch" in capsys.readouterr().out


def test_import_file_rules(world, scratch_home):
    world.base()
    world.sync().do_sync()
    imp = scratch_home / ".claude" / "CLAUDE.md"
    # your own rules under the import line: hands off
    imp.write_text(imp.read_text() + "my rule\n")
    s = world.sync()
    s.do_sync()
    assert "my rule" in imp.read_text() and s.n_skip == 0
    # import line removed by hand: skipped with a warning
    imp.write_text("only mine\n")
    s = world.sync()
    s.do_sync()
    assert imp.read_text() == "only mine\n" and s.n_skip == 1


def test_import_updates_when_source_moves_and_file_untouched(world, scratch_home):
    world.base()
    world.sync().do_sync()
    new = world.root / "config/claude/OTHER.md"
    new.write_text("x")
    world.write(world.manifest.read_text().replace("config/claude/CLAUDE.md", "config/claude/OTHER.md"))
    s = world.sync()
    s.do_sync()
    assert (scratch_home / ".claude" / "CLAUDE.md").read_text().splitlines()[-1].endswith("OTHER.md")


def test_deleted_config_entry_removes_untouched_generated_file_only(world, scratch_home):
    world.base()
    world.sync().do_sync()
    imp = scratch_home / ".claude" / "CLAUDE.md"
    cut = world.manifest.read_text().split("  - source: config/claude/CLAUDE.md")[0]
    world.write(cut)
    world.sync().do_sync()
    assert not imp.exists() and os.path.islink(scratch_home / ".codex" / "AGENTS.md")


def test_deleted_config_entry_keeps_edited_file(world, scratch_home, capsys):
    world.base()
    world.sync().do_sync()
    imp = scratch_home / ".claude" / "CLAUDE.md"
    imp.write_text(imp.read_text() + "mine\n")
    world.write(world.manifest.read_text().split("  - source: config/claude/CLAUDE.md")[0])
    world.sync().do_sync()
    assert imp.exists() and "was edited by you" in capsys.readouterr().err


def test_tools_run_once_and_removal_only_warns(world, tmp_path, capsys):
    marker = tmp_path / "ran.txt"
    world.base(f"""
        tools:
          - name: demo
            run: python -c "open(r'{marker}', 'a').write('x')"
        """)
    world.sync().do_sync()
    world.sync().do_sync()
    assert marker.read_text() == "x"
    world.write(world.manifest.read_text().split("tools:")[0])
    world.sync().do_sync()
    assert "uninstall it manually" in capsys.readouterr().err


def test_failing_tool_is_reported_and_retried(world, capsys):
    world.base("""
        tools:
          - name: bad
            run: python -c "import sys; sys.exit(3)"
        """)
    s = world.sync()
    assert s.do_sync() == 1
    assert "tool bad" in capsys.readouterr().out
    assert "tool\tbad" not in managed_text(None)


# ---------- the Windows fallback: copies instead of links ----------
def test_copy_fallback_creates_copies_and_refreshes_only_when_source_is_newer(world, scratch_home, monkeypatch):
    monkeypatch.setenv("AGENTS_FORCE_COPY", "1")
    world.base()
    s = world.sync()
    assert s.do_sync() == 0
    a = skills_dir() / "alpha"
    assert a.is_dir() and not os.path.islink(a) and (a / "SKILL.md").is_file()
    assert os.path.isfile(scratch_home / ".codex" / "AGENTS.md") and not os.path.islink(scratch_home / ".codex" / "AGENTS.md")
    assert f"copy\t{a.as_posix()}" in managed_text(scratch_home)
    # nothing changed: nothing is copied again
    s = world.sync()
    s.do_sync()
    assert s.n_new == 0
    # the source gets newer content: the copy is refreshed
    src = world.root / "vendor" / "alpha" / "SKILL.md"
    src.write_text("new content")
    future = time.time() + 100
    os.utime(src, (future, future))
    s = world.sync()
    s.do_sync()
    assert (a / "SKILL.md").read_text() == "new content" and s.n_new == 1
    # the copy is newer than the source (someone edited it): it is NOT overwritten
    (a / "SKILL.md").write_text("local edit")
    later = future + 100
    os.utime(a / "SKILL.md", (later, later))
    s = world.sync()
    s.do_sync()
    assert (a / "SKILL.md").read_text() == "local edit"


def test_copy_fallback_removes_copies_when_entry_is_deleted(world, monkeypatch):
    monkeypatch.setenv("AGENTS_FORCE_COPY", "1")
    world.base()
    world.sync().do_sync()
    world.write(world.manifest.read_text().replace("      beta: skills/b\n", ""))
    s = world.sync()
    s.do_sync()
    assert not (skills_dir() / "beta").exists() and (skills_dir() / "alpha").is_dir()


def test_copy_fallback_never_overwrites_an_unmanaged_folder(world, monkeypatch, capsys):
    monkeypatch.setenv("AGENTS_FORCE_COPY", "1")
    world.base()
    (skills_dir() / "alpha").mkdir(parents=True)
    (skills_dir() / "alpha" / "mine.txt").write_text("keep")
    s = world.sync()
    s.do_sync()
    assert (skills_dir() / "alpha" / "mine.txt").read_text() == "keep" and s.n_skip >= 1


def test_source_is_newer_helper(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "f").write_text("1"); (b / "f").write_text("1")
    t = time.time()
    os.utime(a / "f", (t, t)); os.utime(b / "f", (t, t)); os.utime(a, (t, t)); os.utime(b, (t, t))
    assert not links.source_is_newer(a, b)                 # same time, same content
    (b / "f").write_text("2"); os.utime(b / "f", (t, t))
    assert links.source_is_newer(a, b)                     # same time, different content
    os.utime(a / "f", (t + 10, t + 10))
    assert links.source_is_newer(a, b)                     # newer


# ---------- doctor ----------
def test_doctor_reports_dangling_and_missing(world, capsys):
    world.base()
    world.sync().do_sync()
    capsys.readouterr()
    assert world.sync().doctor() == 0
    assert "healthy" in capsys.readouterr().out
    import shutil
    shutil.rmtree(world.root / "vendor" / "alpha")
    os.unlink(skills_dir() / "beta")
    assert world.sync().doctor() == 1
    out = capsys.readouterr().out
    assert "DANGLING" in out and "MISSING" in out and "problems found" in out


def test_invalid_manifest_stops_with_error(world):
    from agents_setup.common import Fail
    world.manifest.write_text("skills: 3\n")
    with pytest.raises(Fail):
        world.sync().do_sync()
