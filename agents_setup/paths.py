"""Where things live on this machine."""
import os
import sys
from pathlib import Path


def home():
    return Path(os.path.expanduser("~"))


def expand_home(p):
    """'~/x' -> <home>/x ; absolute paths stay as they are."""
    if p.startswith("~/"):
        return home() / p[2:]
    return Path(p)


def state_dir():
    """Folder with managed.txt. Linux: $XDG_STATE_HOME or ~/.local/state; macOS: ~/Library/Application Support; Windows: %LOCALAPPDATA%."""
    if os.environ.get("XDG_STATE_HOME"):
        base = Path(os.environ["XDG_STATE_HOME"])
    elif sys.platform == "darwin":
        base = home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or home() / "AppData" / "Local")
    else:
        base = home() / ".local" / "state"
    return base / "agents-setup"


def claude_dir():
    return home() / ".claude" / "skills"


def codex_dir():
    return home() / ".agents" / "skills"   # Codex user-level skills directory


def posix(p):
    """Stable text form of a path for records and @imports (forward slashes on every system)."""
    return Path(p).as_posix()
