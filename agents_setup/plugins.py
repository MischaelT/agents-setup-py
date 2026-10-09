"""Claude Code and Codex plugins and saved (vendored) marketplaces (port of lib/plugins.sh + lib/plugins_json.py).

Rules (checked against the real CLIs):
 - Read what is installed first (`<cli> plugin list --json`, stdout only), then change only what the manifest asks for.
 - Anything not recorded in managed.txt as installed by this tool is never touched.
 - Removing a marketplace uninstalls ALL of its plugins in both CLIs, so a marketplace is only removed when no plugin
   installed by hand still depends on it.
"""
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .common import (Fail, NAME_RE, fetch_snapshot, full_url, remove_dir_checked, rmtree, run, say, upstream_matches,
                     vendor_dirty, warn)
from .paths import posix

CATALOG = {"claude": ".claude-plugin/marketplace.json", "codex": ".agents/plugins/marketplace.json"}


# ---------- JSON helpers (plugins_json.py) ----------
def canon(source):
    """Canonical identity of a marketplace source: github:owner/repo | git:<url> | path:<abs folder>."""
    s = (source or "").strip()
    if s.startswith(("/", ".", "~")) or re.match(r"^[A-Za-z]:[\\/]", s):
        return "path:" + posix(os.path.abspath(os.path.expanduser(s)))
    m = re.match(r"^(?:https?://github\.com/|git@github\.com:)([^/\s]+/[^/\s]+?)(?:\.git)?/?$", s)
    if m:
        return "github:" + m.group(1)
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", s):
        return "github:" + s
    return "git:" + s


def parse_plugins_json(text):
    """`<cli> plugin list --json` -> {id: enabled}. None when it is not JSON."""
    try:
        d = json.loads(text)
    except ValueError:
        return None
    items = d if isinstance(d, list) else d.get("installed", [])
    out = {}
    for p in items:
        pid = p.get("id") or p.get("pluginId")
        if pid:
            out[pid] = bool(p.get("enabled", True))
    return out


def parse_marketplaces_json(text, tool):
    """`<cli> plugin marketplace list --json` -> {name: canonical ident}. None when it is not JSON."""
    try:
        d = json.loads(text)
    except ValueError:
        return None
    items = d if isinstance(d, list) else d.get("marketplaces", [])
    out = {}
    for m in items:
        name = m.get("name")
        if not name:
            continue
        if tool == "claude":
            kind = m.get("source")
            src = m.get("repo") or m.get("path") or m.get("url") or ""
            ident = canon(src) if kind == "github" else ("path:" + posix(src) if kind == "directory" else "git:" + src)
        else:
            ms = m.get("marketplaceSource") or {}
            kind, src = ms.get("sourceType"), ms.get("source", "")
            ident = "builtin" if not kind else ("path:" + posix(src) if kind == "local" else canon(src))
        out[name] = ident
    return out


def inside(folder, rel):
    """A relative plugin source counts as saved only if it stays inside the marketplace folder ('../x' is not in the copy)."""
    if not rel.startswith("."):
        return False
    root = os.path.realpath(folder)
    target = os.path.realpath(os.path.join(folder, rel))
    return target == root or target.startswith(root + os.sep)


def read_catalog(tool, folder):
    """-> (catalog name, [(plugin name, 'saved' | 'external:<kind>')]); raises Fail when unreadable."""
    path = Path(folder) / CATALOG[tool]
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Fail(f"cannot read the marketplace catalog {CATALOG[tool]}: {e}", 1)
    plugins = []
    for p in d.get("plugins", []):
        src = p.get("source")
        if isinstance(src, str):
            kind = "saved" if inside(folder, src) else "external:other"
        elif isinstance(src, dict):
            k = src.get("source", "other")
            kind = "saved" if (k == "local" and inside(folder, str(src.get("path", "")))) else f"external:{k}"
        else:
            kind = "external:other"
        plugins.append((p.get("name", ""), kind))
    return d.get("name", ""), plugins


def settings_env(path, pairs, force):
    """Merge VAR=VALUE pairs into the "env" object of a Claude settings file (created when missing). Existing different
    values stay unless forced. Returns [(status, key, value)] with status set | same | kept."""
    path = Path(path)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise Fail(f"{path} is not valid JSON ({e}); not touched", 1)
        if not isinstance(data, dict) or not isinstance(data.get("env", {}), dict):
            raise Fail(f"{path} has an unexpected shape; not touched", 1)
    env = data.setdefault("env", {})
    result = []
    for pair in pairs:
        k, _, v = pair.partition("=")
        if k in env and env[k] != v and not force:
            result.append(("kept", k, env[k]))
        elif env.get(k) == v:
            result.append(("same", k, v))
        else:
            env[k] = v
            result.append(("set", k, v))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return result


# ---------- CLI wrappers ----------
def cli_path(tool):
    return shutil.which(tool)


def cli(tool, *args, cwd=None):
    exe = cli_path(tool) or tool
    return run([exe, *args], cwd=cwd)


def plugin_install(tool, pid):
    return (cli("claude", "plugin", "install", pid, "--json") if tool == "claude"
            else cli("codex", "plugin", "add", pid, "--json")).returncode == 0


def plugin_uninstall(tool, pid):
    return (cli("claude", "plugin", "uninstall", pid, "--json") if tool == "claude"
            else cli("codex", "plugin", "remove", pid, "--json")).returncode == 0


def marketplace_remove(tool, name):
    return cli(tool, "plugin", "marketplace", "remove", name, "--json").returncode == 0


def refresh_marketplace(tool, name, ids):
    """The saved folder of a marketplace we manage moved to a new commit but its path did not. Claude copies a plugin into its
    own cache on install, so the installed copy stays old until updated; Codex has no update command, so its plugins are
    removed and added again."""
    if tool == "claude":
        if cli("claude", "plugin", "marketplace", "update", name, "--json").returncode != 0:
            return False
        return all(cli("claude", "plugin", "update", i, "--json").returncode == 0 for i in ids)
    return all(plugin_uninstall("codex", i) and plugin_install("codex", i) for i in ids)


def marketplace_register(tool, name, src, ref, sparse):
    """Add a marketplace and check it is the one we meant. 0 ok, 1 failed, 2 registered under another name (undone)."""
    args = []
    if tool == "codex":
        if ref != "-":
            args += ["--ref", ref]
        for p in [x for x in sparse.split(",") if x and x != "-"]:
            args += ["--sparse", p]
    r = cli(tool, "plugin", "marketplace", "add", src, *args, "--json")
    if r.returncode != 0:
        return 1
    last = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        d = json.loads(last)
        got = d.get("marketplaceName") or d.get("marketplace") or ""
    except (ValueError, AttributeError):
        got = ""
    if got == name:
        return 0
    if got:
        marketplace_remove(tool, got)      # it registered under another name: undo
    return 2


# ---------- saved (vendored) marketplaces ----------
class Plugins:
    """The plugin part of one sync run. `ctx` is the Sync object (root, vendor, dry, managed, counters, fail)."""

    def __init__(self, ctx, data):
        self.c = ctx
        self.wanted_m = {"claude": [], "codex": []}   # tool -> [marketplace dict]
        self.wanted_p = {"claude": [], "codex": []}   # tool -> [plugin id] (global ones)
        self.bad_snapshot = set()
        for m in data["marketplaces"]:
            m = dict(m)
            m["ident"] = ("path:" + posix(self.mdir(m["tool"], m["name"]))) if m["vendored"] else canon(m["source"])
            self.wanted_m[m["tool"]].append(m)
        for p in data["plugins"]:
            if p["global"]:
                self.wanted_p[p["tool"]].append(p["id"])

    # -- folders --
    def mdir(self, tool, name):
        return self.c.vendor / "marketplaces" / tool / name

    def snapshot_current(self, tool, name, url, ref):
        d = self.mdir(tool, name)
        return (d / CATALOG[tool]).is_file() and upstream_matches(d, url, ".", ref)

    def verify_snapshot(self, tool, name, folder):
        catname, _ = read_catalog(tool, folder)
        if catname != name:
            raise Fail(f"its catalog is named '{catname}' but the manifest calls it '{name}'", 1)

    def stage_snapshot(self, tool, name, url, ref, stage):
        """Fetch and verify into <stage> only; raises Fail."""
        fetch_snapshot(name, url, ".", ref, stage, "repo")
        try:
            self.verify_snapshot(tool, name, stage)
        except Fail:
            rmtree(stage)
            raise

    def ensure_snapshot(self, tool, name, url, ref):
        """vendor/marketplaces/<tool>/<name> exactly at the pin. Returns False when unusable."""
        c = self.c
        dest = self.mdir(tool, name)
        if self.snapshot_current(tool, name, url, ref):
            return True
        if c.nofetch:
            return True
        if c.dry:
            say(f"  would fetch  {tool} marketplace {name} @ {ref[:7]}")
            return True
        if not c.force and vendor_dirty(c.root, dest):
            warn(f"{tool} marketplace {name}: {dest} has uncommitted edits and the manifest pin changed; left as it is "
                 f"(commit or discard the edits, or run sync --force)")
            return True
        stage_root = Path(tempfile.mkdtemp(prefix="agents-setup-"))
        stage = stage_root / name
        old = dest.with_name(f"{dest.name}.old.{os.getpid()}")
        try:
            try:
                self.stage_snapshot(tool, name, url, ref, stage)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_dir():
                    os.replace(dest, old)
                try:
                    os.replace(stage, dest)
                    if old.is_dir():
                        remove_dir_checked(old, dest.parent)
                    say(f"  fetched      {tool} marketplace {name} @ {ref[:7]}")
                    return True
                except OSError:
                    if old.is_dir():
                        os.replace(old, dest)
            except Fail:
                pass
        finally:
            rmtree(stage_root)
        try:
            self.verify_snapshot(tool, name, dest)
            warn(f"{tool} marketplace {name}: upstream unreachable or unusable, using the committed copy")
            return True
        except Fail:
            return False

    def warn_external(self, tool, name, as_note=False):
        """Say which wanted plugins of a saved marketplace the copy cannot provide offline."""
        d = self.mdir(tool, name)
        if not d.is_dir():
            return
        try:
            _, plugins = read_catalog(tool, d)
        except Fail:
            return
        out = (lambda m: say(f"  note     {m}")) if as_note else warn
        for pid in self.wanted_p[tool]:
            if pid.split("@", 1)[1] != name:
                continue
            pname = pid.split("@", 1)[0]
            found = [k for n, k in plugins if n == pname]
            for k in found:
                if k != "saved":
                    out(f"{tool} plugin {pid} comes from an external source ({k[len('external:'):]}); the saved copy of {name} "
                        f"does not include it, so installing it still needs the network")
            if not found:
                out(f"{tool} plugin {pid} is not listed in the saved copy of marketplace {name}")

    # -- listing --
    def listed(self, tool):
        """(marketplaces, plugins) as dicts, or None when the CLI cannot be read."""
        rm = cli(tool, "plugin", "marketplace", "list", "--json")
        rp = cli(tool, "plugin", "list", "--json")
        if rm.returncode != 0 or rp.returncode != 0:
            return None
        lm, lp = parse_marketplaces_json(rm.stdout, tool), parse_plugins_json(rp.stdout)
        if lm is None or lp is None:
            return None
        return lm, lp

    # -- apply --
    def apply(self, tool):
        c, M = self.c, self.c.managed
        names = [m["name"] for m in self.wanted_m[tool]]
        ids = self.wanted_p[tool]
        if not cli_path(tool):
            if names or ids:
                warn(f"{tool} CLI not found: {tool} plugins skipped (install it and re-run)")
            M.carry_over(tool)
            return
        got = self.listed(tool)
        if got is None:
            c.fail(f"{tool} plugins: cannot read what is installed ('{tool} plugin list' failed); nothing was changed for {tool}")
            M.carry_over(tool)
            return
        LM, LP = got
        skip_m, bad_m = set(), set()

        # 1. plugins this repo installed earlier and the manifest no longer lists
        for rec in M.records(f"{tool}-plugin"):
            v = rec[0]
            if v in ids:
                continue
            if v not in LP:
                say(f"  gone         {tool} plugin {v} (already uninstalled)")
                continue
            if c.dry:
                say(f"  would uninstall {tool} plugin {v}")
                c.n_rm += 1
                LP.pop(v, None)
                continue
            if plugin_uninstall(tool, v):
                say(f"  uninstalled  {tool} plugin {v}")
                c.n_rm += 1
                LP.pop(v, None)
            else:
                c.fail(f"uninstall {tool} plugin {v} (record kept, will retry next run)")
                M.keep(f"{tool}-plugin", v)

        # 2. marketplaces this repo added earlier and the manifest no longer lists
        for rec in M.records(f"{tool}-marketplace"):
            v, rest = rec[0], rec[1:]
            if v in names:
                continue
            if v not in LM:
                say(f"  gone         {tool} marketplace {v} (already removed)")
                continue
            # removing a marketplace also uninstalls its plugins: never do that to plugins installed by hand
            still = [i for i in LP if i.split("@", 1)[-1] == v]
            if still:
                warn(f"{tool} marketplace {v} is no longer in the manifest but is kept: plugins installed outside this repo still use it ({' '.join(still)})")
                M.keep(f"{tool}-marketplace", v, *rest)
                continue
            if c.dry:
                say(f"  would remove {tool} marketplace {v}")
                c.n_rm += 1
                continue
            if marketplace_remove(tool, v):
                say(f"  removed      {tool} marketplace {v}")
                c.n_rm += 1
                LM.pop(v, None)
            else:
                c.fail(f"remove {tool} marketplace {v} (record kept, will retry next run)")
                M.keep(f"{tool}-marketplace", v, *rest)

        # 3. marketplaces the manifest wants
        for m in self.wanted_m[tool]:
            n = m["name"]
            wident, wsrc, wref = m["ident"], m["source"], m["ref"] or "-"
            vendored = m["vendored"]
            wsparse = ",".join(m["sparse"]) or "-"
            reg_src = str(self.mdir(tool, n)) if vendored else wsrc
            if (tool, n) in self.bad_snapshot:
                bad_m.add(n)
                continue
            rec = M.rest(f"{tool}-marketplace", n)
            managed = rec is not None
            rec_ident = rec[0] if rec else ""
            rec_ref = rec[1] if rec and len(rec) > 1 else ""
            rec_ref = rec_ref if rec_ref != "" else ""
            if n in LM:
                same = LM[n] == wident
                # Codex does not show a pinned ref in its listing; compare with what we recorded
                if same and tool == "codex" and not vendored and managed and rec_ref != wref:
                    same = False
                if same and managed and vendored and rec_ref != wref and rec_ref != "-":
                    # same folder, new pin: refresh the plugins of ours that are installed from it
                    mine = [i for i in ids if i.split("@", 1)[1] == n and i in LP and M.was(f"{tool}-plugin", i)]
                    mine_txt = " ".join(mine) or "no plugins installed from it"
                    if c.dry:
                        say(f"  would refresh {tool} marketplace {n} (saved copy moved to {wref[:7]}): {mine_txt}")
                        c.n_new += 1
                        M.keep(f"{tool}-marketplace", n, wident, rec_ref)
                        continue
                    if refresh_marketplace(tool, n, mine):
                        say(f"  refreshed    {tool} marketplace {n} @ {wref[:7]}: {mine_txt}")
                        c.n_new += 1
                        M.keep(f"{tool}-marketplace", n, wident, wref)
                    else:
                        c.fail(f"{tool} marketplace {n}: could not refresh its plugins (will retry next run)")
                        M.keep(f"{tool}-marketplace", n, wident, rec_ref)
                    continue
                if same:
                    c.n_ok += 1
                    if managed:
                        M.keep(f"{tool}-marketplace", n, wident, wref)
                    else:
                        say(f"  present      {tool} marketplace {n} (installed outside this repo: left unmanaged)")
                    continue
                if not managed:
                    warn(f"{tool} marketplace '{n}' already exists with a different source and was not added by this repo: "
                         f"left alone (remove it yourself to switch); its plugins are skipped")
                    skip_m.add(n)
                    c.n_skip += 1
                    continue
                # ours, but the manifest changed (source, saved copy or pin)
                if tool == "codex":    # Codex refuses to re-register a name, so remove it first (this uninstalls its plugins)
                    still = [i for i in LP if i.split("@", 1)[-1] == n and i not in ids]
                    if still:
                        warn(f"codex marketplace '{n}' changed in the manifest but plugins installed outside this repo use it ({' '.join(still)}): left as is")
                        skip_m.add(n)
                        M.keep(f"{tool}-marketplace", n, rec_ident, rec_ref)
                        continue
                    if c.dry:
                        say(f"  would re-register codex marketplace {n} (source or pin changed)")
                        c.n_new += 1
                        continue
                    if not marketplace_remove("codex", n):
                        c.fail(f"codex marketplace {n}: could not remove the old registration")
                        M.keep(f"{tool}-marketplace", n, rec_ident, rec_ref)
                        skip_m.add(n)
                        continue
                    for i in [i for i in LP if i.split("@", 1)[-1] == n]:
                        LP.pop(i)
                else:
                    if c.dry:
                        say(f"  would re-point claude marketplace {n} (source changed)")
                        c.n_new += 1
                        continue
            elif c.dry:
                say(f"  would add    {tool} marketplace {n}")
                c.n_new += 1
                continue
            rc = marketplace_register(tool, n, reg_src, "-" if vendored else wref, wsparse)
            if rc == 0:
                say(f"  marketplace  {tool} {n}{' (saved copy)' if vendored else ''}")
                c.n_new += 1
                M.keep(f"{tool}-marketplace", n, wident, wref)
                LM[n] = wident
            elif rc == 2:
                c.fail(f"{tool} marketplace {n}: the catalog at {wsrc} calls itself something else; it was not kept")
                bad_m.add(n)
            else:
                c.fail(f"{tool} marketplace {n}")
                bad_m.add(n)

        # 4. plugins the manifest wants
        for pid in ids:
            mp = pid.split("@", 1)[1]
            if mp in skip_m:
                c.n_skip += 1
                continue
            if mp in bad_m:
                warn(f"{tool} plugin {pid} skipped: its marketplace {mp} is not available (reported above)")
                c.n_skip += 1
                continue
            if pid in LP:
                c.n_ok += 1
                if M.was(f"{tool}-plugin", pid):
                    M.keep(f"{tool}-plugin", pid)
                else:
                    say(f"  present      {tool} plugin {pid} (installed outside this repo: left unmanaged)")
                continue
            if c.dry:
                say(f"  would install {tool} plugin {pid}")
                c.n_new += 1
                continue
            if plugin_install(tool, pid):
                say(f"  plugin       {tool} {pid}")
                c.n_new += 1
                M.keep(f"{tool}-plugin", pid)
                LP[pid] = True
            else:
                c.fail(f"{tool} plugin {pid}")
        # saved copies: say which wanted plugins they cannot provide offline
        for m in self.wanted_m[tool]:
            if m["vendored"]:
                self.warn_external(tool, m["name"])

    # -- doctor --
    def doctor(self, tool):
        """Prints the health of one tool's plugins; returns True when something is wrong."""
        names = self.wanted_m[tool]
        ids = self.wanted_p[tool]
        bad = False
        say(f"{tool} plugins:")
        if not cli_path(tool):
            if names or ids:
                say(f"  PROBLEM  the {tool} CLI is not installed or not on PATH")
                return True
            say("  (none wanted)")
            return False
        got = self.listed(tool)
        if got is None:
            say(f"  PROBLEM  '{tool} plugin list' failed")
            return True
        LM, LP = got
        for m in names:
            n, wident = m["name"], m["ident"]
            mode = "vendored" if m["vendored"] else "remote"
            if n not in LM:
                say(f"  PROBLEM  marketplace {n} is not registered (run sync)")
                bad = True
            elif LM[n] != wident:
                say(f"  PROBLEM  marketplace {n} is registered from {LM[n]}, the manifest wants {wident}")
                bad = True
            else:
                say(f"  ok       marketplace {n} ({mode})")
            if m["vendored"]:
                if not self.snapshot_current(tool, n, full_url(m["source"]), m["ref"]):
                    say(f"  PROBLEM  the saved copy of {n} is missing or does not match the manifest (run sync)")
                    bad = True
                self.warn_external(tool, n, as_note=True)
        for pid in ids:
            if pid not in LP:
                say(f"  PROBLEM  plugin {pid} is not installed (run sync)")
                bad = True
            elif LP[pid] is False:
                say(f"  note     plugin {pid} is installed but disabled")
            else:
                say(f"  ok       plugin {pid}")
        unmanaged = len([k for k in LP if k not in ids])
        say(f"  info     {unmanaged} other plugin(s) installed outside the manifest (never touched)")
        return bad
