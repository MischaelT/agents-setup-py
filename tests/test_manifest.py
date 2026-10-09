import textwrap

import pytest

from agents_setup import manifest as m

SHA = "0123456789abcdef0123456789abcdef01234567"
SHA2 = "fedcba9876543210fedcba9876543210fedcba98"


def ok(text):
    return m.parse(textwrap.dedent(text))


def bad(text, needle):
    with pytest.raises(m.ManifestError) as e:
        m.parse(textwrap.dedent(text))
    assert needle in str(e.value)


GROUP = f"""
skills:
  - repo: o/r
    ref: "{SHA}"
    tool: both
    skills:
      one: skills/one
"""


def test_shipped_manifest_is_valid(tmp_path):
    from conftest import PKG_ROOT
    d = m.load(PKG_ROOT / "manifest.yaml")
    assert {s["name"] for s in d["skills"]} == {"caveman", "testing-python-libraries"}
    assert [x["name"] for x in d["marketplaces"] if x["vendored"]] == ["astral-sh"]


def test_dump_lines_format():
    d = ok(GROUP + "tools:\n  - {name: t, run: 'a   b'}\n")
    assert m.dump_lines(d) == [f"skill both one o/r skills/one {SHA} g1 global", "tool t a b"]


@pytest.mark.parametrize("text,needle", [
    ("nope: 1\n", "unknown top-level"),
    ("apps: []\n", "no longer exist"),
    ("skills: 3\n", "must be a list"),
    (GROUP.replace(SHA, "main"), "40-character"),
    (GROUP.replace("both", "none"), "'tool' must be"),
    (GROUP.replace("skills/one", "../x"), "without '..'"),
    (GROUP + "  - repo: o/q\n    ref: \"%s\"\n    tool: claude\n    skills:\n      one: x\n" % SHA, "listed twice"),
    (GROUP.replace("one: skills/one", "marketplaces: x"), "reserved"),
    (GROUP.replace("tool: both", "tool: both\n    global: maybe"), "'global' must be"),
    (GROUP.replace("tool: both", "tool: both\n    name: x"), "grouped by repo"),
    ("config:\n  - {source: /abs, mode: link, target: ~/x}\n", "inside this repo"),
    ("config:\n  - {source: a, mode: copy, target: ~/x}\n", "link or import"),
    ("config:\n  - {source: a, mode: link, target: x}\n", "must start with"),
    ("config:\n  - {source: a, mode: link, target: ~/x}\n  - {source: b, mode: link, target: ~/x}\n", "used twice"),
    ("tools:\n  - {name: t}\n", "'run' command"),
    ("claude_plugins:\n  plugins: [a@b]\n", "not listed under"),
    ("claude_plugins:\n  marketplaces: [{name: m, repo: o/r, ref: x}]\n", "cannot pin a remote"),
    (f"claude_plugins:\n  marketplaces: [{{name: m, repo: ./x, ref: \"{SHA}\", vendored: true}}]\n", "local path"),
    ("codex_plugins:\n  marketplaces: [{name: m, source: o/r, sparse: [a b]}]\n", "'sparse' must be"),
])
def test_validation_errors(text, needle):
    bad(text, needle)


def test_project_extras_validated():
    bad(GROUP.replace("tool: both", "tool: both\n    project_env: {1bad: x}"), "project_env")
    bad(GROUP.replace("tool: both", "tool: both\n    project_ignore: ['/abs']"), "project_ignore")
    d = ok(GROUP.replace("tool: both", "tool: both\n    global: false\n    project_env: {A: '{project}/x'}\n    project_ignore: [x/]"))
    s = d["skills"][0]
    assert s["global"] is False and s["env"] == {"A": "{project}/x"} and s["ignore"] == ["x/"]


COMMENTED = f"""# top comment
skills:
  # ---------- Claude ----------
  - repo: https://github.com/o/r.git   # trailing comment
    ref: "{SHA}"
    tool: claude
    skills:
      one: skills/one

claude_plugins:
  marketplaces:
    - name: mk                       # mk comment
      repo: o/mk
      ref: "{SHA}"
      vendored: true
  plugins: []
"""


def test_add_skill_joins_group_and_keeps_comments(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(COMMENTED, encoding="utf-8")
    note = m.add_skill(p, "claude", "two", "https://github.com/o/r.git", "skills/two", SHA)
    assert "existing group" in note
    text = p.read_text(encoding="utf-8")
    assert "# top comment" in text and "# trailing comment" in text and "two: skills/two" in text
    assert [s["group"] for s in m.load(p)["skills"]] == [1, 1]


def test_add_skill_new_group_and_duplicate(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(COMMENTED, encoding="utf-8")
    assert "new group" in m.add_skill(p, "codex", "x", "https://github.com/o/other.git", ".", SHA2)
    assert 'x: "."' in p.read_text(encoding="utf-8")
    with pytest.raises(m.ManifestError, match="already exists"):
        m.add_skill(p, "codex", "x", "https://github.com/o/other.git", ".", SHA2)


def test_set_ref_skill_and_marketplace(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(COMMENTED, encoding="utf-8")
    m.set_ref(p, "one", SHA2)
    m.set_ref(p, "mk", SHA2, "claude")
    d = m.load(p)
    assert d["skills"][0]["ref"] == SHA2 and d["marketplaces"][0]["ref"] == SHA2
    text = p.read_text(encoding="utf-8")
    assert "# trailing comment" in text and "# mk comment" in text
    with pytest.raises(m.ManifestError, match="not found"):
        m.set_ref(p, "zzz", SHA2)


def test_edit_refuses_to_write_an_invalid_manifest(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(COMMENTED, encoding="utf-8")
    with pytest.raises(m.ManifestError):
        m.set_ref(p, "one", "not-a-sha")
    assert p.read_text(encoding="utf-8") == COMMENTED
