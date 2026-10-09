"""Shared helpers (port of lib/common.sh): output, git, snapshots, safe removal."""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class Fail(Exception):
    """A fatal error with an exit code (the old `die`)."""

    def __init__(self, msg, code=2):
        super().__init__(msg)
        self.code = code


def say(msg=""):
    print(msg, flush=True)


def warn(msg):
    print(f"WARN  {msg}", file=sys.stderr, flush=True)


def git_env():
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"   # never prompt for credentials; unreachable repos just fail
    if os.name != "nt":
        env["GIT_ASKPASS"] = "true"
    return env


def run(args, cwd=None, capture=True, check=False, env=None, input=None):
    """subprocess.run with a list of arguments (never a shell); returns CompletedProcess, rc 127 when the program is missing."""
    try:
        return subprocess.run(args, cwd=cwd, capture_output=capture, text=True, check=check,
                              env=env, input=input, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: not found")


def git(*args, cwd=None):
    return run(["git", *args], cwd=cwd, env=git_env())


def rmtree(path):
    """rm -rf that also works for read-only files (git objects on Windows)."""
    path = str(path)
    if os.path.islink(path) or os.path.isfile(path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return

    def onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if os.path.isdir(path):
        shutil.rmtree(path, onerror=onerror)


def full_url(repo):
    if "://" in repo or repo.startswith("git@"):
        return repo
    return f"https://github.com/{repo}.git"


def head_sha(repo):
    """Exact HEAD of a remote ('' when unreachable); the ls-remote pattern also lists refs/remotes/origin/HEAD, hence the exact match."""
    r = git("ls-remote", full_url(repo), "HEAD")
    if r.returncode != 0:
        return ""
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "HEAD":
            return parts[0]
    return ""


def remove_dir_checked(d, parent):
    """rm -rf only a real child folder of the expected parent."""
    d, parent = Path(d), Path(parent)
    if not NAME_RE.match(d.name):
        raise Fail(f"refusing to remove '{d}'")
    if d.parent != parent:
        raise Fail(f"refusing to remove '{d}': not directly inside {parent}")
    rmtree(d)


def vendor_dirty(root, d):
    """A committed vendor folder that has uncommitted edits (a local patch a replacement would silently destroy).
    A folder git does not know yet (just downloaded, never committed) has nothing to compare with and is not reported."""
    d = Path(d)
    if not d.is_dir():
        return False
    if git("rev-parse", "--git-dir", cwd=root).returncode != 0:
        return False
    if git("ls-files", "--error-unmatch", "--", str(d), cwd=root).returncode != 0:
        return False
    r = git("status", "--porcelain", "--", str(d), cwd=root)
    return bool(r.stdout.strip())


def copy_tree(src, dest, dereference=False, exclude=()):
    """Copy a folder (tar -c | tar -x replacement). exclude: glob patterns of names to skip."""
    ignore = shutil.ignore_patterns(*exclude) if exclude else None
    shutil.copytree(src, dest, symlinks=not dereference, ignore=ignore, dirs_exist_ok=True)


UPSTREAM_EXCLUDE = (".git", ".github", ".gitignore", "__pycache__", "*.pyc")


def fetch_snapshot(name, repo, path, ref, dest, mode="skill"):
    """Copy the skill folder (mode skill) or the whole repository (mode repo, path '.') at commit <ref> into <dest>, replacing it,
    plus the license and an .upstream marker. Upstream .gitignore files are NOT copied: a nested ignore file could hide
    files the skill needs from git. Raises Fail(message) on any problem; <dest> never stays half-copied."""
    if not NAME_RE.match(name):
        raise Fail(f"bad name '{name}'", 1)
    tmp = tempfile.mkdtemp(prefix="agents-setup-")
    try:
        if git("init", "-q", tmp).returncode != 0 or git("remote", "add", "origin", repo, cwd=tmp).returncode != 0:
            raise Fail("git init failed", 1)
        if git("fetch", "-q", "--depth", "1", "origin", ref, cwd=tmp).returncode != 0:
            if git("fetch", "-q", "origin", cwd=tmp).returncode != 0:
                raise Fail(f"cannot fetch {repo}", 1)
        if git("checkout", "-q", ref, cwd=tmp).returncode != 0 and git("checkout", "-q", "FETCH_HEAD", cwd=tmp).returncode != 0:
            raise Fail(f"cannot check out {ref[:7]}", 1)
        src = Path(tmp) / path
        real_tmp = os.path.realpath(tmp)
        real_src = os.path.realpath(src)
        # the folder must really live inside the clone (no symlink or path trick pointing outside)
        if not src.is_dir() or not (real_src == real_tmp or real_src.startswith(real_tmp + os.sep)):
            raise Fail(f"path '{path}' is not inside the repository", 1)
        if mode == "skill":
            if not (src / "SKILL.md").is_file():
                raise Fail(f"no SKILL.md at '{path}' in {repo} @ {ref[:7]}", 1)
        elif path != ".":
            raise Fail("a whole-repository snapshot is taken from the repository root", 1)
        rmtree(dest)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        try:
            copy_tree(src, dest, exclude=UPSTREAM_EXCLUDE)   # a half-copied folder must never get an .upstream marker
        except (OSError, shutil.Error) as e:
            rmtree(dest)
            raise Fail(f"copying {name} failed part-way ({e})", 1)
        if any(p.name == ".gitignore" and ".git" not in p.parts for p in src.rglob(".gitignore")):
            print(f"note: {name} has upstream .gitignore file(s), not copied. If they exclude per-user files, "
                  f"mirror those patterns in this repo's .gitignore, scoped to its vendor folder", file=sys.stderr)
        if path != "." or mode != "skill":
            if not list(Path(dest).glob("LICENSE*")):
                for lic in sorted(Path(tmp).glob("LICENSE*")):
                    if lic.is_file():
                        shutil.copyfile(lic, Path(dest) / "LICENSE.upstream")
        # no date: re-fetching must not create diff noise
        (Path(dest) / ".upstream").write_text(f"repo={repo}\npath={path}\nref={ref}\n", encoding="utf-8", newline="\n")
    finally:
        rmtree(tmp)


def upstream_matches(folder, repo, path, ref):
    f = Path(folder) / ".upstream"
    if not f.is_file():
        return False
    lines = f.read_text(encoding="utf-8").splitlines()
    return all(x in lines for x in (f"repo={repo}", f"path={path}", f"ref={ref}"))
