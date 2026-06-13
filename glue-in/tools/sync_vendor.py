#!/usr/bin/env python3
"""Sync the vendored copies under ``third_party/`` from the canonical NIC repos.

Single source of truth: the canonical libraries live in their own repositories
(NIC-MLA, NIC-DMD, ...). The copies under ``third_party/`` exist only so this
repo stays self-contained and runnable on its own. Hand-copying those files is
how they silently drift out of date; this script makes the copy mechanical and
adds a ``--check`` mode so CI can fail the moment a vendored file falls behind.

The mapping ``vendored path  <-  canonical source`` lives in
``tools/vendor_manifest.txt`` next to this script.

Usage
-----
    python3 tools/sync_vendor.py            # copy canonical -> vendored (write)
    python3 tools/sync_vendor.py --check    # verify vendored == canonical (CI)

The canonical repos are looked up under ``$NIC_SRC_DIR`` (default: the parent
directory of this repo, so sibling checkouts just work). Each repo name is
matched case-insensitively, so ``NIC-MLA`` resolves to ``nic-mla`` too.

Exit codes: 0 = in sync / written, 1 = drift found (``--check``), 2 = a
canonical source could not be located.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO_ROOT, "tools", "vendor_manifest.txt")
SRC_DIR = os.environ.get("NIC_SRC_DIR", os.path.dirname(REPO_ROOT))


def _resolve_repo(name: str) -> str | None:
    """Find the canonical repo dir under SRC_DIR, tolerating name casing."""
    seen = []
    for cand in (name, name.lower(), name.upper(), name.replace("-", "_"),
                 name.replace("_", "-")):
        if cand in seen:
            continue
        seen.append(cand)
        path = os.path.join(SRC_DIR, cand)
        if os.path.isdir(path):
            return path
    return None


def _entries():
    """Parse the manifest into (dest_rel, src_repo, src_rel) tuples."""
    out = []
    with open(MANIFEST, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if "<-" not in line:
                raise SystemExit(f"bad manifest line (no '<-'): {raw!r}")
            dest, src = (part.strip() for part in line.split("<-", 1))
            repo, rel = src.split("/", 1)
            out.append((dest, repo, rel))
    return out


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    args = sys.argv[1:]
    check = "--check" in args
    require_sources = "--require-sources" in args

    entries = _entries()
    missing_src, drift, written, ok = [], [], [], 0

    for dest_rel, repo, rel in entries:
        base = _resolve_repo(repo)
        src = os.path.join(base, rel) if base else None
        dest = os.path.join(REPO_ROOT, dest_rel)

        if src is None or not os.path.isfile(src):
            missing_src.append((dest_rel, f"{repo}/{rel}"))
            continue

        if check:
            if not os.path.isfile(dest) or _sha(src) != _sha(dest):
                drift.append(dest_rel)
            else:
                ok += 1
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(src, dest)
            written.append(dest_rel)

    if missing_src:
        where = f"NIC_SRC_DIR={SRC_DIR!r}"
        print(f"⚠  {len(missing_src)} canonical source(s) not found under {where}:")
        for dest_rel, src in missing_src:
            print(f"     {src}  (for {dest_rel})")
        if require_sources:
            print("✗  --require-sources set: canonical repos must be checked out.")
            return 2

    if check:
        if drift:
            print(f"✗  {len(drift)} vendored file(s) DRIFTED from canonical:")
            for dest_rel in drift:
                print(f"     {dest_rel}")
            print("\n   Run:  python3 tools/sync_vendor.py   then commit the result.")
            return 1
        print(f"✓  vendored copies in sync ({ok} file(s) verified).")
        return 0

    print(f"✓  synced {len(written)} vendored file(s) from canonical sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
