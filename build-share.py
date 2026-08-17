#!/usr/bin/env python3
"""Generate share.html (the read-only viewer) from index.html + share.template.html.

The viewer is a separate artifact with its own storage, so it cannot read the
tracker's localStorage. It ships with the seed data baked in and accepts a live
snapshot through the URL fragment instead.

Everything both pages must agree on -- the stylesheet, the catalog, the phases,
the dot definitions, the seed data, the date helpers and the charts -- is lifted
out of index.html at build time so the two can never drift.

Usage: python3 build-share.py
"""
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).parent
SRC = (ROOT / "index.html").read_text()
TPL = (ROOT / "share.template.html").read_text()


def between(start: str, end: str, what: str) -> str:
    i = SRC.find(start)
    j = SRC.find(end, i + 1)
    if i < 0 or j < 0:
        sys.exit(f"build-share: could not locate the {what} block in index.html")
    return SRC[i:j].rstrip()


css = re.search(r"<style>(.*?)</style>", SRC, re.S)
if not css:
    sys.exit("build-share: no <style> block in index.html")

BLOCKS = {
    "__SHARED_CSS__": css.group(1).strip(),
    # constants: INJURY/GOAL, DOTS, PHASES, BENCH, GROUPS, CATALOG, STACKS, seed data
    "__SHARED_CONST__": between(
        "/* ════════ CONSTANTS ════════ */",
        "/* ════════ STATE ════════ */",
        "constants",
    ),
    # helpers: dates, formatting, dose maths, dot derivation, time units
    "__SHARED_HELPERS__": between(
        "/* ════════ HELPERS ════════ */",
        "/* ════════ SHEET ════════ */",
        "helpers",
    ),
    # charts: topRound, apapChart, sleepChart, dotChart, wireHover
    "__SHARED_CHARTS__": between(
        "function topRound(",
        "/* ════════ 5 · PLAN ════════ */",
        "chart",
    ),
}

# The viewer must ship with NO record of its own. index.html's seed carries real
# clinical and personal notes, and anything left in the file is readable via
# view-source regardless of which share mode built the link.
seed_at = BLOCKS["__SHARED_CONST__"].find("/* ════════ SEED")
if seed_at < 0:
    sys.exit("build-share: could not find the seed block to strip")
BLOCKS["__SHARED_CONST__"] = BLOCKS["__SHARED_CONST__"][:seed_at] + (
    "/* The viewer ships with no data of its own — a snapshot arrives in the URL\n"
    "   fragment. Opened without one, it shows an empty state. */\n"
    "const SEED_ENTRIES = [];\nconst SEED_DAILY = {};\n"
)

out = TPL
for marker, code in BLOCKS.items():
    token = f"/*{marker}*/"
    if token not in out:
        sys.exit(f"build-share: template is missing {token}")
    out = out.replace(token, code)

# Fail the build if any note text from the tracker survived into the viewer:
# the 5th argument of an s(...) seed call, and any note: on a seed daily record.
notes = set(re.findall(r's\("[\d-]+","[\d:]+","\w+",[\d.]+,"((?:[^"\\]|\\.)+)"\)', SRC))
notes |= set(re.findall(r'note:\s*"((?:[^"\\]|\\.)+)"', SRC))
leaked = sorted(n for n in notes if n in out)
if leaked:
    sys.exit(
        "build-share: personal notes leaked into share.html:\n  "
        + "\n  ".join(repr(n[:70]) for n in leaked[:8])
    )

# the viewer must never carry write paths or the capture surface
for banned in ("localStorage", "openEntry(", "renderCatalog(", "toggleDot("):
    if banned in out:
        sys.exit(f"build-share: viewer must not contain {banned!r}")

(ROOT / "share.html").write_text(out)
print(f"share.html written: {len(out):,} bytes")
