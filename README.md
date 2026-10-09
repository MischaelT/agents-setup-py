# agents-setup (Python, cross-platform)

Make this machine's AI-agent skills, plugins and global instructions match one file, `manifest.yaml`.
Works on Linux, macOS and Windows. Port of the bash version (`sync.sh`, `update.sh`); the manifest format is identical,
so a manifest from the bash version works here without changes.

## Install and run
```bash
python -m pip install -e .          # needs Python 3.9+ and git; installs PyYAML
agents-setup --dry-run              # show what would change
agents-setup                        # apply the manifest (same as: agents-setup sync)
```
Without installing: `python -m agents_setup ...` from this folder (needs `pip install pyyaml`).

| Command | What it does |
|---|---|
| `agents-setup [sync] [--dry-run] [--force]` | install what is new, leave the rest, remove what you deleted from the manifest (only things this tool made) |
| `agents-setup doctor` | read-only health check: links, plugins, saved marketplaces |
| `agents-setup project <folder> [--only NAME...] [--force] [--dry-run]` | copy the `global: false` skills and install such plugins into one project; `--list` shows everything |
| `agents-setup add <claude\|codex\|both> <repo> <path> [name]` | pin a skill at the repo's latest commit, add it to the manifest, sync |
| `agents-setup update [--check] [--force] [NAME...]` | move pins to the latest upstream commit (a skill group moves as one unit) |

`--root DIR` points at another folder holding `manifest.yaml`, `skills/`, `vendor/` and `config/` (default: this checkout).
`AGENTS_MANIFEST=/path/manifest.yaml` overrides the manifest file.

## Manifest
Same schema as the bash version; see the comments in `manifest.yaml`. Top-level keys: `skills` (grouped by repo, one pinned commit
per group), `config` (`mode: link` or `import`), `claude_plugins`, `codex_plugins`, `tools`. Comments survive `add` and `update`.
Own skills dropped into `skills/{shared,claude,codex}/<name>/SKILL.md` are linked without a manifest entry.

## What is recorded where
`managed.txt` in the state folder lists everything this tool created; only those things are ever changed or removed.
State folder: `$XDG_STATE_HOME/agents-setup`, else `~/.local/state/agents-setup` (Linux), `~/Library/Application Support/agents-setup` (macOS),
`%LOCALAPPDATA%\agents-setup` (Windows). Skills end up in `~/.claude/skills` and `~/.agents/skills` (Codex).

## Windows
Symlinks need Developer Mode (or an elevated shell). Without them `sync` falls back to copies and says so. A copy is made only if the
target does not exist, and an existing copy that sync made earlier is replaced only when the source is **newer** (newer modification
time, or same time but different content). A copy that you edited later than the source is left alone. Real files or folders sync did
not create are never overwritten. Set `AGENTS_FORCE_COPY=1` to use copies on any system.
`tool:` entries run with `bash -c` when bash is on PATH, otherwise with `cmd /c`.
The `claude` and `codex` commands are found with `shutil.which`, so `.cmd` shims work.

## Tests
```bash
python -m pip install -e ".[test]"
python -m pytest -q
```
Offline: upstream repos are local git repos, `claude` and `codex` are fakes (`tests/fakes`), HOME is a scratch folder.
CI runs the suite on Ubuntu, macOS and Windows (`.github/workflows/test.yml`).

## Moving from the bash version
Copy your `manifest.yaml`, `config/`, `skills/` and `vendor/` here (or use `--root`). Run `agents-setup --dry-run` first; with an
unchanged manifest it plans the same actions as `./sync.sh --dry-run`. Existing links and the generated `~/.claude/CLAUDE.md` are
recognised as yours only if they appear in `managed.txt`; the bash version keeps it in the same format and location on Linux.
