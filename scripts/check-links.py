#!/usr/bin/env python3
"""Find file paths mentioned in the repo's docs that don't actually exist.

Run it:  python3 scripts/check-links.py
Exit 1 if anything is broken, so it can go in CI or the pre-commit hook later.

Two failure modes it catches, and they are different problems:

  BROKEN   a mention with a slash in it — an actual path — that resolves to
           nothing. Almost always a rename that wasn't followed through.
           This is the only one that fails the run.
  NO MATCH a bare filename with no file of that name anywhere in the repo.
           Warned about, not failed: docs legitimately name runtime files
           (command.json, result.json) that only exist inside a live session.
  IGNORED  a real path that resolves to a gitignored file, so someone who
           clones the repo won't have it. Fine inside a personal note, a bug
           inside README.md or docs/SETUP.md. Reported, doesn't fail.
           Bare filenames are excluded here on purpose: "write
           company-brief.md" names a file *type*, and matching it against
           some gitignored KLA folder would be a meaningless hit.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__", "graphify-out", "node_modules", ".venv"}
# This file's own docstring names example paths on purpose; scanning it would
# report them as broken. Same idea as the pre-commit hook's allowlist.
SKIP_FILES = {"scripts/check-links.py"}
# Only these get scanned for path mentions. Binary formats (.pdf/.pptx/.xlsx)
# obviously can't be, and neither can the résumé folder's contents.
SCAN_EXT = {".md", ".py", ".sh", ".json"}
# A backtick span only counts as a path if it ends in one of these. Without
# this, every `variable_name` in prose gets treated as a missing file.
PATH_EXT = {".md", ".py", ".sh", ".json", ".pdf", ".pptx", ".xlsx", ".txt",
            ".yml", ".yaml", ".plist", ".png", ".jpg", ".csv"}

# Stock fake names the docs use when showing what a path looks like. A hit on
# one of these is the doc doing its job, not a broken link.
PLACEHOLDER = re.compile(r"Acme|<[^>]+>|公司名|職稱")

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
BACKTICK = re.compile(r"`([^`\n]+)`")


def is_pathlike(text):
    """Reject prose, shell one-liners, URLs, and globs before hitting the disk."""
    text = text.strip()
    if not text or text.startswith(("http://", "https://", "mailto:", "#", "$", "<")):
        return False
    if PLACEHOLDER.search(text):
        return False
    if any(c in text for c in "*?|<>\n"):          # globs and shell plumbing
        return False
    if " " in text:
        # 反引號裡的指令(`cp a b`、`curl … http://…/`)整串拿去查檔案一定查不到,
        # 只會變成假的 broken。第一個字不像路徑就當它是指令,不是路徑。
        head = text.split()[0]
        if "/" not in head and os.path.splitext(head)[1].lower() not in PATH_EXT:
            return False
    if text.endswith("/"):                          # directory, checked separately
        return True
    return os.path.splitext(text)[1].lower() in PATH_EXT


def build_suffix_index():
    """Prose shortens paths — README says `sop/workday.md`, the file lives at
    `automation/job-apply/_internal/sop/workday.md`. A unique suffix match is
    what the reader means. Still strict enough to catch a rename: a stale
    `sop/company_brief.md` matches no suffix at all."""
    index = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames + dirnames:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
            parts = rel.split(os.sep)
            for i in range(len(parts)):
                index.setdefault(os.sep.join(parts[i:]), os.path.join(ROOT, rel))
    return index


def build_basename_index():
    """Docs mostly name files the way people talk about them — "write
    company-brief.md" — with no directory at all. Those aren't paths, so
    resolving them relative to anything is wrong. Match on basename instead."""
    index = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames + dirnames:
            index.setdefault(name, os.path.join(dirpath, name))
    return index


def resolve(raw, source_file, index, suffixes):
    """Returns (abs_path_or_None, kind) where kind is 'path' or 'bare'."""
    raw = raw.split("#")[0].strip().strip("'\"")
    if not raw:
        return None, "path"
    if raw.startswith("~") or os.path.isabs(raw):
        # Outside the repo — a runtime session dir, a Keychain path, the user's
        # home. Nothing here can verify it, so don't pretend to.
        return "skip", "path"
    if "/" not in raw.rstrip("/"):
        return index.get(raw.rstrip("/")), "bare"
    for base in (os.path.dirname(source_file), ROOT):
        candidate = os.path.normpath(os.path.join(base, raw))
        if os.path.exists(candidate):
            return candidate, "path"
    return suffixes.get(raw.rstrip("/")), "path"


def gitignored(paths):
    """One batch call — `git check-ignore` per path is far too slow here."""
    if not paths:
        return set()
    # -z on both sides: without it git backslash-escapes any non-ASCII path,
    # and the Chinese filenames in this repo come back unmatchable.
    proc = subprocess.run(["git", "check-ignore", "--stdin", "-z"], cwd=ROOT,
                          input="\0".join(paths), capture_output=True, text=True)
    return {p for p in proc.stdout.split("\0") if p}


def main():
    broken, nomatch, ignored_hits, resolved = [], [], [], {}
    index = build_basename_index()
    suffixes = build_suffix_index()

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1] not in SCAN_EXT:
                continue
            path = os.path.join(dirpath, name)
            if os.path.relpath(path, ROOT) in SKIP_FILES:
                continue
            try:
                lines = open(path, encoding="utf-8").read().splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(lines, 1):
                for raw in MD_LINK.findall(line) + BACKTICK.findall(line):
                    if not is_pathlike(raw):
                        continue
                    where = "%s:%d" % (os.path.relpath(path, ROOT), lineno)
                    hit, kind = resolve(raw, path, index, suffixes)
                    if hit == "skip":
                        continue
                    if hit is None:
                        (nomatch if kind == "bare" else broken).append((where, raw))
                    elif kind == "path":
                        resolved.setdefault(os.path.relpath(hit, ROOT), []).append((where, raw))

    for rel in gitignored(list(resolved)):
        for where, raw in resolved[rel]:
            ignored_hits.append((where, raw))

    if broken:
        print("\n  BROKEN — path mentioned in a doc but not on disk\n")
        for where, raw in sorted(set(broken)):
            print("    %-58s %s" % (where, raw))

    if nomatch:
        print("\n  NO MATCH — filename named in a doc, no such file in the repo\n")
        for where, raw in sorted(set(nomatch)):
            print("    %-58s %s" % (where, raw))

    if ignored_hits:
        print("\n  IGNORED — exists locally, absent from a fresh clone\n")
        for where, raw in sorted(set(ignored_hits)):
            print("    %-58s %s" % (where, raw))

    print("\n  %d broken, %d unmatched, %d gitignored, %d resolved OK\n"
          % (len(set(broken)), len(set(nomatch)), len(set(ignored_hits)), len(resolved)))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
