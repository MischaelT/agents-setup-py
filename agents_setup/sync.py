"""sync: make this machine match manifest.yaml (port of sync.sh). Safe to run any time.

new entries are installed, unchanged ones are left alone, entries deleted from the manifest are removed
(only things this tool created, recorded in managed.txt)."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import links, manifest, paths
from .common import (Fail, copy_tree, fetch_snapshot, full_url, head_sha, remove_dir_checked, rmtree, say, upstream_matches,
                     vendor_dirty, warn)
from .plugins import Plugins, settings_env
from .state import Managed

TARGETS = {"claude": ("claude",), "codex": ("codex",), "both": ("claude", "codex")}


def sha_of_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def shell_command(cmd):
    """Argument list that runs a manifest `tool` command: bash when there is one, else cmd.exe on Windows."""
    bash = shutil.which("bash")
    if bash and not (os.name == "nt" and "system32" in bash.lower()):   # skip the WSL stub in System32
        return [bash, "-c", cmd]
    if os.name == "nt":
        return ["cmd", "/c", cmd]
    return ["sh", "-c", cmd]


class Sync:
    def __init__(self, root, manifest_path=None, dry=False, force=False, nofetch=False):
        self.root = Path(root).resolve()
        self.manifest_path = Path(manifest_path or os.environ.get("AGENTS_MANIFEST") or self.root / "manifest.yaml")
        self.vendor = self.root / "vendor"
        self.dry, self.force, self.nofetch = dry, force, nofetch
        self.managed = Managed(paths.state_dir() / "managed.txt")
        self.n_new = self.n_ok = self.n_rm = self.n_skip = 0
        self.fails = []
        self.want_link = {}      # link path -> source path
        self.want_import = {}    # generated file path -> source path it imports
        self.want_tool = []      # tool names in manifest order
        self.tool_cmd = {}
        self.project_items = []  # (kind, tool, name) marked global: false
        self.penv, self.pign = {}, {}   # (kind, name) -> {VAR: template} / [patterns]
        self.own = set()
        self.keep = set()        # links to leave alone this run because their skill could not be fetched
        self.data = None
        self.plugins = None

    # ---------- helpers ----------
    def act(self, past, would):
        return f"{would if self.dry else past:<13}"

    def fail(self, msg):
        self.fails.append(msg)
        print(f"FAIL  {msg}", file=sys.stderr, flush=True)

    def load(self):
        if not self.manifest_path.is_file():
            raise Fail(f"missing {self.manifest_path}", 1)
        try:
            self.data = manifest.load(self.manifest_path)
        except manifest.ManifestError as e:
            raise Fail(f"manifest: {e}", 1)
        self.plugins = Plugins(self, self.data)

    def target_dirs(self, tgt):
        d = {"claude": paths.claude_dir(), "codex": paths.codex_dir()}
        return [d[t] for t in TARGETS[tgt]]

    def want_for(self, tgt, name, src):
        for d in self.target_dirs(tgt):
            self.want_link[d / name] = Path(src)

    def keep_for(self, tgt, name):
        for d in self.target_dirs(tgt):
            self.keep.add(d / name)

    # ---------- vendored skill snapshots ----------
    def snapshot_current(self, name, repo, path, ref):
        d = self.vendor / name
        return (d / "SKILL.md").is_file() and upstream_matches(d, repo, path, ref)

    def ensure_snapshot(self, name, repo, path, ref):
        if self.snapshot_current(name, repo, path, ref):
            return True
        if self.nofetch:
            return True
        if self.dry:
            say(f"  would fetch  {name} @ {ref[:7]}")
            return True
        dest = self.vendor / name
        if not self.force and vendor_dirty(self.root, dest):
            warn(f"{name}: vendor/{name} has uncommitted edits and the manifest pin changed; left as it is "
                 f"(commit or discard the edits, or run sync --force)")
            return True
        # fetch into a staging folder first: a failed or interrupted download must never leave a half-copied folder behind
        self.vendor.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".stage.", dir=self.vendor))
        old = self.vendor / f"{name}.old.{os.getpid()}"
        try:
            try:
                fetch_snapshot(name, repo, path, ref, stage / name)
                if dest.is_dir():
                    os.replace(dest, old)
                try:
                    os.replace(stage / name, dest)
                    if old.is_dir():
                        remove_dir_checked(old, self.vendor)
                    say(f"  fetched      {name} @ {ref[:7]}")
                    return True
                except OSError:
                    if old.is_dir():
                        os.replace(old, dest)
            except Fail:
                pass
        finally:
            rmtree(stage)
        if (dest / "SKILL.md").is_file():
            warn(f"{name}: upstream unreachable, using committed snapshot")
            return True
        return False

    # ---------- read manifest ----------
    def read_manifest(self):
        d = self.data
        for t in ("shared", "claude", "codex"):
            base = self.root / "skills" / t
            if not base.is_dir():
                continue
            for sd in sorted(base.iterdir()):
                if (sd / "SKILL.md").is_file():
                    self.want_for("both" if t == "shared" else t, sd.name, sd)
                    self.own.add(sd.name)
        for s in d["skills"]:
            name = s["name"]
            if name in self.own:
                self.fail(f"skill {name}: an own skill in skills/ has the same name; rename one of them")
                continue
            self.penv[("skill", name)] = s["env"]
            self.pign[("skill", name)] = s["ignore"]
            if self.ensure_snapshot(name, full_url(s["repo"]), s["path"], s["ref"]):
                if not s["global"]:
                    self.project_items.append(("skill", s["tool"], name))
                else:
                    self.want_for(s["tool"], name, self.vendor / name)
            else:
                self.keep_for(s["tool"], name)   # a network blip must not uninstall a skill that is already linked
                self.fail(f"skill {name}: cannot fetch {s['repo']} @ {s['ref'][:7]} and no committed snapshot")
        for c in d["config"]:
            csrc = self.root / c["source"]
            ctgt = paths.expand_home(c["target"])
            if not csrc.is_file():
                self.fail(f"config {c['source']}: file missing in this repo")
                continue
            if c["mode"] == "link":
                self.want_link[ctgt] = csrc
            else:
                self.want_import[ctgt] = csrc
        for m in d["marketplaces"]:
            if m["vendored"] and not self.plugins.ensure_snapshot(m["tool"], m["name"], full_url(m["source"]), m["ref"]):
                self.plugins.bad_snapshot.add((m["tool"], m["name"]))
                self.fail(f"{m['tool']} marketplace {m['name']}: cannot fetch {m['source']} @ {m['ref'][:7]} and there is no usable committed copy")
        for p in d["plugins"]:
            self.penv[("plugin", p["id"])] = p["env"]
            self.pign[("plugin", p["id"])] = p["ignore"]
            if not p["global"]:
                self.project_items.append(("plugin", p["tool"], p["id"]))
        for t in d["tools"]:
            self.want_tool.append(t["name"])
            self.tool_cmd[t["name"]] = " ".join(t["run"].split())

    # ---------- links ----------
    def apply_links(self):
        M = self.managed
        if not self.dry:
            paths.claude_dir().mkdir(parents=True, exist_ok=True)
            paths.codex_dir().mkdir(parents=True, exist_ok=True)
        for t in sorted(self.want_link, key=str):
            src = self.want_link[t]
            ts = paths.posix(t)
            if links.is_link(t):
                if links.same_target(t, src):
                    self.n_ok += 1
                    M.keep("link", ts)
                elif M.was("link", ts):
                    self.place(t, src, "relinked", "would relink")
                else:
                    warn(f"skip {t}: points elsewhere and is not managed by this repo")
                    self.n_skip += 1
            elif os.path.lexists(t):
                if M.was("copy", ts):     # a copy made earlier because symlinks were not allowed: refresh it only if the source is newer
                    if links.source_is_newer(src, t):
                        self.place(t, src, "updated", "would update", replace=True)
                    else:
                        self.n_ok += 1
                        M.keep("copy", ts)
                else:
                    warn(f"skip {t}: exists and is not managed by this repo")
                    self.n_skip += 1
            else:
                self.place(t, src, "linked", "would link")
        # remove links/copies this tool made earlier that are no longer wanted
        wanted = {paths.posix(t) for t in self.want_link}
        keep = {paths.posix(t) for t in self.keep}
        for kind in ("link", "copy"):
            for rec in M.records(kind):
                p = rec[0]
                if p in wanted:
                    continue
                if p in keep:
                    M.keep(kind, p)
                    warn(f"kept {p} (its skill could not be fetched this run)")
                    continue
                if kind == "link" and links.is_link(p):
                    if not self.dry:
                        os.unlink(p)
                    say(f"  {self.act('removed', 'would remove')} {p}")
                    self.n_rm += 1
                elif kind == "copy" and os.path.lexists(p) and not links.is_link(p):
                    if not self.dry:
                        rmtree(p)
                    say(f"  {self.act('removed', 'would remove')} {p}")
                    self.n_rm += 1

    def place(self, t, src, past, would, replace=False):
        """Create (or re-create) the link at t: a symlink, or a copy where symlinks are not allowed."""
        ts = paths.posix(t)
        if self.dry:
            say(f"  {self.act(past, would)} {t}")
            self.n_new += 1
            self.managed.keep("copy" if replace else "link", ts)
            return
        if replace:
            links.make_copy(t, src)
            self.managed.keep("copy", ts)
            say(f"  {self.act(past, would)} {t} (copy)")
        elif links.make_symlink(t, src):
            self.managed.keep("link", ts)
            say(f"  {self.act(past, would)} {t}")
        else:
            if os.path.lexists(t):
                rmtree(t)
            links.make_copy(t, src)
            self.managed.keep("copy", ts)
            say(f"  {self.act('copied', 'would copy')} {t} (symlinks not allowed here; the copy is refreshed when the source is newer)")
        self.n_new += 1

    # ---------- generated import files (~/.claude/CLAUDE.md) ----------
    @staticmethod
    def import_content(src):
        return ("# Generated by agents-setup (sync). Claude Code imports the shared instructions below.\n"
                "# Add Claude-only rules under the import line. Once you edit this file, sync leaves it alone.\n"
                f"@{paths.posix(src)}\n")

    def write_import(self, t, src):
        t = Path(t)
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(self.import_content(src), encoding="utf-8", newline="\n")
        self.managed.keep("import", paths.posix(t), sha_of_file(t))

    def apply_imports(self):
        M = self.managed
        for t in sorted(self.want_import, key=str):
            src = self.want_import[t]
            ts = paths.posix(t)
            want = self.import_content(src)
            rec = M.rest("import", ts)
            rec = rec[0] if rec else ""
            if links.is_link(t):
                warn(f"skip {t}: is a symlink, not generated by this repo")
                self.n_skip += 1
            elif t.exists():
                cur = t.read_text(encoding="utf-8")
                if cur.rstrip("\n") == want.rstrip("\n"):
                    self.n_ok += 1
                    M.keep("import", ts, sha_of_file(t))
                elif rec and sha_of_file(t) == rec:
                    if not self.dry:
                        self.write_import(t, src)
                    say(f"  {self.act('updated', 'would update')} {t} (import path changed)")
                    self.n_new += 1
                elif f"@{paths.posix(src)}" in cur.splitlines():   # you added your own rules but the import line is intact: hands off
                    self.n_ok += 1
                    if rec:
                        M.keep("import", ts, rec)
                else:
                    warn(f"skip {t}: exists and was not generated by this repo (or you edited it). To share the instructions add this line to it:  @{paths.posix(src)}")
                    self.n_skip += 1
                    if rec:
                        M.keep("import", ts, rec)
            else:
                if not self.dry:
                    self.write_import(t, src)
                say(f"  {self.act('created', 'would create')} {t} (imports {paths.posix(src)})")
                self.n_new += 1
        # generated files whose manifest entry was deleted: remove only if still exactly what we wrote
        for rec in M.records("import"):
            p, sha = rec[0], (rec[1] if len(rec) > 1 else "")
            if Path(p) in self.want_import or any(paths.posix(w) == p for w in self.want_import):
                continue
            if os.path.isfile(p) and sha_of_file(p) == sha:
                if not self.dry:
                    os.unlink(p)
                say(f"  {self.act('removed', 'would remove')} {p}")
                self.n_rm += 1
            elif os.path.lexists(p):
                warn(f"{p} was edited by you; left in place (entry removed from manifest)")

    # ---------- tools ----------
    def apply_tools(self):
        M = self.managed
        for t in self.want_tool:
            if M.was("tool", t):
                M.keep("tool", t)
                self.n_ok += 1
                continue
            if self.dry:
                say(f"  would run tool {t}: {self.tool_cmd[t]}")
                continue
            say(f"  running tool {t}: {self.tool_cmd[t]}")
            r = subprocess.run(shell_command(self.tool_cmd[t]))
            if r.returncode == 0:
                M.keep("tool", t)
                self.n_new += 1
            else:
                self.fail(f"tool {t}")
        for rec in M.records("tool"):
            if rec[0] not in self.want_tool:
                warn(f"tool {rec[0]} removed from manifest: uninstall it manually")

    # ---------- run ----------
    def do_sync(self):
        self.load()
        self.read_manifest()
        say("links:")
        self.apply_links()
        say("config:")
        self.apply_imports()
        say("plugins:")
        self.plugins.apply("claude")
        self.plugins.apply("codex")
        say("tools:")
        self.apply_tools()
        if self.project_items:
            say("project-only (global: false; saved here, not installed globally):")
            for kind, tool, name in self.project_items:
                say(f"  {kind} {tool} {name}")
            say("  -> install them into a project with:  agents-setup project <project-folder>")
        if not self.dry:
            self.managed.save()
        say("")
        say(f"{'[dry-run] ' if self.dry else ''}unchanged {self.n_ok}, installed/updated {self.n_new}, removed {self.n_rm}, "
            f"skipped {self.n_skip}, failed {len(self.fails)}")
        if self.fails:
            for f in self.fails:
                say(f"  failed: {f}")
            return 1
        if not self.dry and self.n_new + self.n_rm > 0:
            say("Restart Claude Code / Codex to pick up changes.")
        return 0

    # ---------- add ----------
    def add(self, target, repo, path, name=None):
        if target not in TARGETS:
            raise Fail("target must be claude|codex|both")
        if path == "." and not name:
            raise Fail("give a name when path is .")
        name = name or Path(path).name
        ref = head_sha(repo)
        if not ref:
            raise Fail(f"cannot reach {repo}", 1)
        try:
            note = manifest.add_skill(self.manifest_path, target, name, full_url(repo), path, ref)
        except manifest.ManifestError as e:
            raise Fail(f"manifest: {e}", 1)
        say(note)
        say(f"added {name} @ {ref[:7]}. Read vendor/{name}/SKILL.md after sync before trusting it.")

    # ---------- doctor: read-only health check ----------
    def doctor(self):
        self.load()
        bad = False
        say("links recorded by sync:")
        M = self.managed
        if M.has_file():
            for kind in ("link", "copy"):
                for rec in M.records(kind):
                    p = rec[0]
                    if kind == "link":
                        if links.is_link(p) and os.path.exists(p):
                            say(f"  ok       {p}")
                        elif links.is_link(p):
                            say(f"  DANGLING {p} -> {links.read_link(p)}")
                            bad = True
                        else:
                            say(f"  MISSING  {p} (run sync)")
                            bad = True
                    else:
                        if os.path.lexists(p):
                            say(f"  ok       {p} (copy)")
                        else:
                            say(f"  MISSING  {p} (run sync)")
                            bad = True
        else:
            say("  (nothing recorded yet: run sync)")
        say("dangling links in the skill folders:")
        for d in (paths.claude_dir(), paths.codex_dir()):
            if d.is_dir():
                for p in sorted(d.iterdir()):
                    if links.is_link(p) and not os.path.exists(p):
                        say(f"  DANGLING {p}")
                        bad = True
        for tool in ("claude", "codex"):
            if self.plugins.doctor(tool):
                bad = True
        say("")
        say("problems found" if bad else "healthy")
        return 1 if bad else 0
