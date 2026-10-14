"""Fast filesystem scanning: large files, duplicates and folder-size trees."""
from __future__ import annotations

import hashlib
import heapq
import os
import time
from collections import defaultdict

from .utils import IS_WINDOWS, settings

_REPARSE = 0x400
_OFFLINE = 0x1000
_RECALL_ON_OPEN = 0x40000
_RECALL_ON_DATA = 0x400000
_LINK_TAGS = (0xA0000003, 0xA000000C)  # junction / mount point, symlink

_SKIP_DIR_NAMES = {"$recycle.bin", "system volume information", "$winreagent", "config.msi",
                   "$windows.~bt", "$windows.~ws", "$sysreset", "recovery", "proc", "sys"}

CATEGORY_EXTS = {
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".3gp"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".raw", ".cr2",
               ".nef", ".arw", ".dng", ".psd", ".svg", ".ico"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".aiff"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".cab", ".zst"},
    "Installers & images": {".exe", ".msi", ".msix", ".appx", ".iso", ".img", ".vhd", ".vhdx", ".vmdk",
                            ".dmg", ".pkg"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".odt",
                  ".csv", ".epub", ".md"},
    "Code & data": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".json", ".xml",
                    ".sql", ".db", ".sqlite", ".log", ".bak", ".dmp", ".pak", ".dat", ".bin"},
}
_EXT_TO_CAT = {ext: cat for cat, exts in CATEGORY_EXTS.items() for ext in exts}
CATEGORY_COLORS = {"Video": "#7c5cff", "Images": "#22d3ee", "Audio": "#f472b6", "Archives": "#f5a524",
                   "Installers & images": "#f05252", "Documents": "#2fd07a", "Code & data": "#60a5fa",
                   "Other": "#8a93a8"}


def categorize(path: str) -> str:
    return _EXT_TO_CAT.get(os.path.splitext(path)[1].lower(), "Other")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _system_skip_paths() -> set:
    if IS_WINDOWS:
        return {_norm(os.environ.get("SystemRoot", r"C:\Windows"))}
    return {"/proc", "/sys", "/dev", "/run", "/snap"}


def _is_link(entry, st) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)
    if is_junction is not None:
        try:
            if is_junction():
                return True
        except OSError:
            return False
    if IS_WINDOWS and getattr(st, "st_file_attributes", 0) & _REPARSE:
        return getattr(st, "st_reparse_tag", 0) in _LINK_TAGS
    return False


def walk_entries(roots, ctx=None, skip_system=True, exclusions=None):
    """Yield (path, size, mtime) for every regular file under roots.

    Never follows symlinks/junctions, skips cloud placeholders that use no disk space,
    honours user exclusions and reports progress to ctx about every 0.2 s.
    """
    excl = {_norm(e) for e in (settings.get("exclusions") if exclusions is None else exclusions) or []}
    skip_paths = _system_skip_paths() if skip_system else set()
    stack = list(roots)
    files = 0
    last = 0.0
    while stack:
        if ctx is not None and ctx.cancelled():
            return
        folder = stack.pop()
        try:
            it = os.scandir(folder)
        except OSError:
            continue
        with it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                    if _is_link(entry, st):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if skip_system and entry.name.lower() in _SKIP_DIR_NAMES:
                            continue
                        n = _norm(entry.path)
                        if n in excl or n in skip_paths:
                            continue
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        attrs = getattr(st, "st_file_attributes", 0)
                        if attrs & (_OFFLINE | _RECALL_ON_DATA | _RECALL_ON_OPEN):
                            continue  # cloud-only placeholder, takes no disk space
                        files += 1
                        yield entry.path, st.st_size, st.st_mtime
                except OSError:
                    continue
        if ctx is not None:
            now = time.monotonic()
            if now - last > 0.2:
                last = now
                ctx.progress(f"{files:,} files scanned: {folder}")


# --------------------------------------------------------------------------- large files
def find_large_files(ctx, roots, min_bytes, older_than_days=0, limit=5000) -> dict:
    cutoff = time.time() - older_than_days * 86400 if older_than_days else None
    heap: list = []
    for path, size, mtime in walk_entries(roots, ctx, skip_system=settings.get("skip_system_folders")):
        if size < min_bytes or (cutoff and mtime > cutoff):
            continue
        item = (size, path, mtime)
        if len(heap) < limit:
            heapq.heappush(heap, item)
        else:
            heapq.heappushpop(heap, item)
    items = [{"path": p, "name": os.path.basename(p), "folder": os.path.dirname(p), "size": s,
              "mtime": m, "category": categorize(p)} for s, p, m in sorted(heap, reverse=True)]
    return {"items": items, "cancelled": bool(ctx and ctx.cancelled())}


# --------------------------------------------------------------------------- duplicates
_PARTIAL = 64 * 1024


def _hash_file(path, size, partial, ctx=None):
    h = hashlib.blake2b(digest_size=20)
    try:
        with open(path, "rb") as f:
            if partial:
                h.update(f.read(_PARTIAL))
                if size > 2 * _PARTIAL:
                    f.seek(-_PARTIAL, os.SEEK_END)
                    h.update(f.read(_PARTIAL))
            else:
                while True:
                    if ctx is not None and ctx.cancelled():
                        return None
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def find_duplicates(ctx, roots, min_bytes) -> dict:
    by_size = defaultdict(list)
    for path, size, mtime in walk_entries(roots, ctx, skip_system=True):
        if size >= max(1, min_bytes):
            by_size[size].append((path, mtime))
    candidates = {s: f for s, f in by_size.items() if len(f) > 1}
    total = sum(len(f) for f in candidates.values()) or 1
    done = 0
    stage2 = []
    for size, files in candidates.items():
        buckets = defaultdict(list)
        for path, mtime in files:
            if ctx is not None and ctx.cancelled():
                return {"groups": [], "cancelled": True}
            digest = _hash_file(path, size, True)
            done += 1
            if ctx is not None:
                ctx.progress(f"Comparing candidates… {done:,}/{total:,}", int(done * 40 / total))
            if digest:
                buckets[digest].append((path, mtime))
        stage2.extend((size, grp) for grp in buckets.values() if len(grp) > 1)

    total_bytes = sum(size * len(grp) for size, grp in stage2 if size > 2 * _PARTIAL) or 1
    hashed = 0
    groups = []
    for size, grp in stage2:
        if size <= 2 * _PARTIAL:  # partial hash already covered the whole file
            final = [grp]
        else:
            buckets = defaultdict(list)
            for path, mtime in grp:
                if ctx is not None and ctx.cancelled():
                    return {"groups": [], "cancelled": True}
                digest = _hash_file(path, size, False, ctx)
                hashed += size
                if ctx is not None:
                    ctx.progress(f"Verifying contents… {os.path.basename(path)}",
                                 40 + int(hashed * 60 / total_bytes))
                if digest:
                    buckets[digest].append((path, mtime))
            final = [g for g in buckets.values() if len(g) > 1]
        for g in final:
            groups.append({"size": size, "wasted": size * (len(g) - 1),
                           "files": [{"path": p, "mtime": m} for p, m in g]})
    groups.sort(key=lambda g: g["wasted"], reverse=True)
    return {"groups": groups, "cancelled": False}


# --------------------------------------------------------------------------- disk usage tree
class DirNode:
    __slots__ = ("path", "name", "size", "files", "own_size", "own_files", "children")

    def __init__(self, path: str, name: str = ""):
        self.path = path
        self.name = name or os.path.basename(path.rstrip("\\/")) or path
        self.size = 0
        self.files = 0
        self.own_size = 0
        self.own_files = 0
        self.children: list = []


def build_tree(ctx, root: str) -> dict:
    """Scan a folder into a DirNode tree plus per-category and per-extension totals."""
    cat_totals: dict = defaultdict(int)
    ext_totals: dict = defaultdict(int)
    root_node = DirNode(root, root)
    stack = [root_node]
    order = []
    last = 0.0
    scanned = 0
    while stack:
        if ctx is not None and ctx.cancelled():
            break
        node = stack.pop()
        order.append(node)
        try:
            it = os.scandir(node.path)
        except OSError:
            continue
        with it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                    if _is_link(entry, st):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        child = DirNode(entry.path, entry.name)
                        node.children.append(child)
                        stack.append(child)
                    elif entry.is_file(follow_symlinks=False):
                        attrs = getattr(st, "st_file_attributes", 0)
                        if attrs & (_OFFLINE | _RECALL_ON_DATA | _RECALL_ON_OPEN):
                            continue
                        node.own_size += st.st_size
                        node.own_files += 1
                        ext = os.path.splitext(entry.name)[1].lower() or "(none)"
                        ext_totals[ext] += st.st_size
                        cat_totals[_EXT_TO_CAT.get(ext, "Other")] += st.st_size
                        scanned += 1
                except OSError:
                    continue
        if ctx is not None:
            now = time.monotonic()
            if now - last > 0.2:
                last = now
                ctx.progress(f"{scanned:,} files measured: {node.path}")
    for node in reversed(order):  # children always come after their parent in `order`
        node.size = node.own_size + sum(c.size for c in node.children)
        node.files = node.own_files + sum(c.files for c in node.children)
        node.children.sort(key=lambda c: c.size, reverse=True)
    top_ext = sorted(ext_totals.items(), key=lambda kv: kv[1], reverse=True)[:15]
    return {"root": root_node, "categories": dict(cat_totals), "extensions": top_ext,
            "cancelled": bool(ctx and ctx.cancelled())}
