"""Shared engine of the fake `claude` and `codex` plugin CLIs used by tests/run.sh.

It mimics the behaviour recorded from the real CLIs (Claude Code 2.1.291, Codex 0.160.1), including the differences:
  - claude: re-adding a marketplace name from another source re-points it; uninstalling a plugin that is not installed fails.
  - codex:  re-adding a name from another source is refused; removing a plugin that is not installed succeeds;
            a PATH-alias warning goes to stderr, the JSON to stdout.
  - both:   removing a marketplace also uninstalls its plugins; adds and installs are idempotent.
State lives in $FAKE_STATE_DIR/<tool>.json, every call is appended to $FAKE_STATE_DIR/calls.log.
Failure injection: FAKE_FAIL="<tool>:<command>:<name-or-id>,..."  (command: marketplace-add, install, uninstall, marketplace-remove).
Remote marketplace names: FAKE_NAMES="owner/repo=name,..." (default: the repository name).
"""
import json
import os
import re
import sys

CATALOG = {"claude": ".claude-plugin/marketplace.json", "codex": ".agents/plugins/marketplace.json"}


def state_path(tool):
    return os.path.join(os.environ["FAKE_STATE_DIR"], f"{tool}.json")


def load(tool):
    try:
        with open(state_path(tool), encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {"marketplaces": {}, "plugins": {}}


def save(tool, st):
    with open(state_path(tool), "w", encoding="utf-8") as f:
        json.dump(st, f)


def log(tool, argv):
    with open(os.path.join(os.environ["FAKE_STATE_DIR"], "calls.log"), "a", encoding="utf-8") as f:
        f.write(tool + " " + " ".join(argv) + "\n")


def should_fail(tool, command, key):
    for rule in filter(None, os.environ.get("FAKE_FAIL", "").split(",")):
        t, c, k = (rule.split(":") + ["*", "*"])[:3]
        if t == tool and c == command and k in ("*", key):
            return True
    return False


def is_local(src):
    return src.startswith((".", "~")) or os.path.isabs(src)


def name_of(tool, src):
    """Marketplace name: from the catalog for a local folder, from FAKE_NAMES or the repo name for a remote source."""
    if is_local(src):
        try:
            with open(os.path.join(src, CATALOG[tool]), encoding="utf-8") as f:
                return json.load(f)["name"]
        except (OSError, ValueError, KeyError):
            return None
    for pair in filter(None, os.environ.get("FAKE_NAMES", "").split(",")):
        k, _, v = pair.partition("=")
        if k == src:
            return v
    m = re.search(r"([^/]+?)(?:\.git)?/?$", src)
    return m.group(1) if m else src


def canon_remote(src):
    m = re.match(r"^(?:https?://github\.com/)?([^/\s]+/[^/\s]+?)(?:\.git)?/?$", src)
    return f"https://github.com/{m.group(1)}.git" if m else src


def out(obj):
    print(json.dumps(obj))


def parse(argv):
    """Positional words and flags: --ref X, --sparse X (repeatable), --json, --scope/-s X, -y, --marketplace/-m X."""
    pos: list = []
    opts: dict = {"sparse": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--ref", "--scope", "-s", "--marketplace", "-m", "--registry", "--config", "--accept-command"):
            opts[a.lstrip("-")] = argv[i + 1]
            i += 2
        elif a == "--sparse":
            opts["sparse"].append(argv[i + 1])
            i += 2
        elif a.startswith("-"):
            opts[a.lstrip("-")] = True
            i += 1
        else:
            pos.append(a)
            i += 1
    return pos, opts


def drop_marketplace_plugins(st, name):
    for pid in [p for p in st["plugins"] if p.endswith("@" + name)]:
        del st["plugins"][pid]
