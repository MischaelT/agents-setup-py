"""update: pull newer upstream commits for vendored skill groups and saved plugin marketplaces (port of update.sh).

A skills group shares one pinned commit and is updated as ONE unit. A marketplace with `vendored: true` is a unit of its own.
If a skill of a group is missing or a marketplace catalog is unusable at the new commit, the unit stays on the old pin."""
import os
import tempfile
from pathlib import Path

from . import manifest
from .common import Fail, fetch_snapshot, full_url, head_sha, remove_dir_checked, rmtree, say, vendor_dirty, warn
from .sync import Sync

RUNNABLE = {".sh", ".bash", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1"}


def _files(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}


def review_executables(vendor, old, new):
    """List new or changed files that can run commands (scripts, executables)."""
    old_f, new_f = _files(old), _files(new)
    hits = []
    for rel, p in sorted(new_f.items()):
        if rel == ".upstream" or rel.endswith("/.upstream"):
            continue
        o = old_f.get(rel)
        if o is not None and o.read_bytes() == p.read_bytes():
            continue
        if p.suffix in RUNNABLE or "/scripts/" in "/" + rel or (os.name != "nt" and os.access(p, os.X_OK)):
            hits.append(Path(new).relative_to(vendor).as_posix() + "/" + rel)
    if hits:
        say("           review: new or changed files that can run commands:")
        for h in hits:
            say(f"             {h}")


class Unit:
    def __init__(self, key, kind, repo, ref, tool=None):
        self.key, self.kind, self.repo, self.ref, self.tool = key, kind, repo, ref, tool
        self.names, self.paths = [], []

    def label(self):
        if self.kind == "marketplace":
            return f"{self.tool} marketplace {self.names[0]}"
        if len(self.names) == 1:
            return self.names[0]
        r = self.repo.rsplit("/", 1)[-1]
        r = r[:-4] if r.endswith(".git") else r
        return f"{r} ({len(self.names)} skills)"


def units_of(data):
    units = {}
    for s in data["skills"]:
        key = f"g{s['group']}"
        u = units.setdefault(key, Unit(key, "group", s["repo"], s["ref"]))
        u.names.append(s["name"])
        u.paths.append(s["path"])
    for m in data["marketplaces"]:
        if m["vendored"]:
            key = f"m:{m['tool']}:{m['name']}"
            u = units[key] = Unit(key, "marketplace", m["source"], m["ref"], m["tool"])
            u.names.append(m["name"])
    return list(units.values())


def update_group(s, u, new):
    """Every skill of the group, or none. Raises Fail(message)."""
    url = full_url(u.repo)
    vendor = s.vendor
    stage_root = Path(tempfile.mkdtemp(prefix="agents-setup-"))
    placed = []
    pid = os.getpid()

    def restore_all():
        for n in placed:
            if (vendor / n).is_dir():
                remove_dir_checked(vendor / n, vendor)
            old = vendor / f"{n}.old.{pid}"
            if old.is_dir():
                os.replace(old, vendor / n)

    try:
        for n, p in zip(u.names, u.paths):    # 1. fetch and check every skill at the new commit into a staging folder
            try:
                fetch_snapshot(n, url, p, new, stage_root / n)
            except Fail as e:
                raise Fail(f"{n}: {e or 'fetch failed'}")
        vendor.mkdir(parents=True, exist_ok=True)   # 2. move the folders in; remember each old one so everything can be undone
        for n in u.names:
            old = vendor / f"{n}.old.{pid}"
            try:
                if (vendor / n).is_dir():
                    os.replace(vendor / n, old)
                placed.append(n)
                os.replace(stage_root / n, vendor / n)
            except OSError:
                restore_all()
                raise Fail(f"{n}: could not move the new version in place")
        try:                                  # 3. the pin last; undo the folders if it cannot be written
            manifest.set_ref(s.manifest_path, u.names[0], new)
        except manifest.ManifestError:
            restore_all()
            raise Fail("could not edit manifest.yaml")
        for n in u.names:
            old = vendor / f"{n}.old.{pid}"
            if old.is_dir():
                review_executables(vendor, old, vendor / n)
                remove_dir_checked(old, vendor)
    finally:
        rmtree(stage_root)


def update_marketplace(s, u, new):
    tool, name = u.tool, u.names[0]
    dest = s.plugins.mdir(tool, name)
    stage_root = Path(tempfile.mkdtemp(prefix="agents-setup-"))
    stage = stage_root / name
    old = dest.with_name(f"{dest.name}.old.{os.getpid()}")
    try:
        s.plugins.stage_snapshot(tool, name, full_url(u.repo), new, stage)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dest.is_dir():
                os.replace(dest, old)
            os.replace(stage, dest)
        except OSError:
            if old.is_dir() and not dest.is_dir():
                os.replace(old, dest)
            raise Fail("could not move the new version in place")
        try:
            manifest.set_ref(s.manifest_path, name, new, tool)
        except manifest.ManifestError:
            rmtree(dest)
            if old.is_dir():
                os.replace(old, dest)
            raise Fail("could not edit manifest.yaml")
        if old.is_dir():
            review_executables(s.vendor, old, dest)
            remove_dir_checked(old, dest.parent)
    finally:
        rmtree(stage_root)


def run_update(s, only, check):
    s.load()
    units = units_of(s.data)
    head_cache = {}
    updated, current, kept, available = [], [], [], []
    for u in units:
        if only and not any(n in only for n in u.names):
            continue
        label, ref, url = u.label(), u.ref, full_url(u.repo)
        if url not in head_cache:
            head_cache[url] = head_sha(url)
        new = head_cache[url]
        if not new:
            say(f"  kept     {label}: upstream unreachable")
            kept.append(label)
            continue
        if new == ref:
            say(f"  current  {label} @ {ref[:7]}")
            current.append(label)
            continue
        if check:
            say(f"  outdated {label}  {ref[:7]} -> {new[:7]}")
            available.append(label)
            continue
        if not s.force:   # a local patch must not be replaced silently
            if u.kind == "marketplace":
                dirty = [u.names[0]] if vendor_dirty(s.root, s.plugins.mdir(u.tool, u.names[0])) else []
            else:
                dirty = [n for n in u.names if vendor_dirty(s.root, s.vendor / n)]
            if dirty:
                say(f"  kept     {label}: local edits in {' '.join(dirty)} (commit or discard them, or use --force); still pinned to {ref[:7]}")
                kept.append(label)
                continue
        try:
            if u.kind == "marketplace":
                update_marketplace(s, u, new)
            else:
                update_group(s, u, new)
            say(f"  updated  {label}  {ref[:7]} -> {new[:7]}")
            updated.append(label)
            if u.kind == "group" and len(u.names) > 1:
                say(f"           skills: {' '.join(u.names)}")
        except Fail as e:
            together = ", the whole group stays together" if u.kind == "group" and len(u.names) > 1 else ""
            say(f"  kept     {label}: new version not usable ({e}); still pinned to {ref[:7]}{together}")
            kept.append(label)
    for n in only:
        if not any(n in u.names for u in units):
            warn(f"{n} is not a vendored skill or saved marketplace in the manifest")
    say("")
    if check:
        say(f"[check] {len(current)} current, {len(available)} outdated, {len(kept)} unreachable")
        if available:
            say("Run agents-setup update to apply.")
    else:
        say(f"{len(updated)} updated, {len(current)} current, {len(kept)} kept")
        if updated:
            say("Review before trusting:  git diff --stat -- vendor/ manifest.yaml")
            say("Then commit. To roll back: git checkout -- vendor/ manifest.yaml")
    return 0
