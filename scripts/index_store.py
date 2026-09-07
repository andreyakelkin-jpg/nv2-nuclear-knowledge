"""Publish immutable index generations and pin a consistent generation for readers."""
from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

GENERATED = frozenset({
    "documents.yaml", "cross-references.yaml", "materials.yaml", "addition-queue.yaml",
    "replacements.yaml", "impact-index.yaml", "training-impact.yaml", "corpus-manifest.yaml",
    "clause-index.json", "search-index.json",
})
_snapshots: contextvars.ContextVar[dict[str, Path]] = contextvars.ContextVar("index_snapshots", default={})


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _active(root: Path) -> Path:
    pointer = root / "meta" / "index-current.json"
    if not pointer.is_file():
        return root / "meta"
    generation = json.loads(pointer.read_text(encoding="utf-8"))["generation"]
    if not isinstance(generation, str) or not generation or any(c not in "0123456789abcdef" for c in generation):
        raise ValueError("Invalid index generation")
    directory = root / "meta" / "index-generations" / generation
    if not directory.is_dir():
        raise FileNotFoundError("Active index generation is missing")
    return directory


def index_path(root: Path, relative: str = "meta/documents.yaml") -> Path:
    relative_path = Path(relative)
    if relative_path.parent != Path("meta") or relative_path.name not in GENERATED:
        return root / relative_path
    directory = _snapshots.get().get(str(root.resolve())) or _active(root)
    return directory / relative_path.name


@contextlib.contextmanager
def pin_indexes(root: Path):
    key = str(root.resolve())
    if key in _snapshots.get():
        yield
        return
    token = _snapshots.set({**_snapshots.get(), key: _active(root)})
    try:
        yield
    finally:
        _snapshots.reset(token)


def index_snapshot(function: Callable) -> Callable:
    @functools.wraps(function)
    def wrapped(root: Path, *args, **kwargs):
        with pin_indexes(root):
            return function(root, *args, **kwargs)
    return wrapped


def publish_indexes(root: Path, build: Callable[[Path], Any]) -> Any:
    """Build off-line, publish one pointer last. Keep old generation for in-flight readers."""
    generations = root / "meta" / "index-generations"
    generations.mkdir(parents=True, exist_ok=True)
    generation = uuid.uuid4().hex
    directory = generations / generation
    directory.mkdir()
    try:
        result = build(directory)
        if not (directory / "documents.yaml").is_file() or not (directory / "corpus-manifest.yaml").is_file():
            raise ValueError("Incomplete index generation")
        # Compatibility mirrors for users/tools reading the historical paths.
        # Plugin readers use the pointer and never observe partial publication.
        for name in GENERATED:
            source = directory / name
            if source.is_file():
                atomic_write(root / "meta" / name, source.read_bytes())
        atomic_write(root / "meta" / "index-current.json", json.dumps({"generation": generation}).encode("utf-8"))
        return result
    except Exception:
        # Only this unpublished, validated descendant belongs to this operation.
        if directory.parent == generations and directory.name == generation:
            shutil.rmtree(directory)
        raise
