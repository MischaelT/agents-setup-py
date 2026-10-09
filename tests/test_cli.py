import pytest

from agents_setup import cli


def test_default_command_is_sync_and_flags_combine(world, capsys):
    world.base()
    assert cli.main(["--root", str(world.root), "--dry-run", "--force"]) == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert not (world.root / "vendor").exists()


def test_sync_subcommand_flags_after_the_command(world, capsys):
    world.base()
    assert cli.main(["--root", str(world.root), "sync", "--dry-run"]) == 0
    assert "[dry-run]" in capsys.readouterr().out


def test_real_run_then_doctor_then_update_check(world, capsys):
    world.base()
    assert cli.main(["--root", str(world.root)]) == 0
    assert cli.main(["--root", str(world.root), "doctor"]) == 0
    assert cli.main(["--root", str(world.root), "update", "--check"]) == 0
    assert "current" in capsys.readouterr().out


def test_invalid_manifest_exit_code(world, capsys):
    world.manifest.write_text("bogus: 1\n")
    assert cli.main(["--root", str(world.root)]) == 1
    assert "unknown top-level key" in capsys.readouterr().err


def test_project_usage_errors(world, capsys):
    world.base()
    assert cli.main(["--root", str(world.root), "project", str(world.tmp / "nope")]) == 2
    assert "existing project folder" in capsys.readouterr().err


def test_add_rejects_unknown_target(world):
    world.base()
    with pytest.raises(SystemExit):
        cli.main(["--root", str(world.root), "add", "everyone", "o/r", "p"])


def test_add_skill_updates_manifest_and_syncs(world, capsys):
    import conftest
    conftest.make_skill(world.py, "skills/c", "gamma")
    conftest.commit(world.py)
    world.base()
    assert cli.main(["--root", str(world.root), "add", "claude", world.py.as_uri(), "skills/c", "gamma"]) == 0
    assert "gamma: skills/c" in world.manifest.read_text()
    assert (world.root / "vendor/gamma/SKILL.md").is_file()
