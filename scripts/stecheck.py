#!/usr/bin/env python3
"""Writing check: score new or changed prose against ASD-STE100 Simplified
Technical English (STE).  The check is advisory.

  stecheck.py --staged [--brief N]   the sentences the staged commit adds or
                        changes (the default when no paths are given)
  stecheck.py --commit REV           the sentences that commit REV added or
                        changed, and the body of its message
  stecheck.py --msg FILE             a commit message body (for a commit-msg hook)
  stecheck.py PATH ...               every sentence in these files
                        (--newest-entry: only the newest dated entry of a LOG.md)
  stecheck.py --all [--repo R]       every sentence in the tracked files of repo R

What it reads.  Markdown prose (headings, paragraphs, list items, table
cells), Python comments and docstrings, commit message bodies, and
operator-screen strings.  An operator-screen string is a string literal on a
Python line that carries a "# STE" comment, or a "meaning" value in a
names.json file.  The check skips fenced code, inline code, links, URLs,
paths, identifiers, command flags, lines that hold mostly numbers, and every
path under _archive/.  Only sentences that touch an added line count in
--staged and --commit modes.

The checks on one sentence.  A sentence fails on any of these:
  length     more than 25 words, or more than 20 words in a command
             (a sentence that starts with a verb such as "Run" or "Never")
  passive    a form of "be" before a past participle ("is checked")
  jargon     a word in stecheck_words.json with no single meaning ("via")
  abbrev     a capital token of 2-6 letters that the same file does not
             define, as "local oscillator (LO)" or "LO (local oscillator)",
             and that is not in the allow list or in names.json
  name       an alias from names.json where the table gives one display name
  semicolon  any semicolon
A colon that is not after a label or before a list gives a warning only.

Technical names come from the repo's own code/experiment/bench/console/
names.json, a list of {id, display, code, aliases, meaning}.  Today only
quantum-enhanced-analog-computing has one.  A repo without the file gets no
name check, so the bench names of one project do not flag plain words in
another.  --names PATH or STECHECK_NAMES sets one file for every repo.

Output.  Per file, the pass rate and each failing sentence with its reasons
and a cheap fix.  Then the overall pass rate.  --brief N prints only the
summary line and the first N failures (the pre-commit hook uses --brief 5).
The exit code is 0, also on an internal error in --staged or --msg mode.
--strict makes it 1 when the pass rate is below --min (default 0.8).

The hooks.  shared/githooks/pre-commit runs --staged --brief 5 after
codecheck.py.  shared/githooks/commit-msg runs --msg FILE --brief 5.  Both
always exit 0.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORDS_FILE = HERE / "stecheck_words.json"
NAMES_REL = "code/experiment/bench/console/names.json"
DESCRIPTIVE_MAX = 25
PROCEDURAL_MAX = 20
MIN_PASS = 0.8
PLACE = "§"     # stands in for masked code, paths, links and identifiers
SHOW_PLACE = "`..`"       # how a masked token prints
PROSE = "\0prose"         # the cache key of the masked prose of the whole file

# ---------------------------------------------------------------- word lists

BE = {"is", "are", "was", "were", "be", "been", "being", "isn't", "aren't", "wasn't", "weren't"}
SKIP_ADVERBS = {"not", "also", "now", "then", "still", "already", "only", "always", "never", "often",
                "usually", "all", "both", "each", "first", "again", "just", "then", "no", "longer",
                "therefore", "thus", "later", "once", "since"}
IRREGULAR_PP = {
    "done", "made", "set", "run", "written", "known", "given", "taken", "shown", "seen", "built",
    "kept", "held", "left", "found", "sent", "read", "lost", "put", "cut", "driven", "chosen",
    "broken", "hidden", "begun", "drawn", "grown", "thrown", "fed", "led", "met", "paid", "said",
    "sold", "told", "understood", "bound", "caught", "brought", "bought", "taught", "thought",
    "meant", "spent", "split", "shut", "hit", "struck", "worn", "torn", "forgotten", "gotten",
    "laid", "lit", "overwritten", "rewritten", "rebuilt", "undone", "withdrawn", "mistaken",
    "shaken", "stolen", "ridden", "risen", "proven", "spun", "won", "dealt", "fit", "upset",
    "reset", "rerun", "redone", "remade", "beaten", "bitten", "eaten", "fallen", "forbidden",
    "given", "sped", "swept", "wound", "hung", "dug", "sought", "wrung"}
ED_NOT_PP = {"need", "red", "bed", "speed", "seed", "feed", "embed", "shed", "bleed", "breed",
             "exceed", "proceed", "succeed", "indeed", "hundred", "naked", "wicked", "sacred",
             "kindred", "aged", "biased", "unbiased", "advanced", "detailed", "dedicated",
             "complicated", "sophisticated", "interested", "tired", "concerned", "supposed",
             "ashamed", "pleased", "beloved", "learned", "rugged", "jagged", "ragged"}
IMPERATIVE = {
    "add", "apply", "ask", "avoid", "block", "build", "call", "change", "check", "choose", "clean",
    "click", "close", "commit", "compare", "confirm", "connect", "copy", "count", "create", "cut",
    "define", "delete", "diff", "disable", "disconnect", "do", "don't", "drop", "edit", "enable",
    "enter", "fetch", "find", "fix", "follow", "give", "go", "install", "keep", "kill", "launch",
    "lead", "leave", "let", "list", "load", "look", "make", "mark", "measure", "merge", "move",
    "name", "never", "always", "note", "open", "pass", "pick", "plant", "plot", "point", "prefer",
    "present", "press", "print", "propose", "pull", "push", "put", "quote", "read", "rebuild",
    "record", "reduce", "register", "remove", "rename", "replace", "report", "rerun", "reset",
    "resolve", "restart", "resume", "return", "route", "run", "save", "say", "score", "see",
    "select", "send", "set", "show", "skip", "sort", "split", "start", "stop", "submit", "supply",
    "switch", "take", "tell", "treat", "try", "turn", "type", "unblock", "update", "use",
    "verify", "wait", "watch", "write", "please", "ensure", "make", "raise", "lower", "align"}
NO_SPLIT = {"e.g", "i.e", "vs", "cf", "fig", "figs", "eq", "eqs", "al", "approx", "no", "nos",
            "dr", "mr", "resp", "incl", "ch", "ca", "ref", "refs", "sec", "et"}
CONJ = {"and", "but", "so", "because", "which", "while", "where", "when", "then", "or", "with"}
CODE_EXT = ("py|md|json|sh|cmd|ps1|png|pdf|csv|log|txt|ipynb|tex|yaml|yml|toml|npz|npy|h5|ini|cfg|"
            "js|ts|html|css|bib|svg|jpg|pt|pkl|xlsx|docx|pptx|zip|tar|gz|bit|hwh|tcl|xdc|v|sv|c|h|cpp")
FILE_RE = re.compile(rf"^[\w.-]+\.(?:{CODE_EXT})$", re.I)
DOTTED_ABBR_RE = re.compile(r"^(?:[A-Za-z]\.){2,}$")
CAMEL_RE = re.compile(r"^[a-z]+[A-Z]\w*$")
ABBR_RE = re.compile(r"^(?=(?:[0-9]*[A-Z]){2})[A-Z0-9]{2,8}s?$")
FENCE_RE = re.compile(r"^(```|~~~)")
BULLET_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
DATE_LABEL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s*:?\s*")
END_RE = re.compile(r"[.!?]+[\"')\]*]*(?=\s|$)")
LEAD_PUNCT = "([{\"'*_“‘"
TRAIL_PUNCT = ".,;:!?)]}\"'*_”’"


def load_words(path=WORDS_FILE):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    jargon = []
    for word, spec in data.get("jargon", {}).items():
        forms = [word] + list(spec.get("forms", []))
        alts = "|".join(re.escape(w) for w in sorted(set(forms), key=len, reverse=True))
        flags = 0 if spec.get("case_sensitive") else re.I
        jargon.append((word, re.compile(rf"(?<![\w-])(?:{alts})(?![\w-])", flags), spec.get("use", "")))
    return {"jargon": jargon, "abbreviations": set(data.get("abbreviations", [])),
            "stative": set(data.get("stative", []))}


# ---------------------------------------------------------------- names.json

@dataclass
class Names:
    path: str = ""
    aliases: list = field(default_factory=list)    # [(alias, compiled regex, display)]
    displays: list = field(default_factory=list)   # compiled regexes of the display names
    tokens: set = field(default_factory=set)       # every display, code and alias string

    def __bool__(self):
        return bool(self.path)


def _as_list(v):
    if v is None:
        return []
    return [v] if isinstance(v, str) else [x for x in v if isinstance(x, str)]


def _name_re(s):
    flags = 0 if any(c.isupper() for c in s) else re.I
    return re.compile(rf"(?<![\w-]){re.escape(s)}(?![\w-])", flags)


def load_names(path):
    """Names from a names.json: a list of {id, display, code, aliases, meaning},
    or {"names": [...]}, or {id: {...}}.  Returns an empty Names on any error."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return Names()
    if isinstance(data, dict):
        data = data.get("names", data.get("glossary", list(data.values())))
    entries = [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
    out = Names(path=str(path))
    displays = {}
    for e in entries:
        d = e.get("display") or e.get("name")
        if isinstance(d, str) and d.strip():
            displays[d.lower()] = d
    for d in sorted(displays.values(), key=len, reverse=True):
        out.displays.append(_name_re(d))
        out.tokens.add(d)
    for e in entries:
        d = e.get("display") or e.get("name")
        if not isinstance(d, str):
            continue
        codes = [c for c in _as_list(e.get("code")) if len(c) >= 3]
        for a in _as_list(e.get("aliases")) + codes:
            out.tokens.add(a)
            if len(a) < 3 or a.lower() in displays:
                continue
            out.aliases.append((a, _name_re(a), d))
    return out


def repo_top(path):
    """The nearest folder above path that holds a .git file or folder, or ""."""
    p = Path(path).resolve().parent
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return str(d)
    return ""


def find_names(explicit, tops):
    """The first names.json found: --names, STECHECK_NAMES, then the repo's own
    file at NAMES_REL under each repo top.  A repo without its own file gets no
    name check: the names of one project are not the names of another."""
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("STECHECK_NAMES"):
        cands.append(Path(os.environ["STECHECK_NAMES"]))
    for top in tops:
        if top:
            cands.append(Path(top) / NAMES_REL)
    for c in cands:
        if c.is_file():
            return load_names(c)
    return Names()


# ---------------------------------------------------------------- data classes

@dataclass
class Unit:
    """One block of prose: a paragraph, list item, heading, comment or string."""
    line: int
    lines: list
    kind: str


@dataclass
class Sentence:
    path: str
    line: int
    end: int
    text: str
    kind: str
    fails: list = field(default_factory=list)    # [(rule, detail, fix)]
    warns: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.fails


# ---------------------------------------------------------------- extraction

def md_units(text, kind="md"):
    """Prose blocks of a Markdown text, fenced code left out."""
    units, cur, fence = [], None, None
    lines = text.split("\n")

    def flush():
        nonlocal cur
        if cur is not None:
            units.append(Unit(cur[0], cur[1], kind))
        cur = None

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip("\r")
        s = line.strip()
        m = FENCE_RE.match(s)
        if m:
            flush()
            if fence is None:
                fence = m.group(1)
            elif s.startswith(fence):
                fence = None
            continue
        if fence is not None:
            continue
        if not s:
            flush()
            continue
        if s.startswith("|"):
            flush()
            if re.fullmatch(r"[|:\-\s]+", s):
                continue
            nxt = lines[i].strip() if i < len(lines) else ""
            if nxt.startswith("|") and re.fullmatch(r"[|:\-\s]+", nxt):
                continue                               # the header row holds labels only
            for cell in s.strip("|").split("|"):
                if cell.strip():
                    units.append(Unit(i, [cell.strip()], kind))
            continue
        if s.startswith("#"):
            flush()
            units.append(Unit(i, [DATE_LABEL_RE.sub("", s.lstrip("#").strip())], kind))
            continue
        if cur is None and (line.startswith("    ") or line.startswith("\t")) and not BULLET_RE.match(line):
            continue                                   # an indented code block
        if s.startswith(">"):
            s = s.lstrip(">").strip()
        b = BULLET_RE.match(line)
        if b:
            flush()
            cur = [i, [b.group(2)]]
            continue
        if cur is None:
            cur = [i, [s]]
        else:
            cur[1].append(s)
    flush()
    return units


NOQA_RE = re.compile(r"\bnoqa\b(?::\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*)?")
DIRECTIVE_RE = re.compile(r"^(?:type:|pragma\b|fmt:|pylint:|mypy:|isort:|%%|-\*-|ruff:|pyright:)")
CODE_START_RE = re.compile(r"^(?:import|from|def|class|return|if|elif|else|for|while|with|try|except|"
                           r"print|raise|assert|yield|lambda|async|await)\b")


def looks_like_code(s):
    """A commented-out line of code, not a prose comment."""
    if s.endswith("."):
        return False
    if CODE_START_RE.match(s) and re.search(r"[():=\[]", s):
        return True
    if not re.search(r"[=()\[\]{}]", s):
        return False
    try:
        compile(s, "<comment>", "exec")
        return True
    except (SyntaxError, ValueError):
        return False


def doc_units(start, value):
    """Prose blocks of one docstring.  The check skips indented lines (usage, tables, code)."""
    lines = value.split("\n")
    rest = [ln for ln in lines[1:] if ln.strip()]
    ind = min((len(ln) - len(ln.lstrip()) for ln in rest), default=0)
    units, cur = [], None
    for k, ln in enumerate(lines):
        t = ln.strip() if k == 0 else ln[ind:]
        line = start + k
        if not t.strip() or t.startswith((" ", "\t")) or t.lstrip().startswith(">>>"):
            if cur:
                units.append(Unit(cur[0], cur[1], "docstring"))
            cur = None
            continue
        b = BULLET_RE.match(t)
        if b:
            if cur:
                units.append(Unit(cur[0], cur[1], "docstring"))
            cur = [line, [b.group(2)]]
        elif cur is None:
            cur = [line, [t.strip()]]
        else:
            cur[1].append(t.strip())
    if cur:
        units.append(Unit(cur[0], cur[1], "docstring"))
    return units


STRING_RE = re.compile(r"""(?P<pre>[rRbBuUfF]{0,2})(?P<q>'''|\"\"\"|'|")(?P<body>.*?)(?<!\\)(?P=q)""")


def py_units(text):
    """Comments, docstrings and marked operator strings of a Python source."""
    units = []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    units += doc_units(first.lineno, first.value.value)
    src_lines = text.split("\n")
    group = None                                       # [first line, last line, col, [texts]]
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        toks = []
    for tok in toks:
        if tok.type != tokenize.COMMENT:
            continue
        line, col = tok.start
        raw = tok.string
        if line == 1 and raw.startswith("#!"):
            continue
        body = raw.lstrip("#").strip()
        if re.match(r"^STE\b", body) or re.search(r"\bSTE\s*$", body):
            src = src_lines[line - 1][:col] if line - 1 < len(src_lines) else ""
            for m in STRING_RE.finditer(src):
                s = re.sub(r"\{[^{}]*\}", PLACE, m.group("body")) if "f" in m.group("pre").lower() else m.group("body")
                units.append(Unit(line, [s], "ui"))
            continue
        body = NOQA_RE.sub("", body).strip()
        body = re.sub(r"^[-=~*#_]{3,}\s*|\s*[-=~*#_]{3,}$", "", body).strip()
        full = line - 1 < len(src_lines) and not src_lines[line - 1][:col].strip()
        skip = not body or DIRECTIVE_RE.match(body) or looks_like_code(body)
        if full and group and group[1] == line - 1 and group[2] == col and not skip:
            group[1] = line
            group[3].append(body)
            continue
        if group:
            units.append(Unit(group[0], group[3], "comment"))
            group = None
        if skip:
            continue
        if full:
            group = [line, line, col, [body]]
        else:
            units.append(Unit(line, [body], "comment"))
    if group:
        units.append(Unit(group[0], group[3], "comment"))
    return units


def json_units(text):
    """The "meaning" values of a names.json, one unit each."""
    out = []
    for i, line in enumerate(text.split("\n"), 1):
        for m in re.finditer(r'"meaning"\s*:\s*"((?:[^"\\]|\\.)*)"', line):
            out.append(Unit(i, [m.group(1)], "ui"))
    return out


def msg_units(text):
    """The body of a commit message: no subject, no comments, no trailers."""
    lines = []
    for ln in text.replace("\r\n", "\n").split("\n"):
        if re.match(r"^# -+ >8 -+", ln):
            break
        lines.append("" if ln.startswith("#") else ln)
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and lines[i].strip():      # the subject paragraph
        i += 1
    for k in range(i):
        lines[k] = ""
    # trailers: the last paragraph when every line is "Key: value"
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    start = end
    while start and lines[start - 1].strip():
        start -= 1
    if start < end and all(re.match(r"^[A-Za-z][\w-]*: \S", ln) for ln in lines[start:end]):
        for k in range(start, end):
            lines[k] = ""
    return md_units("\n".join(lines), kind="msg")


def units_for(path, text):
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if p.endswith(".md"):
        return md_units(text)
    if p.endswith(".py"):
        return py_units(text)
    if name == "names.json":
        return json_units(text)
    return []


def wanted(path):
    p = path.replace("\\", "/")
    if p.startswith("_archive/") or "/_archive/" in p:
        return False
    return p.endswith((".md", ".py")) or p.rsplit("/", 1)[-1] == "names.json"


# ---------------------------------------------------------------- masking and splitting

def _keep_nl(m):
    return PLACE + "\n" * m.group(0).count("\n")


def _mask_token(m, allow):
    tok = m.group(0)
    q = re.match(r"^([(\[]?)'[^'\s]+'([.,;:!?)\]]*)$", tok)
    if q:                                            # a quoted key such as 'REV:'
        return q.group(1) + PLACE + q.group(2)
    i, j = 0, len(tok)
    while i < j and tok[i] in LEAD_PUNCT:
        i += 1
    while j > i and tok[j - 1] in TRAIL_PUNCT:
        j -= 1
    core = tok[i:j]
    if not core or core == PLACE:
        return tok
    if core.lower() in ("w/", "w/o", "e.g", "i.e") or DOTTED_ABBR_RE.match(core + "."):
        return tok
    code = (("/" in core or "\\" in core) and re.search(r"\w", core)) \
        or FILE_RE.match(core) \
        or re.search(r"\w_\w", core) or core.startswith("_") \
        or re.match(r"^--?[A-Za-z]", core) \
        or "=" in core or re.search(r"\w[(\[]", tok) or core.startswith(("~/", "$")) \
        or (CAMEL_RE.match(core) and core not in allow) \
        or re.match(r"^[A-Za-z_]\w*\.[A-Za-z_]\w*", core) \
        or re.search(r"[<>{}]", core)
    if not code:
        return tok
    return tok[:i] + PLACE + tok[j:]


def mask(text, allow=frozenset()):
    """Replace code, links, URLs, paths and identifiers by one placeholder each.
    The number of newlines stays the same, so line numbers stay correct."""
    t = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    t = re.sub(r"(`+)(.+?)\1", _keep_nl, t, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", PLACE, t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"<https?://[^>]*>|https?://\S+|www\.\S+", PLACE, t)
    t = re.sub(r"<[^>\s]+>", PLACE, t)
    t = t.replace("**", "").replace("__", "")
    return re.sub(r"\S+", lambda m: _mask_token(m, allow), t)


def split_sentences(t):
    """[(start, end)] spans of the sentences in a masked text."""
    spans, start = [], 0
    for m in END_RE.finditer(t):
        before = re.search(r"(\S+)$", t[start:m.start()] + "")
        word = before.group(1).lower().lstrip(LEAD_PUNCT) if before else ""
        if word in NO_SPLIT or DOTTED_ABBR_RE.match(word + ".") or re.fullmatch(r"(?:[a-z]\.)*[a-z]", word) and "." in word:
            continue
        nxt = re.match(r"\s*(\S)", t[m.end():])
        if nxt and nxt.group(1).islower():
            continue
        spans.append((start, m.end()))
        start = m.end()
    if t[start:].strip():
        spans.append((start, len(t)))
    return spans


# ---------------------------------------------------------------- checks

def words_of(s):
    return [w for w in s.split() if re.search(r"[\w" + PLACE + "]", w)]


def _clean(w):
    return w.strip(LEAD_PUNCT + TRAIL_PUNCT).lower()


def is_prose(s):
    """False for fragments and lines that hold mostly numbers, units or code."""
    ws = words_of(s)
    alpha = [w for w in ws if re.search(r"[A-Za-z]{2,}", w) and PLACE not in w]
    other = len(ws) - len(alpha)
    return len(alpha) >= 3 and other <= len(alpha)


def strip_label(s):
    """(label, body).  The length check does not count a lead label of at most 6 words before a colon."""
    m = re.match(r"^([^:]{1,60}?):\s+(?=\S)", s)
    if m and len(m.group(1).split()) <= 6 and not re.search(r"\d$", m.group(1)):
        return m.group(1), s[m.end():]
    return "", s


LEAD_CLAUSE = {"when", "before", "after", "if", "once", "unless", "while", "until", "on", "for", "in"}


def _starts_command(ws):
    if not ws:
        return False
    if ws[0] in ("do", "don't") and len(ws) > 1 and ws[1] in ("not", "never"):
        return True
    return ws[0] in IMPERATIVE


def is_imperative(body):
    """A command: it starts with a verb, or with a lead clause and then a verb
    ("When the run stops, read the log.")."""
    ws = [_clean(w) for w in body.split()]
    if _starts_command(ws[:2]):
        return True
    if ws and ws[0] in LEAD_CLAUSE:
        m = re.search(r",\s+(\S+)(?:\s+(\S+))?", body)
        if m:
            return _starts_command([_clean(m.group(1)), _clean(m.group(2) or "")])
    return False


def find_passive(body, stative):
    toks = [_clean(w) for w in body.split()]
    for i, w in enumerate(toks):
        if w not in BE:
            continue
        j = i + 1
        while j < len(toks) and j <= i + 3 and (toks[j] in SKIP_ADVERBS or (toks[j].endswith("ly") and len(toks[j]) > 4)):
            j += 1
        if j >= len(toks):
            continue
        t = toks[j]
        if t in stative:
            continue
        if t in IRREGULAR_PP or (t.endswith("ed") and len(t) > 4 and t not in ED_NOT_PP and t.isalpha()):
            return " ".join(toks[i:j + 1])
    return ""


def split_hint(ws, limit):
    """Where to split a long sentence: a conjunction near the middle."""
    best = None
    for k, w in enumerate(ws):
        if _clean(w) in CONJ and 5 <= k <= len(ws) - 5:
            if best is None or abs(k - len(ws) / 2) < abs(best - len(ws) / 2):
                best = k
    if best is not None:
        return f"split into two sentences before '{_clean(ws[best])}' (word {best + 1})"
    return f"split into sentences of at most {limit} words"


def defined_in(abbr, full):
    a = re.escape(abbr)
    pats = (rf"\(\s*{a}\s*[),]",
            rf"(?<![\w-]){a}s?\s*\((?!\s*\d)[A-Za-z][^)]{{2,}}\)",
            rf"(?<![\w-]){a}\s*(?:=|means|stands for|is short for)\s")
    return any(re.search(p, full) for p in pats)


def check_sentence(sent, words, names, full_text, cache):
    label, body = strip_label(sent.text)
    imperative = is_imperative(body)
    limit = PROCEDURAL_MAX if imperative else DESCRIPTIVE_MAX
    ws = words_of(body)
    if len(ws) > limit:
        sent.fails.append(("length", f"{len(ws)} > {limit}" + (" (command)" if imperative else ""),
                           split_hint(ws, limit)))
    # the other checks skip quoted text: a quote cites words, it does not use them
    s = re.sub(r'"[^"]*"|“[^”]*”', '"' + PLACE + '"', sent.text)
    label, body = strip_label(s)
    pv = find_passive(body, words["stative"])
    if pv:
        sent.fails.append(("passive", f"'{pv}'", "use the active voice: say who or what does it"))
    for word, rx, use in words["jargon"]:
        m = rx.search(s)
        if m:
            sent.fails.append(("jargon", f"'{m.group(0)}'", f"use '{use}'" if use else "use an approved word"))
    if ";" in s:
        sent.fails.append(("semicolon", "';'", "end the sentence with a full stop and start a new one"))
    # technical names: mask the display names, then look for aliases
    if names:
        tmp = s
        for rx in names.displays:
            tmp = rx.sub(PLACE, tmp)
        seen = set()
        for alias, rx, disp in names.aliases:
            if alias in seen:
                continue
            if rx.search(tmp):
                seen.add(alias)
                sent.fails.append(("name", f"'{alias}' is an alias", f"use '{disp}'"))
    # abbreviations
    reported = set()
    for w in re.split(r"[\s/-]+", s):                 # matched-LO holds LO
        core = w.strip(LEAD_PUNCT + TRAIL_PUNCT)
        core = re.sub(r"['’]s$", "", core)
        if not ABBR_RE.match(core):
            continue
        base = core[:-1] if core.endswith("s") else core
        letters = sum(c.isalpha() for c in base)
        digits = sum(c.isdigit() for c in base)
        if not 2 <= letters <= 6 or digits > letters or base in reported:   # LMH5401 is a part number
            continue
        if core in words["abbreviations"] or base in words["abbreviations"]:
            continue
        if names and (core in names.tokens or base in names.tokens):
            continue
        if base.isalpha() and len(base) >= 3 and re.search(
                rf"(?<![\w-]){base.lower()}(?![\w-])", cache.get(PROSE, "")):
            continue                                   # emphasis: the file uses the word in lowercase
        key = base
        if key not in cache:
            cache[key] = defined_in(base, full_text)
        if not cache[key]:
            reported.add(base)
            sent.fails.append(("abbrev", f"'{base}' not defined in this file",
                               f"write the full term at first use, then ({base})"))
    # colons: a warning only
    for m in re.finditer(r"(?<!\d):(?!\d)", s):
        before, after = s[:m.start()], s[m.end():]
        if not after.strip():
            continue
        if label and m.start() == len(label):
            continue
        if len(before.split()) <= 4 or after.count(",") >= 2:
            continue
        sent.warns.append(("colon", "a colon between two clauses",
                           "use a colon only after a label or before a list"))
        break


def sentences_of(path, units, changed, words, names, full_text):
    """Every prose sentence in the units, with its checks done.  changed is a
    set of line numbers, or None for every line."""
    out = []
    allow = words["abbreviations"] | (names.tokens if names else set())
    masked = [mask("\n".join(u.lines), allow) for u in units]
    cache = {PROSE: "\n".join(masked)}
    for u, t in zip(units, masked):
        for a, b in split_sentences(t):
            first = u.line + t[:a].count("\n") + (1 if t[a:b].startswith("\n") else 0)
            last = u.line + t[:b].rstrip().count("\n")
            text = " ".join(t[a:b].split())
            if not text or not is_prose(text):
                continue
            if changed is not None and not any(n in changed for n in range(first, last + 1)):
                continue
            s = Sentence(path, first, last, text, u.kind)
            check_sentence(s, words, names, full_text, cache)
            out.append(s)
    return sorted(out, key=lambda s: s.line)


# ---------------------------------------------------------------- git input

def git(repo, *args, timeout=120):
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def added_lines(diff):
    """{path: set of added line numbers} from a -U0 diff."""
    out, path = {}, None
    for ln in diff.split("\n"):
        if ln.startswith("+++ "):
            p = ln[4:].strip()
            if p.startswith('"') and p.endswith('"'):
                p = p[1:-1]
            path = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
            if path is not None:
                out.setdefault(path, set())
        elif ln.startswith("@@") and path is not None:
            m = re.match(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", ln)
            if m:
                c, d = int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1
                out[path].update(range(c, c + d))
    return out


def check_diff(top, diff, blob_prefix, words, names):
    """Sentences on the added lines of a diff.  blob_prefix is ':' for the index
    or 'REV:' for a commit."""
    res = []
    for path, lines in sorted(added_lines(diff).items()):
        if not lines or not wanted(path):
            continue
        rc, text, _ = git(top, "show", f"{blob_prefix}{path}")
        if rc != 0:
            continue
        text = text.replace("\r\n", "\n")
        res += sentences_of(path, units_for(path, text), lines, words, names, text)
    return res


def staged(top, words, names):
    rc, diff, err = git(top, "diff", "--cached", "--no-color", "--no-ext-diff", "--no-renames",
                        "-U0", "--diff-filter=AM")
    if rc != 0:
        raise RuntimeError(f"git diff --cached failed: {err.strip()[:120]}")
    return check_diff(top, diff, ":", words, names)


def commit(top, rev, words, names):
    rc, diff, err = git(top, "diff-tree", "-p", "-r", "--root", "--no-color", "--no-ext-diff",
                        "--no-renames", "-U0", "--diff-filter=AM", rev)
    if rc != 0:
        raise RuntimeError(f"git diff-tree {rev} failed: {err.strip()[:120]}")
    res = check_diff(top, diff, f"{rev}:", words, names)
    rc, msg, _ = git(top, "log", "-1", "--format=%B", rev)
    if rc == 0:
        res += sentences_of(f"{rev} message", msg_units(msg), None, words, names, msg)
    return res


def newest_entry_lines(text):
    """Line numbers of the newest dated entry of a LOG.md (None when there is none)."""
    lines = text.split("\n")
    start = None
    for i, ln in enumerate(lines, 1):
        if start is None and re.match(r"^## \d{4}-\d{2}-\d{2}", ln):
            start = i
        elif start is not None and ln.startswith("## "):
            return set(range(start, i))
    return set(range(start, len(lines) + 1)) if start else None


# ---------------------------------------------------------------- output

def pct(n, m):
    return f"{100.0 * n / m:.0f} %" if m else "-"


def show(text, width=110):
    t = text.replace(PLACE, SHOW_PLACE)
    return t if len(t) <= width else t[:width - 3] + "..."


def reasons(s):
    return ", ".join([f"{r} {d}" for r, d, _ in s.fails] + [f"warn {r}" for r, _, _ in s.warns])


def print_full(res, names_note):
    by_path = {}
    for s in res:
        by_path.setdefault(s.path, []).append(s)
    for path, ss in by_path.items():
        ss.sort(key=lambda s: s.line)
        ok = sum(s.ok for s in ss)
        print(f"stecheck: {path}  {ok} of {len(ss)} sentences pass ({pct(ok, len(ss))})")
        for s in ss:
            if s.ok and not s.warns:
                continue
            print(f"  L{s.line}  {reasons(s)}")
            print(f"       \"{show(s.text)}\"")
            for _, _, fix in s.fails + s.warns:
                print(f"       fix: {fix}")
    if names_note:
        print(f"stecheck: {names_note}")


def summary_line(res, mode, what):
    ok, n = sum(s.ok for s in res), len(res)
    if not n:
        return f"stecheck: {mode}  no {what}sentences to check"
    return f"stecheck: {mode}  {ok} of {n} {what}sentences pass ({pct(ok, n)})"


def print_brief(res, k, mode, what, rerun):
    print(summary_line(res, mode, what))
    bad = [s for s in res if not s.ok]
    for s in bad[:k]:
        print(f"stecheck:   {s.path}:{s.line}  {reasons(s)}  \"{show(s.text, 80)}\"")
    if len(bad) > k:
        print(f"stecheck:   ... {len(bad) - k} more; the full list: {rerun}")


# ---------------------------------------------------------------- main

def main(argv):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="files to check in full")
    ap.add_argument("--staged", action="store_true", help="the staged commit (default without paths)")
    ap.add_argument("--commit", metavar="REV", help="what commit REV added, and its message body")
    ap.add_argument("--msg", metavar="FILE", help="a commit message file")
    ap.add_argument("--repo", help="the repo for --staged or --commit (default: the current one)")
    ap.add_argument("--all", action="store_true", help="every tracked file of the repo (--repo, default: the current one)")
    ap.add_argument("--newest-entry", action="store_true", help="with paths: only the newest dated LOG.md entry")
    ap.add_argument("--brief", type=int, metavar="N", help="print the summary line and the first N failures")
    ap.add_argument("--names", help="a names.json to use")
    ap.add_argument("--words", default=str(WORDS_FILE), help="the word lists (default: beside this script)")
    ap.add_argument("--strict", action="store_true", help="exit 1 when the pass rate is below --min")
    ap.add_argument("--min", type=float, default=MIN_PASS, help="the pass rate for --strict (default 0.8)")
    a = ap.parse_args(argv)
    hook = not a.paths and not a.commit and not a.all
    try:
        return run(a)
    except Exception as e:  # noqa: BLE001
        if not hook or a.strict:
            raise
        print(f"stecheck: internal error ({e.__class__.__name__}: {e}); the commit is allowed")
        return 0


def run(a):
    words = load_words(a.words)
    top = a.repo
    if top is None and (a.all or not a.paths) and not a.msg:
        rc, out, _ = git(".", "rev-parse", "--show-toplevel")
        top = out.strip() if rc == 0 else "."
    names = find_names(a.names, [top, os.getcwd()])
    names_note = "" if names else f"no names.json found ({NAMES_REL}); the name check did not run"
    if a.msg:
        text = Path(a.msg).read_text(encoding="utf-8", errors="replace")
        res = sentences_of("commit message", msg_units(text), None, words, names, text)
        mode, what, rerun = "advisory", "message ", f"stecheck.py --msg {a.msg}"
    elif a.commit:
        res = commit(top or ".", a.commit, words, names)
        mode, what, rerun = "", "new ", f"stecheck.py --commit {a.commit}"
    elif a.paths or a.all:
        if a.all:
            base = Path(top)
            rc, out, err = git(base, "ls-files", "-z")
            if rc != 0:
                raise RuntimeError(f"git ls-files failed: {err.strip()[:120]}")
            paths = [str(base / f) for f in out.split("\0") if f and wanted(f)]
            rerun = f"stecheck.py --all --repo {top}"
        else:
            paths, rerun = a.paths, "stecheck.py " + " ".join(a.paths)
        res, by_top = [], {}
        for p in paths:
            try:
                text = Path(p).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue
            ptop = repo_top(p)
            if ptop not in by_top:
                by_top[ptop] = find_names(a.names, [ptop])
            changed = newest_entry_lines(text) if a.newest_entry and p.endswith(".md") else None
            shown = os.path.relpath(p, top).replace("\\", "/") if a.all else p.replace("\\", "/")
            res += sentences_of(shown, units_for(p, text) if wanted(p) or p.endswith(".py") else [],
                                changed, words, by_top[ptop], text)
        mode, what = "", ""
        names = next((n for n in by_top.values() if n), names)
        names_note = "" if names else f"no names.json found ({NAMES_REL}); the name check did not run"
    else:
        res = staged(top, words, names)
        mode, what, rerun = "advisory", "new ", "python shared/scripts/stecheck.py --staged"
    mode = mode or ("strict" if a.strict else "advisory")
    if a.brief is not None:
        print_brief(res, a.brief, mode, what, rerun)
    else:
        print_full(res, names_note)
        print(summary_line(res, mode, what))
    rate = sum(s.ok for s in res) / len(res) if res else 1.0
    return 1 if a.strict and rate < a.min else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
