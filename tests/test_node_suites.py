"""Run the optional JavaScript suites from unittest when Node.js is present.

The panel-state check (tests/test_panel_state.mjs) and the terminal viewer's suites use
Node's built-in test runner and nothing else. Without `node` they are skipped, so
`python3 -m unittest discover -s tests` still runs everywhere the Python package does.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = [ROOT / "tests" / "test_panel_state.mjs",
          *sorted((ROOT / "integrations" / "terminal-viewer" / "tests").glob("*.test.mjs"))]


@unittest.skipUnless(shutil.which("node"), "node is not installed; the JavaScript suites are optional")
class NodeSuiteTests(unittest.TestCase):
    def test_javascript_suites_pass(self):
        run = subprocess.run(["node", "--test", *map(str, SUITES)], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=180)
        self.assertEqual(run.returncode, 0, "node --test failed:\n" + run.stdout[-4000:] + run.stderr[-4000:])
