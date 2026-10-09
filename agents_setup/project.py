"""sync project: put skills and plugins into one project instead of installing them globally (port of cmd_project)."""
import os
import sys
from pathlib import Path

from .common import Fail, copy_tree, rmtree, run, say, warn
from .plugins import cli_path, settings_env
from .sync import Sync

DIRS = {"claude": [".claude/skills"], "codex": [".agents/skills"], "both": [".claude/skills", ".agents/skills"]}


def inventory(s):
    """{skill name: (tool, source folder)}: own skills (skills/shared|claude|codex) and vendored snapshots (vendor/<name>)."""
    inv = {}
    for t in ("shared", "claude", "codex"):
        base = s.root / "skills" / t
        if base.is_dir():
            for sd in sorted(base.iterdir()):
                if (sd / "SKILL.md").is_file():
                    inv[sd.name] = ("both" if t == "shared" else t, sd)
    for sk in s.data["skills"]:
        if (s.vendor / sk["name"] / "SKILL.md").is_file():
            inv[sk["name"]] = (sk["tool"], s.vendor / sk["name"])
    return inv


def run_project(s, folder, only, force, listing):
    s.load()
    if listing:
        s.nofetch = True
    s.read_manifest()
    inv = inventory(s)
    ptool = {p["id"]: p["tool"] for p in s.data["plugins"]}
    pname = {p["id"].split("@")[0]: p["id"] for p in s.data["plugins"]}
    is_proj = {name for _, _, name in s.project_items}
    if listing:   # skills that are not downloaded yet are still listed, from the manifest
        for sk in s.data["skills"]:
            inv.setdefault(sk["name"], (sk["tool"], "(not downloaded yet)"))
        say(f"{'NAME':<34} {'TOOL':<8} INSTALLED")
        for n in sorted(inv):
            say(f"{n:<34} {inv[n][0]:<8} {'project-only' if n in is_proj else 'global'}")
        for i in sorted(ptool):
            say(f"{i:<34} {ptool[i]:<8} {'project-only (plugin)' if i in is_proj else 'global (plugin)'}")
        return 0
    if not folder or not Path(folder).is_dir():
        raise Fail("give an existing project folder (see project --help)")
    d = Path(folder).resolve()

    if not only:
        todo = list(s.project_items)
        if not todo:
            say("nothing is marked global: false in the manifest (name things with --only)")
            return 0
    else:
        todo, bad = [], []
        for n in only:
            if n in inv:
                todo.append(("skill", inv[n][0], n))
            elif n in ptool:
                todo.append(("plugin", ptool[n], n))
            elif n in pname:
                todo.append(("plugin", ptool[pname[n]], pname[n]))
            else:
                bad.append(n)
        if bad:
            print(f"ERROR unknown name(s): {' '.join(bad)}\n       try: agents-setup project --list", file=sys.stderr)
            return 2

    rc = 0
    copied = skipped = replaced = 0
    for kind, tool, name in todo:
        if kind == "skill":
            for sub in DIRS[tool]:
                dest = d / sub / name
                if os.path.lexists(dest):
                    if not force:
                        say(f"  skip         {name} -> {sub} (already there; --force replaces it)")
                        skipped += 1
                        continue
                    if not s.dry:
                        rmtree(dest)
                    say(f"  {s.act('replaced', 'would replace')} {name} -> {sub}")
                    replaced += 1
                else:
                    say(f"  {s.act('copied', 'would copy')} {name} -> {sub}")
                    copied += 1
                if not s.dry:
                    dest.mkdir(parents=True, exist_ok=True)
                    copy_tree(inv[name][1], dest, dereference=True, exclude=(".upstream",))
        elif tool == "claude":
            if s.dry:
                say(f"  would install claude plugin {name} (--scope project)")
                continue
            r = run([cli_path("claude") or "claude", "plugin", "install", name, "--scope", "project"], cwd=d)
            if r.returncode == 0:
                say(f"  installed    claude plugin {name} (recorded in .claude/settings.json of the project)")
            else:
                s.fail(f"claude plugin {name}: project install failed (run it by hand inside the folder to see why)")
                rc = 1
        else:
            warn(f"codex plugin {name}: Codex has no project scope for plugins; skipped")
    rc = extras(s, d, todo, force) or rc
    say("")
    say(f"{'[dry-run] ' if s.dry else ''}skills: {copied} copied, {replaced} replaced, {skipped} skipped -> {d}")
    say("Project files belong to the project: sync does not record, update or remove them.")
    return rc


def extras(s, d, todo, force):
    """What the items just installed into a project need around them.
    project_env: variables written into <project>/.claude/settings.local.json ({project} = the project folder; Claude only).
    project_ignore: patterns appended to <project>/.gitignore."""
    rc = 0
    pairs, pats = [], []
    for kind, tool, name in todo:
        env = s.penv.get((kind, name)) or {}
        if env:
            if tool == "codex":
                warn(f"{name}: project_env is only written for Claude projects; set it yourself where you run Codex")
            else:
                pairs += [f"{k}={v.replace('{project}', str(d))}" for k, v in env.items()]
        pats += list(s.pign.get((kind, name)) or [])
    if pairs:
        target = d / ".claude" / "settings.local.json"
        if s.dry:
            for p in pairs:
                say(f"  would set    {p} in {target}")
        else:
            try:
                for st, k, v in settings_env(target, pairs, force):
                    if st == "set":
                        say(f"  set          {k}={v}  (.claude/settings.local.json)")
                    elif st == "same":
                        say(f"  already set  {k}  (.claude/settings.local.json)")
                    else:
                        warn(f"{k} is already {v} in the project's settings.local.json; kept (use --force to replace it)")
            except Fail:
                s.fail(f"could not update {target}")
                rc = 1
    gi = d / ".gitignore"
    for pat in pats:
        existing = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
        if pat in existing:
            continue
        if s.dry:
            say(f"  would add    {pat} to {gi}")
            continue
        text = gi.read_text(encoding="utf-8") if gi.is_file() else ""
        with open(gi, "a", encoding="utf-8", newline="\n") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            f.write(pat + "\n")
        say(f"  ignored      {pat}  (.gitignore)")
    return rc
