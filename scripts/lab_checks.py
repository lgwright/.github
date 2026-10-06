#!/usr/bin/env python3
"""File checks for LGW Lab repositories: large files and heavy notebooks.

    lab_checks.py [--base SHA] [--max-mb 10] [--notebook-mb 2]

With --base, the check reads only the files that changed between SHA and HEAD.
Without --base, it reads every tracked file. Each finding prints as a GitHub
annotation. A notebook with outputs is allowed (a run record) unless it is
larger than --notebook-mb. The exit code is 1 when a check fails.
"""
import argparse
import json
import os
import subprocess
import sys


def changed_files(base):
    if base:
        cmd = ["git", "diff", "--name-only", "--diff-filter=AM", base, "HEAD"]
    else:
        cmd = ["git", "ls-files"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f and os.path.isfile(f)]


def notebook_outputs(path):
    """Return the number of code cells that keep outputs."""
    try:
        with open(path, encoding="utf-8") as fh:
            nb = json.load(fh)
    except (ValueError, UnicodeDecodeError):
        return 0
    return sum(1 for c in nb.get("cells", []) if c.get("cell_type") == "code" and c.get("outputs"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--max-mb", type=float, default=10.0)
    ap.add_argument("--notebook-mb", type=float, default=2.0)
    a = ap.parse_args()
    limit = a.max_mb * 1024 * 1024
    files = changed_files(a.base)
    fails = 0
    for f in files:
        size = os.path.getsize(f)
        if size > limit:
            fails += 1
            print(f"::error file={f}::File is {size / 1048576:.1f} MB, above the {a.max_mb:g} MB limit. "
                  "Keep data and large outputs out of git (use a data folder that git ignores, or a release asset).")
        if f.endswith(".ipynb"):
            n = notebook_outputs(f)
            if n and size > a.notebook_mb * 1024 * 1024:
                fails += 1
                print(f"::error file={f}::Notebook is {size / 1048576:.1f} MB with {n} output cells, above the "
                      f"{a.notebook_mb:g} MB notebook limit. Save large figures as files and clear the heavy outputs.")
            elif n:
                print(f"::warning file={f}::{n} code cells keep their outputs ({size / 1048576:.1f} MB). "
                      "This is allowed for a run record. Clear them if they are not needed.")
    scope = f"changed since {a.base[:7]}" if a.base else "tracked"
    print(f"lab_checks: {len(files)} {scope} files, {fails} findings")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
