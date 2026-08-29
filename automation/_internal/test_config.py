#!/usr/bin/env python3
"""
test_config.py — config.py 的兩種缺檔反應各自照約定走。

WHY THIS EXISTS
這支模組存在的理由就是「缺設定時的行為」以前有三種版本各自散在五個檔案裡。把它
收成一份之後,那個約定必須有人守著,否則下次有人覺得 get() 應該要炸,就又回去了。

    python3 test_config.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import config  # noqa: E402


class LoadTest(unittest.TestCase):
    def test_missing_file_loads_as_empty(self):
        self.assertEqual(config.load(os.path.join(tempfile.gettempdir(), "nope.json")), {})

    def test_malformed_json_raises_rather_than_reading_as_empty(self):
        """打錯字跟沒設定不是同一件事 —— 當成空的會讓錯誤延後好幾步才爆。"""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{not json")
            path = fh.name
        self.addCleanup(os.unlink, path)
        with self.assertRaises(ValueError):
            config.load(path)

    def test_present_file_reads_back(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"dashboard_port": 9001}, fh)
            path = fh.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(config.load(path)["dashboard_port"], 9001)


class PolicyTest(unittest.TestCase):
    """get 與 require 在同一份缺檔的情況下必須分岔,不然收攏就沒有意義。"""

    SNIPPET = (
        "import sys; sys.path.insert(0, %r);"
        "import config;"
        "config.CONFIG_PATH = %r;"
        "config._cache = None;"
        "%s"
    )

    def run_with_no_config(self, tail):
        missing = os.path.join(tempfile.gettempdir(), "definitely-not-here.json")
        return subprocess.run(
            [sys.executable, "-c", self.SNIPPET % (BASE_DIR, missing, tail)],
            capture_output=True, text=True)

    def test_get_falls_back_and_exits_clean(self):
        proc = self.run_with_no_config("print(config.get('dashboard_port', 8765))")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "8765")

    def test_require_exits_nonzero_and_names_the_setup_doc(self):
        proc = self.run_with_no_config("config.require('gmail_address')")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("docs/SETUP.md", proc.stdout + proc.stderr)

    def test_require_also_rejects_a_present_file_missing_the_key(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"dashboard_port": 8765}, fh)
            path = fh.name
        self.addCleanup(os.unlink, path)
        proc = subprocess.run(
            [sys.executable, "-c", self.SNIPPET % (BASE_DIR, path, "config.require('gmail_address')")],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("gmail_address", proc.stdout + proc.stderr)


class RuntimeDirTest(unittest.TestCase):
    def test_runtime_dir_is_outside_the_repo(self):
        """這個 repo 是公開的。執行期狀態掉進去過一次就是永久紀錄。"""
        self.assertFalse(os.path.abspath(config.RUNTIME_DIR).startswith(
            os.path.abspath(config.REPO_ROOT) + os.sep))

    def test_every_runtime_writer_agrees_on_the_directory(self):
        """runlock / server / apply_batch 以前各寫一次字面值。"""
        import runlock
        self.assertEqual(os.path.dirname(runlock.LOCK_PATH), config.RUNTIME_DIR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
