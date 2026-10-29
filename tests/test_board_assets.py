"""The dashboard script only uses elements the page really contains, and the operator panel stays out of a viewer's reach."""

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "astra" / "static"
TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "astra" / "templates"


def ids_used(script: str) -> set[str]:
    pattern = r"""(?:\$|fill|getElementById)\(\s*["']([a-z0-9-]+)["']"""
    return set(re.findall(pattern, script))


class BoardAssetTests(unittest.TestCase):
    def setUp(self):
        self.script = (STATIC / "board.js").read_text(encoding="utf-8")
        self.page = (TEMPLATES / "index.html").read_text(encoding="utf-8")
        self.ids = set(re.findall(r'id="([a-z0-9-]+)"', self.page))

    def test_every_element_the_script_uses_exists_in_the_page(self):
        missing = sorted(i for i in ids_used(self.script) if i.startswith("board-") and i not in self.ids)
        self.assertEqual(missing, [])
        self.assertGreater(len([i for i in ids_used(self.script) if i.startswith("board-")]), 20)

    def test_the_script_is_loaded_after_lab_js_which_provides_its_helpers(self):
        self.assertLess(self.page.index("/static/lab.js"), self.page.index("/static/board.js"))

    def test_the_actions_panel_is_operator_only_and_hidden_until_the_script_allows_it(self):
        panel = re.search(r'<section class="([^"]*)" id="board-actions"([^>]*)>', self.page)
        self.assertIsNotNone(panel)
        self.assertIn("operator-only", panel.group(1))
        self.assertIn("hidden", panel.group(2))
        self.assertIn("if (!section || !CAN_ACT) return;", self.script)

    def test_the_script_never_calls_the_broker_or_an_order_route_directly(self):
        routes = set(re.findall(r'["`](/api/[a-z0-9/_${}.-]+)', self.script))
        post_routes = set(re.findall(r'post\(\s*["`](/api/[^"`]+)', self.script))
        self.assertEqual(post_routes, {"/api/mt5/requests"})  # the only write: a request the bridge will judge
        self.assertTrue(all(r.startswith("/api/") for r in routes))
        for forbidden in ("order_send", "submit_order", "/api/orders", "kill", "release"):
            self.assertNotIn(forbidden, "".join(post_routes))

    def test_an_order_request_has_no_volume_field(self):
        sends = re.findall(r'sendRequest\(\{ kind: "order"[^}]*\}', self.script)
        self.assertEqual(len(sends), 1)
        self.assertNotRegex(sends[0], r"lots|volume")


if __name__ == "__main__":
    unittest.main()
