import os

import pytest

from agents_setup import plugins as P

CLAUDE = """
claude_plugins:
  marketplaces:
    - name: astral-sh
      repo: {cmkt}
      ref: "{sha_cmkt}"
      vendored: true
    - name: remote-mkt
      repo: owner/remote-mkt
  plugins:
    - astral@astral-sh
    - tool@remote-mkt
"""

CODEX = """
codex_plugins:
  marketplaces:
    - name: cx
      source: {xmkt}
      ref: "{sha_xmkt}"
      vendored: true
  plugins:
    - hello@cx
"""


def setup(world, extra):
    world.base(extra)
    # the saved-copy source must be a git URL
    world.write(world.manifest.read_text().replace(f"repo: {world.cmkt.as_posix()}", f"repo: {world.cmkt.as_uri()}")
                .replace(f"source: {world.xmkt.as_posix()}", f"source: {world.xmkt.as_uri()}"))


@pytest.mark.parametrize("tool,section,plugin,mkt", [("claude", CLAUDE, "astral@astral-sh", "astral-sh"), ("codex", CODEX, "hello@cx", "cx")])
def test_install_is_idempotent_and_records_only_ours(world, fakes, tool, section, plugin, mkt, capsys):
    fakes.add(tool)
    setup(world, section)
    if tool == "claude":   # the remote marketplace is not part of this check
        world.write(world.manifest.read_text().replace("    - name: remote-mkt\n      repo: owner/remote-mkt\n", "").replace("    - tool@remote-mkt\n", ""))
    s = world.sync()
    assert s.do_sync() == 0
    assert mkt in fakes.state(tool)["marketplaces"] and plugin in fakes.state(tool)["plugins"]
    assert (world.root / "vendor/marketplaces" / tool / mkt / ".upstream").is_file()
    rec = (__import__("agents_setup.paths", fromlist=["x"]).state_dir() / "managed.txt").read_text()
    assert f"{tool}-plugin\t{plugin}" in rec and f"{tool}-marketplace\t{mkt}\t" in rec
    capsys.readouterr()
    n = len(fakes.mutating())
    s = world.sync()
    assert s.do_sync() == 0
    assert len(fakes.mutating()) == n                # a second run changes nothing
    assert s.n_new == 0


@pytest.mark.parametrize("tool,section,plugin", [("claude", CLAUDE, "astral@astral-sh"), ("codex", CODEX, "hello@cx")])
def test_dry_run_makes_no_cli_changes(world, fakes, tool, section, plugin, capsys):
    fakes.add(tool)
    setup(world, section)
    assert world.sync(dry=True).do_sync() in (0, 1)
    assert fakes.mutating() == []
    assert "would add" in capsys.readouterr().out


@pytest.mark.parametrize("tool,section,plugin,mkt", [("claude", CLAUDE, "astral@astral-sh", "astral-sh"), ("codex", CODEX, "hello@cx", "cx")])
def test_removed_from_manifest_is_uninstalled_but_unmanaged_is_kept(world, fakes, tool, section, plugin, mkt):
    fakes.add(tool)
    setup(world, section)
    if tool == "claude":
        world.write(world.manifest.read_text().replace("    - name: remote-mkt\n      repo: owner/remote-mkt\n", "").replace("    - tool@remote-mkt\n", ""))
    world.sync().do_sync()
    # a plugin installed by hand under our marketplace
    st = fakes.state(tool)
    st["plugins"]["manual@" + mkt] = {"version": "1", "enabled": True}
    (fakes.state_dir / f"{tool}.json").write_text(__import__("json").dumps(st))
    # drop our plugin from the manifest
    world.write(world.manifest.read_text().replace(f"    - {plugin}\n", "").replace("  plugins:\n", "  plugins: []\n", 1) if False else
                world.manifest.read_text().replace(f"    - {plugin}\n", ""))
    s = world.sync()
    s.do_sync()
    assert plugin not in fakes.state(tool)["plugins"]
    assert "manual@" + mkt in fakes.state(tool)["plugins"]


def test_marketplace_with_foreign_plugins_is_not_removed(world, fakes, capsys):
    fakes.add("claude")
    setup(world, CLAUDE)
    world.sync().do_sync()
    st = fakes.state("claude")
    st["plugins"]["manual@astral-sh"] = {"version": "1", "enabled": True}
    (fakes.state_dir / "claude.json").write_text(__import__("json").dumps(st))
    text = world.manifest.read_text()
    cut = text.split("claude_plugins:")[0]
    world.write(cut)
    s = world.sync()
    s.do_sync()
    assert "astral-sh" in fakes.state("claude")["marketplaces"]
    assert "plugins installed outside this repo still use it" in capsys.readouterr().err


def test_failed_install_is_reported_and_retried(world, fakes, monkeypatch, capsys):
    fakes.add("claude")
    setup(world, CLAUDE)
    monkeypatch.setenv("FAKE_FAIL", "claude:install:astral@astral-sh")
    s = world.sync()
    assert s.do_sync() == 1
    assert "claude plugin astral@astral-sh" in capsys.readouterr().out
    rec = (__import__("agents_setup.paths", fromlist=["x"]).state_dir() / "managed.txt").read_text()
    assert "claude-plugin\tastral@astral-sh" not in rec
    monkeypatch.delenv("FAKE_FAIL")
    assert world.sync().do_sync() == 0
    assert "astral@astral-sh" in fakes.state("claude")["plugins"]


def test_missing_cli_warns_and_keeps_records(world, fakes, capsys):
    fakes.add("claude")
    setup(world, CLAUDE)
    world.sync().do_sync()
    for f in (os.path.join(os.environ["PATH"].split(os.pathsep)[0], n) for n in os.listdir(os.environ["PATH"].split(os.pathsep)[0])):
        os.unlink(f)
    capsys.readouterr()
    s = world.sync()
    assert s.do_sync() == 0
    assert "claude CLI not found" in capsys.readouterr().err
    rec = (__import__("agents_setup.paths", fromlist=["x"]).state_dir() / "managed.txt").read_text()
    assert "claude-plugin\tastral@astral-sh" in rec          # not forgotten


def test_existing_marketplace_from_another_source_is_left_alone(world, fakes, capsys):
    fakes.add("claude")
    setup(world, CLAUDE)
    (fakes.state_dir / "claude.json").write_text(__import__("json").dumps(
        {"marketplaces": {"astral-sh": {"kind": "github", "src": "someone/else"}}, "plugins": {}}))
    s = world.sync()
    s.do_sync()
    assert "already exists with a different source" in capsys.readouterr().err
    assert fakes.state("claude")["marketplaces"]["astral-sh"]["src"] == "someone/else"
    assert "astral@astral-sh" not in fakes.state("claude")["plugins"]


def test_list_failure_changes_nothing(world, fakes, capsys):
    fakes.add("claude")
    setup(world, CLAUDE)
    (fakes.state_dir / "claude.json").write_text("{not json")
    s = world.sync()
    assert s.do_sync() == 1
    assert fakes.mutating() == []
    assert "cannot read what is installed" in capsys.readouterr().out


def test_vendored_pin_change_refreshes_plugins(world, fakes, capsys):
    fakes.add("claude")
    setup(world, CLAUDE)
    world.sync().do_sync()
    # upstream gets a new commit; the manifest pin moves
    import conftest
    (world.cmkt / "plugins/astral/.claude-plugin/plugin.json").write_text('{"name":"astral","version":"2.0.0"}')
    new = conftest.commit(world.cmkt)
    world.write(world.manifest.read_text().replace(world.sha_cmkt, new))
    capsys.readouterr()
    s = world.sync()
    assert s.do_sync() == 0
    assert "refreshed    claude marketplace astral-sh" in capsys.readouterr().out
    assert fakes.state("claude")["plugins"]["astral@astral-sh"]["version"] == "updated"


def test_doctor_for_plugins(world, fakes, capsys):
    fakes.add("claude")
    setup(world, CLAUDE)
    assert world.sync().doctor() == 1                       # nothing registered yet
    out = capsys.readouterr().out
    assert "PROBLEM  marketplace astral-sh is not registered" in out
    world.sync().do_sync()
    capsys.readouterr()
    s = world.sync()
    s.nofetch = True
    assert s.doctor() == 0
    assert "ok       plugin astral@astral-sh" in capsys.readouterr().out


# ---------- pure helpers ----------
@pytest.mark.parametrize("src,want", [
    ("owner/repo", "github:owner/repo"),
    ("https://github.com/owner/repo.git", "github:owner/repo"),
    ("https://github.com/owner/repo", "github:owner/repo"),
    ("git@github.com:owner/repo.git", "github:owner/repo"),
    ("https://example.com/x.git", "git:https://example.com/x.git"),
])
def test_canon(src, want):
    assert P.canon(src) == want


def test_canon_local_path(tmp_path):
    assert P.canon(str(tmp_path)) == "path:" + tmp_path.as_posix()


def test_catalog_reports_external_and_escaping_sources(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / "in").mkdir()
    (tmp_path / ".claude-plugin/marketplace.json").write_text(
        '{"name":"m","plugins":[{"name":"a","source":"./in"},{"name":"b","source":"../out"},'
        '{"name":"c","source":{"source":"github","repo":"o/r"}},{"name":"d","source":{"source":"local","path":"./in"}}]}')
    name, plugins = P.read_catalog("claude", tmp_path)
    assert name == "m"
    assert plugins == [("a", "saved"), ("b", "external:other"), ("c", "external:github"), ("d", "saved")]


def test_settings_env(tmp_path):
    f = tmp_path / "s" / "settings.local.json"
    assert P.settings_env(f, ["A=1"], False) == [("set", "A", "1")]
    assert P.settings_env(f, ["A=1"], False) == [("same", "A", "1")]
    assert P.settings_env(f, ["A=2"], False) == [("kept", "A", "1")]
    assert P.settings_env(f, ["A=2"], True) == [("set", "A", "2")]
    f.write_text("{bad")
    from agents_setup.common import Fail
    with pytest.raises(Fail):
        P.settings_env(f, ["A=1"], False)
    assert f.read_text() == "{bad"


def test_marketplaces_json_shapes():
    claude = '[{"name":"a","source":"github","repo":"o/r"},{"name":"b","source":"directory","path":"/x/y"}]'
    assert P.parse_marketplaces_json(claude, "claude") == {"a": "github:o/r", "b": "path:/x/y"}
    codex = ('{"marketplaces":[{"name":"c","marketplaceSource":{"sourceType":"local","source":"/p"}},'
             '{"name":"d","marketplaceSource":{"sourceType":"git","source":"https://github.com/o/r.git"}},{"name":"e"}]}')
    assert P.parse_marketplaces_json(codex, "codex") == {"c": "path:/p", "d": "github:o/r", "e": "builtin"}
    assert P.parse_plugins_json('{"installed":[{"pluginId":"x@y","enabled":false}]}') == {"x@y": False}
    assert P.parse_plugins_json("garbage") is None
