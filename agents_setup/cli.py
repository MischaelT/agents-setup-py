"""Command line: agents-setup [sync] | doctor | project | add | update"""
import argparse
import sys
from pathlib import Path

from .common import Fail
from .sync import Sync

ROOT = Path(__file__).resolve().parent.parent


def build_parser():
    p = argparse.ArgumentParser(prog="agents-setup", description="Make this machine match manifest.yaml. Safe to run any time.")
    p.add_argument("--root", default=str(ROOT), help="folder holding manifest.yaml, skills/, vendor/ (default: this checkout)")
    sub = p.add_subparsers(dest="cmd")
    sp = sub.add_parser("sync", help="apply the manifest (the default command)")
    for q, dflt in ((p, False), (sp, argparse.SUPPRESS)):   # SUPPRESS: a subparser default must not overwrite the main parser's value
        q.add_argument("--dry-run", "-n", action="store_true", default=dflt, help="show what would change, without touching anything")
        q.add_argument("--force", action="store_true", default=dflt, help="also replace vendored folders that have uncommitted local edits")
    sub.add_parser("doctor", help="read-only health check (links, plugins, saved marketplaces)")
    pp = sub.add_parser("project", help="put the 'global: false' items (or --only NAME...) into one project")
    pp.add_argument("folder", nargs="?")
    pp.add_argument("--only", nargs="+", metavar="NAME")
    pp.add_argument("--force", action="store_true")
    pp.add_argument("--dry-run", "-n", action="store_true")
    pp.add_argument("--list", action="store_true")
    ap = sub.add_parser("add", help="add a skill to the manifest, then sync")
    ap.add_argument("target", choices=["claude", "codex", "both"])
    ap.add_argument("repo")
    ap.add_argument("path")
    ap.add_argument("name", nargs="?")
    up = sub.add_parser("update", help="move pins to the latest upstream commit")
    up.add_argument("names", nargs="*", metavar="NAME")
    up.add_argument("--check", "-n", action="store_true", help="only report what is outdated")
    up.add_argument("--force", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.cmd or "sync"
    s = Sync(args.root, dry=getattr(args, "dry_run", False), force=getattr(args, "force", False))
    try:
        if cmd == "sync":
            return s.do_sync()
        if cmd == "doctor":
            s.nofetch = True
            return s.doctor()
        if cmd == "project":
            from .project import run_project
            s.dry = args.dry_run
            return run_project(s, args.folder, args.only or [], args.force, args.list)
        if cmd == "add":
            s.add(args.target, args.repo, args.path, args.name)
            return Sync(args.root).do_sync()
        if cmd == "update":
            from .update import run_update
            return run_update(s, args.names, args.check)
    except Fail as e:
        print(f"ERROR {e}", file=sys.stderr)
        return e.code
    return 2


if __name__ == "__main__":
    sys.exit(main())
