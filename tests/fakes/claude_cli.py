#!/usr/bin/env python3
"""Fake `claude plugin ...` for tests (see _fake.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fake as F  # noqa: E402

TOOL = "claude"
argv = sys.argv[1:]
F.log(TOOL, argv)
if argv[:1] != ["plugin"]:
    sys.exit("fake claude: only `plugin` is supported")
argv = argv[1:]
st = F.load(TOOL)


def result(rc, command, outcome, **extra):
    F.out({"command": command, "outcome": outcome, **extra})
    sys.exit(rc)


if argv[:2] == ["marketplace", "list"]:
    rows = []
    for name, m in st["marketplaces"].items():
        row = {"name": name, "source": m["kind"], "installLocation": m["src"]}
        row["path" if m["kind"] == "directory" else "repo"] = m["src"]
        rows.append(row)
    print(__import__("json").dumps(rows))
elif argv[:2] == ["marketplace", "add"]:
    pos, opts = F.parse(argv[2:])
    src = pos[0]
    name = F.name_of(TOOL, src)
    if name is None:
        result(1, "marketplace-add", "failed", message="no marketplace catalog found", failureCode="invalid")
    if F.should_fail(TOOL, "marketplace-add", name):
        result(1, "marketplace-add", "failed", marketplace=name, message="injected failure")
    kind = "directory" if F.is_local(src) else "github"
    cur = st["marketplaces"].get(name)
    msg = "Successfully added marketplace: %s (declared in user settings)" % name
    if cur and cur["src"] == src:
        msg = "Marketplace '%s' already on disk" % name
    st["marketplaces"][name] = {"kind": kind, "src": src}      # same name from another source: re-pointed in place
    F.save(TOOL, st)
    result(0, "marketplace-add", "ok", marketplace=name, message=msg)
elif argv[:2] == ["marketplace", "remove"]:
    name = argv[2]
    if name not in st["marketplaces"]:
        result(1, "marketplace-remove", "failed", marketplace=name, message="Marketplace '%s' not found" % name, failureCode="not_configured")
    if F.should_fail(TOOL, "marketplace-remove", name):
        result(1, "marketplace-remove", "failed", marketplace=name, message="injected failure")
    del st["marketplaces"][name]
    F.drop_marketplace_plugins(st, name)
    F.save(TOOL, st)
    result(0, "marketplace-remove", "ok", marketplace=name, message="Successfully removed marketplace: " + name)
elif argv[:1] == ["list"]:
    print(__import__("json").dumps([{"id": pid, "version": p["version"], "scope": "user", "enabled": p["enabled"],
                                     "installPath": "/fake/" + pid} for pid, p in st["plugins"].items()]))
elif argv[:1] == ["install"]:
    pid = argv[1]
    mp = pid.split("@")[-1]
    if mp not in st["marketplaces"]:
        result(1, "install", "failed", plugin=pid, message="Marketplace '%s' not found" % mp)
    if F.should_fail(TOOL, "install", pid):
        result(1, "install", "failed", plugin=pid, message="injected failure")
    st["plugins"].setdefault(pid, {"version": "1.0.0", "enabled": True})
    F.save(TOOL, st)
    result(0, "install", "ok", plugin=pid, pluginId=pid, scope="user")
elif argv[:2] == ["marketplace", "update"]:
    name = argv[2]
    if name not in st["marketplaces"]:
        result(1, "marketplace-update", "failed", marketplace=name, message="Marketplace '%s' not found" % name)
    if F.should_fail(TOOL, "marketplace-update", name):
        result(1, "marketplace-update", "failed", marketplace=name, message="injected failure")
    result(0, "marketplace-update", "ok", marketplace=name)
elif argv[:1] == ["update"]:
    pid = argv[1]
    if pid not in st["plugins"]:
        result(1, "update", "failed", plugin=pid, message="not installed")
    if F.should_fail(TOOL, "update", pid):
        result(1, "update", "failed", plugin=pid, message="injected failure")
    st["plugins"][pid]["version"] = "updated"
    F.save(TOOL, st)
    result(0, "update", "ok", plugin=pid)
elif argv[:1] == ["uninstall"]:
    pid = argv[1]
    if pid not in st["plugins"]:
        result(1, "uninstall", "failed", plugin=pid, message="not found in installed plugins", failureCode="not_installed")
    if F.should_fail(TOOL, "uninstall", pid):
        result(1, "uninstall", "failed", plugin=pid, message="injected failure")
    del st["plugins"][pid]
    F.save(TOOL, st)
    result(0, "uninstall", "ok", plugin=pid)
else:
    sys.exit("fake claude: unsupported command: " + " ".join(argv))
