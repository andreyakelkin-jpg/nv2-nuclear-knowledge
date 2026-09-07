from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
from index_store import index_path, pin_indexes, publish_indexes  # noqa: E402


class IndexPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "meta").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def build(version: str):
        def write(target: Path) -> str:
            (target / "documents.yaml").write_text(f"version: {version}\n", encoding="utf-8")
            (target / "corpus-manifest.yaml").write_text(f"version: {version}\n", encoding="utf-8")
            return version
        return write

    def test_pinned_reader_keeps_one_generation_during_publication(self) -> None:
        publish_indexes(self.root, self.build("one"))
        with pin_indexes(self.root):
            pinned = index_path(self.root)
            publish_indexes(self.root, self.build("two"))
            self.assertEqual("version: one\n", pinned.read_text(encoding="utf-8"))
            self.assertEqual(pinned, index_path(self.root))
        self.assertEqual("version: two\n", index_path(self.root).read_text(encoding="utf-8"))

    def test_failed_build_preserves_active_generation_and_cleans_unpublished_directory(self) -> None:
        publish_indexes(self.root, self.build("one"))
        before_pointer = (self.root / "meta/index-current.json").read_bytes()
        before_generations = {path.name for path in (self.root / "meta/index-generations").iterdir()}

        def broken(target: Path) -> None:
            (target / "documents.yaml").write_text("version: broken\n", encoding="utf-8")
            raise RuntimeError("stop before publish")

        with self.assertRaisesRegex(RuntimeError, "stop before publish"):
            publish_indexes(self.root, broken)
        self.assertEqual(before_pointer, (self.root / "meta/index-current.json").read_bytes())
        self.assertEqual(before_generations, {path.name for path in (self.root / "meta/index-generations").iterdir()})
        self.assertEqual("version: one\n", index_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(32, len(json.loads(before_pointer.decode("utf-8"))["generation"]))


if __name__ == "__main__":
    unittest.main()
