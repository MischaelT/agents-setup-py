"""Links with a fallback: a symlink where the system allows it, otherwise a copy that is refreshed only when the source is newer."""
import hashlib
import os
import shutil
from pathlib import Path

from .common import rmtree


def force_copy():
    """AGENTS_FORCE_COPY=1 makes links be copies (used by tests and by people who cannot create symlinks)."""
    return os.environ.get("AGENTS_FORCE_COPY") == "1"


def is_link(p):
    return os.path.islink(p)


def read_link(p):
    """Link target as text; Windows prepends a long-path marker we did not write."""
    return os.readlink(p).removeprefix("\\\\?\\")


def same_target(link, src):
    """A link points at src (compared as resolved text so '/a/b' and '/a/./b' agree)."""
    cur = read_link(link)
    return cur == str(src) or os.path.normcase(os.path.abspath(cur)) == os.path.normcase(os.path.abspath(str(src)))


def make_symlink(target, src):
    """Create/replace a symlink; returns False when the system refuses (Windows without Developer Mode)."""
    if force_copy():
        return False
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if os.path.islink(target):
            os.unlink(target)
        os.symlink(str(src), str(target), target_is_directory=Path(src).is_dir())
        return True
    except (OSError, NotImplementedError):
        if os.name == "nt" or force_copy():
            return False
        raise


def newest_mtime(p):
    """Newest modification time of a file, or of anything inside a folder."""
    p = Path(p)
    if not p.is_dir():
        return p.stat().st_mtime
    newest = p.stat().st_mtime
    for root, dirs, files in os.walk(p):
        for n in files + dirs:
            try:
                newest = max(newest, os.stat(os.path.join(root, n)).st_mtime)
            except OSError:
                pass
    return newest


def tree_hash(p):
    h = hashlib.sha256()
    p = Path(p)
    if not p.is_dir():
        h.update(p.read_bytes())
        return h.hexdigest()
    for f in sorted(x for x in p.rglob("*") if x.is_file()):
        h.update(str(f.relative_to(p).as_posix()).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def source_is_newer(src, copy):
    """Copy only if the content we want to put there is newer than what is there: newer mtime, or (same mtime) different content."""
    a, b = newest_mtime(src), newest_mtime(copy)
    if a > b:
        return True
    if a == b:
        return tree_hash(src) != tree_hash(copy)
    return False


def make_copy(target, src):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    rmtree(target)
    if Path(src).is_dir():
        shutil.copytree(src, target)
    else:
        shutil.copy2(src, target)
