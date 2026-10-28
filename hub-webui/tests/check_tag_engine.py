"""Shared tag-index regressions: temporary CSVs only, no models or network."""
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/painter/core"))
import tag_engine as tags


class TagEngineTests(unittest.TestCase):
    def setUp(self):
        temporary_root = ROOT / ".cache" / "tag-tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.csv_dir = patch.object(tags, "_CSV_DIR", self.directory)
        self.csv_dir.start()
        self.addCleanup(self.csv_dir.stop)
        tags._engines.clear()
        tags._csv_locks.clear()

    def write(self, name, text):
        path = self.directory / f"{name}.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_rejects_traversal_absolute_names_and_windows_devices(self):
        for name in ("", "../secret", "..\\secret", "C:\\secret", "/secret", "a/b", "a:b",
                     "name.", "name ", "CON", "nul.csv", "COM1", "LPT9"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                tags.csv_path_for(name)
        self.assertEqual(tags.csv_path_for("merged_2024-12-22.test").parent, self.directory)

    def test_header_bom_and_first_data_row_are_retained(self):
        self.write("bom", "\ufefftag,category,post_count,aliases\nfirst_tag,0,10,\nsecond_tag,4,5,\n")
        self.assertEqual(tags.ensure_loaded("bom"), 2)
        self.assertEqual(tags.search("bom", "first")[0]["name"], "first_tag")

    def test_concurrent_first_queries_build_one_shared_index(self):
        self.write("shared", "blue_hair,0,10,\n")
        start = threading.Barrier(8)

        def query():
            start.wait(timeout=5)
            return tags.search("shared", "blue")

        with patch.object(tags, "prepare_csv", wraps=tags.prepare_csv) as prepare:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: query(), range(8)))
        self.assertEqual(prepare.call_count, 1)
        self.assertTrue(all(result[0]["name"] == "blue_hair" for result in results))
        self.assertEqual(tags.loaded_names(), ["shared"])

    def test_names_and_aliases_use_the_same_unicode_normalization(self):
        self.write("unicode", "shelly_bélier,4,71,\ncoffee_shop,0,3,cafétéria\n黒髪,0,2,\n")
        for query in ("shelly belier", "shelly_bélier", "SHELLY   BÉLIER"):
            with self.subTest(query=query):
                self.assertEqual(tags.search("unicode", query)[0]["name"], "shelly_bélier")
        result = tags.search("unicode", "cafeteria")[0]
        self.assertEqual(result["name"], "coffee_shop")
        self.assertEqual(result["matched_alias"], "cafétéria")
        self.assertEqual(tags.search("unicode", "黒髪")[0]["name"], "黒髪")

    def test_token_order_ranking_and_ties_are_stable(self):
        self.write("order", "blue_zeta,0,5,\nblue_alpha,0,5,\nsakura_futaba_(persona_5),4,2,\n")
        expected = ["blue_alpha", "blue_zeta"]
        self.assertEqual([result["name"] for result in tags.search("order", "blue")], expected)
        self.assertEqual(tags.search("order", "futaba sakura")[0]["name"], "sakura_futaba_(persona_5)")

    def test_all_limits_remain_between_one_and_twenty(self):
        self.write("limits", "".join(f"blue_{i},0,10,\n" for i in range(30)))
        for limit, expected in ((-5, 1), (0, 1), (1, 1), (8, 8), (500, 20)):
            with self.subTest(limit=limit):
                self.assertEqual(len(tags.search("limits", "blue", limit)), expected)

    def test_missing_or_invalid_reload_retains_previous_index(self):
        path = self.write("reload", "old_tag,0,10,\n")
        tags.load_csv("reload")
        previous = tags._engines["reload"]
        path.write_text("<html>not a tag library</html>", encoding="utf-8")
        with self.assertRaises(ValueError):
            tags.load_csv("reload")
        self.assertIs(tags._engines["reload"], previous)
        path.unlink()
        self.assertEqual(tags.load_csv("reload"), 0)
        self.assertIs(tags._engines["reload"], previous)

    def test_staging_only_publishes_after_successful_atomic_install(self):
        path = self.write("install", "old_tag,0,10,\n")
        tags.load_csv("install")
        previous = tags._engines["install"]
        staged = self.directory / ".staged.tmp"
        staged.write_text("new_tag,4,12,\n", encoding="utf-8")
        prepared = tags.prepare_csv("install", staged)
        self.assertIs(tags._engines["install"], previous)
        self.assertIn("old_tag", path.read_text())
        with patch.object(tags.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                tags.install_csv("install", staged, prepared)
        self.assertIs(tags._engines["install"], previous)
        self.assertTrue(staged.exists())
        self.assertIn("old_tag", path.read_text())
        self.assertEqual(tags.install_csv("install", staged, prepared), 1)
        self.assertFalse(staged.exists())
        self.assertIs(tags._engines["install"], prepared)
        self.assertEqual(tags.search("install", "new")[0]["name"], "new_tag")

    def test_search_uses_last_good_index_while_reload_is_preparing(self):
        path = self.write("live", "old_tag,0,10,\n")
        tags.load_csv("live")
        path.write_text("new_tag,4,12,\n", encoding="utf-8")
        entered, release = threading.Event(), threading.Event()
        prepare = tags.prepare_csv

        def blocked(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test reload was not released")
            return prepare(*args)

        with patch.object(tags, "prepare_csv", side_effect=blocked):
            with ThreadPoolExecutor(max_workers=2) as pool:
                reloading = pool.submit(tags.load_csv, "live")
                try:
                    self.assertTrue(entered.wait(5))
                    result = pool.submit(tags.search, "live", "old").result(timeout=2)
                    self.assertEqual(result[0]["name"], "old_tag")
                finally:
                    release.set()
                self.assertEqual(reloading.result(timeout=5), 1)
        self.assertEqual(tags.search("live", "new")[0]["name"], "new_tag")


if __name__ == "__main__":
    unittest.main()
