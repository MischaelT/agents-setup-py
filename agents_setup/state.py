"""managed.txt: what this tool created (same tab-separated records as the shell version).

  link<TAB>path                      symlink made by sync
  copy<TAB>path                      copy made instead of a link (Windows without symlink rights)
  import<TAB>path<TAB>sha256         generated file with an @-import
  tool<TAB>name
  <tool>-marketplace<TAB>name<TAB>ident<TAB>ref
  <tool>-plugin<TAB>id
"""
import os
from pathlib import Path


class Managed:
    def __init__(self, path):
        self.path = Path(path)
        self.old = []          # records from the last run
        if self.path.is_file():
            self.old = [l for l in self.path.read_text(encoding="utf-8").split("\n") if l]
        self.new = []          # records for the next run

    def has_file(self):
        return self.path.is_file()

    def was(self, *fields):
        return "\t".join(fields) in self.old

    def records(self, kind):
        """Fields (after the kind) of every old record of that kind."""
        return [l.split("\t")[1:] for l in self.old if l.split("\t")[0] == kind]

    def rest(self, kind, key):
        """Fields after the key of the old record <kind, key>, or None."""
        for f in self.records(kind):
            if f and f[0] == key:
                return f[1:]
        return None

    def keep(self, *fields):
        self.new.append("\t".join(fields))

    def carry_over(self, tool):
        """Keep earlier records of this tool when its CLI could not be used this run."""
        for l in self.old:
            k = l.split("\t")[0]
            if k in (f"{tool}-marketplace", f"{tool}-plugin"):
                self.new.append(l)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(sorted(set(self.new))) + ("\n" if self.new else "")
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, self.path)
