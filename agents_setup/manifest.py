"""manifest.yaml reader/validator/editor (port of lib/manifest.py; same schema, same rules).

parse(text)            validated manifest (flat skills, marketplaces, plugins, config, tools)
dump_lines(data)       the simple line format the old shell scripts read (kept for parity and debugging)
add_skill(path, ...)   add a skill: joins the group for that repo/tool/commit, or starts a new group
set_ref(path, ...)     move the pinned commit of a skill's group, or of a vendored plugin marketplace
Comments and layout are preserved by the two editing functions.
"""
import os
import re

import yaml


class ManifestError(Exception):
    pass


TOOLS = {"claude", "codex", "both"}
PLUGIN_TOOLS = ("claude", "codex")
SECTION = {"claude": "claude_plugins", "codex": "codex_plugins"}
KEYS = {"tools": {"name", "run"}}
CONFIG_KEYS = {"source", "mode", "target"}
GROUP_KEYS = {"repo", "ref", "tool", "skills", "global", "project_env", "project_ignore"}
ENVVAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MARKETPLACE_KEYS = {"claude": {"name", "repo", "ref", "vendored"}, "codex": {"name", "source", "ref", "sparse", "vendored"}}
TOP = set(KEYS) | {"skills", "config"} | set(SECTION.values())
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")   # no ".", "..", or leading dot/dash
SHA = re.compile(r"^[0-9a-f]{40}$")


def project_extras(entry, where):
    """The optional project_env / project_ignore of a skill group or a plugin entry, validated: ({VAR: template}, [pattern])."""
    env = entry.get("project_env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and ENVVAR.match(k) and isinstance(v, str) and v for k, v in env.items()):
        fail(f"{where}: 'project_env' must map variable names to text, like {{FLUENT_DATA_DIR: \"{{project}}/.fluent-data\"}}")
    ign = entry.get("project_ignore") or []
    if not isinstance(ign, list) or not all(isinstance(p, str) and p and not re.search(r"\s", p) and ".." not in p.split("/") and not p.startswith("/") for p in ign):
        fail(f"{where}: 'project_ignore' must be a list of .gitignore patterns without spaces, relative to the project")
    return env, ign


def fail(msg):
    raise ManifestError(msg)


def safe_relpath(v, where, key):
    """A path that stays inside its base folder: relative, no '..' segment ('.' alone is allowed where noted)."""
    if v.startswith("/") or v.startswith("~") or any(seg == ".." for seg in v.replace("\\", "/").split("/")):
        fail(f"{where}: '{key}' must be a relative path without '..', got {v!r}")


def text_field(item, key, where, what="text without spaces"):
    v = item.get(key)
    if not isinstance(v, str) or not v or re.search(r"\s", v):
        fail(f"{where}: '{key}' is required, {what}")
    return v


def parse_plugins(data, tool):
    """Validate one plugin section (claude_plugins / codex_plugins) and flatten it into data['marketplaces'] / data['plugins']."""
    key = SECTION[tool]
    sec = data.get(key)
    if sec is None:
        return [], []
    if not isinstance(sec, dict):
        fail(f"'{key}' must be a mapping with 'marketplaces' and 'plugins'")
    extra = set(sec) - {"marketplaces", "plugins"}
    if extra:
        fail(f"{key}: unknown key(s) {sorted(extra)}; allowed: marketplaces, plugins")
    marketplaces, plugins, seen = [], [], set()
    for i, m in enumerate(sec.get("marketplaces") or [], 1):
        where = f"{key} marketplace #{i}"
        if not isinstance(m, dict):
            fail(f"{where} must be a mapping")
        bad = set(m) - MARKETPLACE_KEYS[tool]
        if bad:
            fail(f"{where}: unknown key(s) {sorted(bad)}; allowed: {sorted(MARKETPLACE_KEYS[tool])}")
        name = m.get("name")
        if not isinstance(name, str) or not NAME.match(name):
            fail(f"{where}: 'name' must start with a letter or digit and use only letters, digits, . _ -")
        where = f"{key} marketplace '{name}'"
        if name in seen:
            fail(f"{where}: duplicate name")
        seen.add(name)
        source = text_field(m, "repo" if tool == "claude" else "source", where, "text without spaces (owner/repo or a git URL)")
        vendored = m.get("vendored", False)
        if not isinstance(vendored, bool):
            fail(f"{where}: 'vendored' must be true or false")
        ref = m.get("ref")
        if ref is not None and (not isinstance(ref, str) or not ref or re.search(r"\s", ref)):
            fail(f"{where}: 'ref' must be text without spaces (quote it)")
        if vendored:
            if ref is None or not SHA.match(ref):
                fail(f"{where}: a vendored marketplace needs 'ref': a full 40-character lowercase commit hash, in quotes")
            if source.startswith(("/", ".", "~")):
                fail(f"{where}: only a git repository can be vendored, not the local path {source!r}")
        elif ref is not None and tool == "claude":
            fail(f"{where}: Claude cannot pin a remote marketplace (its CLI has no ref option); use 'vendored: true' with a commit, or drop 'ref'")
        sparse = m.get("sparse") or []
        if sparse:
            if not isinstance(sparse, list) or not all(isinstance(p, str) and p and not re.search(r"[\s,]", p) for p in sparse):
                fail(f"{where}: 'sparse' must be a list of paths without spaces or commas")
            for p in sparse:
                safe_relpath(p, where, "sparse")
            if vendored:
                fail(f"{where}: 'sparse' cannot be combined with 'vendored' (the whole repository is saved)")
        marketplaces.append({"tool": tool, "name": name, "source": source, "ref": ref, "vendored": vendored, "sparse": sparse})
    known = {m["name"] for m in marketplaces}
    seen_p = set()
    for entry in sec.get("plugins") or []:
        glob, penv, pign = True, {}, []
        if isinstance(entry, dict):   # {id: name@marketplace, global: false, project_env: {...}, project_ignore: [...]}
            if set(entry) - {"id", "global", "project_env", "project_ignore"} or "id" not in entry:
                fail(f"{key}: a plugin written as a mapping needs 'id' and may have 'global', 'project_env', 'project_ignore' only, got {entry!r}")
            penv, pign = project_extras(entry, f"{key} plugin {entry['id']!r}")
            glob = entry.get("global", True)
            if not isinstance(glob, bool):
                fail(f"{key}: plugin {entry['id']!r}: 'global' must be true or false")
            entry = entry["id"]
        pid = entry
        if not isinstance(pid, str) or re.search(r"\s", pid) or pid.count("@") != 1 or not all(pid.split("@")):
            fail(f"{key}: plugin entries must look like name@marketplace, got {pid!r}")
        if pid.split("@")[1] not in known:
            fail(f"{key}: plugin {pid!r}: its marketplace is not listed under {key}.marketplaces")
        if pid in seen_p:
            fail(f"{key}: plugin {pid!r} is listed twice")
        seen_p.add(pid)
        plugins.append({"tool": tool, "id": pid, "global": glob, "env": penv, "ignore": pign})
    return marketplaces, plugins

def parse(text):
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        fail(f"not valid YAML: {e}")
    data = data or {}
    if not isinstance(data, dict):
        fail("top level must be a mapping (skills:, claude_plugins:, codex_plugins: ...)")
    unknown = set(data) - TOP
    if unknown:
        old = sorted(unknown & {"apps", "codex_env", "marketplaces", "plugins"})
        hint = (f" ({', '.join(old)} no longer exist: plugins now live under claude_plugins and codex_plugins)" if old else "")
        fail(f"unknown top-level key(s) {sorted(unknown)}{hint}; allowed: {sorted(TOP)}")
    for key in ("skills", "config", "tools"):
        if data.get(key) is None:
            data[key] = []
        if not isinstance(data[key], list):
            fail(f"'{key}' must be a list")
    seen_tools = set()
    for i, item in enumerate(data["tools"], 1):
        where = f"tools #{i}"
        if not isinstance(item, dict):
            fail(f"{where} must be a mapping")
        bad = set(item) - KEYS["tools"]
        if bad:
            fail(f"{where}: unknown key(s) {sorted(bad)}; allowed: {sorted(KEYS['tools'])}")
        name = item.get("name")
        if not isinstance(name, str) or not NAME.match(name):
            fail(f"{where}: 'name' must start with a letter or digit and use only letters, digits, . _ -")
        if name in seen_tools:
            fail(f"tools '{name}': duplicate name")
        seen_tools.add(name)
        if not (isinstance(item.get("run"), str) and item["run"].strip()):
            fail(f"tools '{name}': 'run' command is required")
    # skills are written as groups, one per repo and commit; everything downstream sees one flat entry per skill
    flat = []
    seen_skill = set()
    for gi, g in enumerate(data["skills"], 1):
        where = f"skills group #{gi}"
        if not isinstance(g, dict):
            fail(f"{where} must be a mapping with repo, ref, tool and skills")
        if "name" in g or "path" in g:
            fail(f"{where}: skills are grouped by repo now. Write one entry per repo "
                 f"(repo, ref, tool, skills: {{name: path-in-repo, ...}}) instead of one per skill")
        bad = set(g) - GROUP_KEYS
        if bad:
            fail(f"{where}: unknown key(s) {sorted(bad)}; allowed: {sorted(GROUP_KEYS)}")
        for k in ("repo", "ref"):
            v = g.get(k)
            if not isinstance(v, str) or not v or re.search(r"\s", v):
                fail(f"{where}: '{k}' is required, text without spaces (quote the ref)")
        where = f"skills group {g['repo']}"
        if not SHA.match(g["ref"]):
            fail(f"{where}: 'ref' must be a full 40-character lowercase commit hash, in quotes")
        if g.get("tool") not in TOOLS:
            fail(f"{where}: 'tool' must be one of {sorted(TOOLS)}")
        glob = g.get("global", True)
        if not isinstance(glob, bool):
            fail(f"{where}: 'global' must be true or false")
        penv, pign = project_extras(g, where)
        sk = g.get("skills")
        if not isinstance(sk, dict) or not sk:
            fail(f"{where}: 'skills' must be a non-empty mapping of  name: path-in-repo")
        for name, path in sk.items():
            if not isinstance(name, str) or not NAME.match(name):
                fail(f"{where}: skill name {name!r} must start with a letter or digit and use only letters, digits, . _ -")
            if name == "marketplaces":
                fail(f"{where}: the skill name 'marketplaces' is reserved (vendor/marketplaces holds the saved plugin marketplaces)")
            if not isinstance(path, str) or not path or re.search(r"\s", path):
                fail(f"{where}: skill '{name}' needs a path inside the repo, text without spaces ('.' = the repo root)")
            safe_relpath(path, f"{where} skill '{name}'", "path")
            if name in seen_skill:
                fail(f"{where}: skill '{name}' is listed twice")
            seen_skill.add(name)
            flat.append({"name": name, "tool": g["tool"], "repo": g["repo"], "path": path, "ref": g["ref"], "group": gi, "global": glob, "env": penv, "ignore": pign})
    data["skills"] = flat
    seen_targets = set()
    for i, item in enumerate(data["config"], 1):
        where = f"config #{i}"
        if not isinstance(item, dict):
            fail(f"{where} must be a mapping")
        bad = set(item) - CONFIG_KEYS
        if bad:
            fail(f"{where}: unknown key(s) {sorted(bad)}; allowed: {sorted(CONFIG_KEYS)}")
        for k in CONFIG_KEYS:
            text_field(item, k, where)
        if item["mode"] not in ("link", "import"):
            fail(f"{where}: 'mode' must be link or import")
        if item["source"].startswith("/") or ".." in item["source"].split("/"):
            fail(f"{where}: 'source' must be a path inside this repo, like config/claude/CLAUDE.md")
        if not (item["target"].startswith("~/") or item["target"].startswith("/")):
            fail(f"{where}: 'target' must start with ~/ or /")
        if item["target"] in seen_targets:
            fail(f"{where}: target {item['target']} is used twice")
        seen_targets.add(item["target"])
    data["marketplaces"], data["plugins"] = [], []
    for tool in PLUGIN_TOOLS:
        m, p = parse_plugins(data, tool)
        data["marketplaces"] += m
        data["plugins"] += p
    return data
def dump_lines(data):
    out = []

    def add(*parts):
        out.append(" ".join(str(x) for x in parts))

    for s in data["skills"]:
        add("skill", s["tool"], s["name"], s["repo"], s["path"], s["ref"], f"g{s['group']}", "global" if s["global"] else "project")
        for k, v in s["env"].items():
            add("project-env", "skill", s["name"], k, v)
        for pat in s["ignore"]:
            add("project-ignore", "skill", s["name"], pat)
    for m in data["marketplaces"]:
        add("marketplace", m["tool"], m["name"], m["source"], m["ref"] or "-",
            "vendored" if m["vendored"] else "remote", ",".join(m["sparse"]) or "-")
    for p in data["plugins"]:
        add("plugin", p["tool"], p["id"], "global" if p["global"] else "project")
        for k, v in p["env"].items():
            add("project-env", "plugin", p["id"], k, v)
        for pat in p["ignore"]:
            add("project-ignore", "plugin", p["id"], pat)
    for c in data["config"]:
        add("config", c["mode"], c["source"], c["target"])
    for t in data["tools"]:
        add("tool", t["name"], " ".join(t["run"].split()))
    return out


# ---- comment-preserving edits ----
def block(lines, key):
    start = next((i for i, l in enumerate(lines) if re.match(rf"^{key}\s*:", l)), None)
    if start is None:
        fail(f"no '{key}:' section to edit")
    last = start
    for j in range(start + 1, len(lines)):
        l = lines[j]
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        if re.match(r"^\S", l):
            break
        last = j
    return start, last


def sub_block(lines, start, last, key):
    """Range of the lines under an indented sub-key (e.g. `  marketplaces:`) inside the block start..last."""
    found = None
    for j in range(start + 1, last + 1):
        m = re.match(r"^(\s+)%s\s*:" % re.escape(key), lines[j])
        if m:
            found = (j, len(m.group(1)))
            break
    if found is None:
        fail(f"no '{key}:' list to edit")
    j0, ind = found
    end = j0
    for j in range(j0 + 1, last + 1):
        l = lines[j]
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        if len(l) - len(l.lstrip()) <= ind:
            break
        end = j
    return j0, end


def items(lines, start, last):
    idx = [j for j in range(start + 1, last + 1) if re.match(r"^\s*-\s", lines[j])]
    if not idx:
        fail("that list is empty or written in a style this editor cannot change; edit manifest.yaml by hand")
    indent = min(len(lines[j]) - len(lines[j].lstrip()) for j in idx)
    idx = [j for j in idx if len(lines[j]) - len(lines[j].lstrip()) == indent]
    return idx, indent


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise ManifestError(f"cannot read {path}: {e}")


def load(path):
    return parse(read(path))


def write_checked(path, lines):
    text = "\n".join(lines) + "\n"
    parse(text)  # refuse to save something that no longer validates
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def group_chunks(lines):
    """Line ranges of the groups in the skills list, in file order (group n = chunks[n-1]), plus the item indent."""
    start, last = block(lines, "skills")
    try:
        idx, indent = items(lines, start, last)
    except ManifestError:
        # no groups yet (`skills:` or `skills: []`): the first one goes right after the key
        if re.match(r"^skills:\s*(\[\s*\])?\s*(#.*)?$", lines[start]):
            lines[start] = "skills:"
            return [], 2, start
        raise
    return [range(j, idx[n + 1] if n + 1 < len(idx) else last + 1) for n, j in enumerate(idx)], indent, last


def yaml_path(p):
    return json_quote(p) if (p == "." or re.search(r"[:#\[\]{},&*!|>'\"%@`]", p)) else p


def json_quote(p):
    return '"' + p.replace("\\", "\\\\").replace('"', '\\"') + '"'


def add_skill(mpath, tool, name, repo, path, ref):
    text = read(mpath)
    data = parse(text)
    if any(s["name"] == name for s in data["skills"]):
        fail(f"skill '{name}' already exists")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    chunks, indent, last = group_chunks(lines)
    pad = " " * indent
    # an existing group for the same repo, tool and commit takes the new skill; otherwise it starts a group
    target = next((g for g in range(1, len(chunks) + 1)
                   if any(s["group"] == g and s["repo"] == repo and s["tool"] == tool and s["ref"] == ref and s["global"] for s in data["skills"])), None)
    entry = f"{name}: {yaml_path(path)}"
    if target is not None:
        chunk = chunks[target - 1]
        sk_line = next((j for j in chunk if re.match(r"^\s+skills:\s*(#.*)?$", lines[j])), None)
        if sk_line is None:
            fail("that group's skills are written in a style this editor cannot change; add the skill by hand")
        entries = [j for j in range(sk_line + 1, chunk.stop) if re.match(r"^\s+[A-Za-z0-9][A-Za-z0-9._-]*\s*:\s*\S", lines[j])]
        if not entries:
            fail("that group's skills are written in a style this editor cannot change; add the skill by hand")
        indent_m = re.match(r"^(\s+)", lines[entries[0]])
        sub = indent_m.group(1) if indent_m else " " * (indent + 4)
        lines.insert(entries[-1] + 1, f"{sub}{entry}")
        note = f"added to the existing group for {repo}"
    else:
        new = [f"{pad}- repo: {repo}", f'{pad}  ref: "{ref}"', f"{pad}  tool: {tool}", f"{pad}  skills:", f"{pad}    {entry}"]
        lines[last + 1:last + 1] = new
        note = "started a new group"
    write_checked(mpath, lines)
    return note


def replace_ref(lines, chunk, indent):
    """Replace an entry's own ref line (not a skill or plugin that happens to be called 'ref')."""
    dash = re.compile(r"^(\s{%d}-\s+ref:\s*)[\"']?[^\"'#\s]+[\"']?(\s*(#.*)?)$" % indent)
    key = re.compile(r"^(\s{%d}ref:\s*)[\"']?[^\"'#\s]+[\"']?(\s*(#.*)?)$" % (indent + 2))
    return next(((j, m) for j in chunk for m in [dash.match(lines[j]) or key.match(lines[j])] if m), None)


def set_ref(mpath, name, ref, tool=None):
    text = read(mpath)
    data = parse(text)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    group = next((x["group"] for x in data["skills"] if x["name"] == name), None)
    if group is not None:
        chunks, indent, _ = group_chunks(lines)
        hit = replace_ref(lines, chunks[group - 1], indent)
        if not hit:
            fail(f"the group containing '{name}' has no 'ref:' line this editor can change; edit it by hand")
        j, m = hit
        lines[j] = f'{m.group(1)}"{ref}"{m.group(2)}'
        write_checked(mpath, lines)
        return
    cands = [m for m in data["marketplaces"] if m["name"] == name and tool in (None, m["tool"])]
    if not cands:
        fail(f"'{name}' not found among skills or plugin marketplaces")
    if len(cands) > 1:
        fail(f"'{name}' is a marketplace in both claude_plugins and codex_plugins; add --tool claude or --tool codex")
    m = cands[0]
    start, last = block(lines, SECTION[m["tool"]])
    s0, s1 = sub_block(lines, start, last, "marketplaces")
    idx, indent = items(lines, s0, s1)
    for n, st in enumerate(idx):
        chunk = range(st, idx[n + 1] if n + 1 < len(idx) else s1 + 1)
        if not any(re.match(rf"^\s*(?:-\s+)?name:\s*[\"']?{re.escape(name)}[\"']?\s*(#.*)?$", lines[j]) for j in chunk):
            continue
        hit = replace_ref(lines, chunk, indent)
        if not hit:
            fail(f"marketplace '{name}' has no 'ref:' line to change (write the marketplace entries in block style, with a ref)")
        j, mm = hit
        lines[j] = f'{mm.group(1)}"{ref}"{mm.group(2)}'
        write_checked(mpath, lines)
        return
    fail(f"marketplace '{name}': entry not found in the file text")
