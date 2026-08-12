import unittest
from pathlib import Path
from unittest.mock import patch

import apppath
import utils.resource_path as resource_path
from utils.resource_path import resolve_resource_path


class ResourcePathTests(unittest.TestCase):
    def test_resolves_from_source_tree_when_app_path_is_unset(self):
        with patch.object(apppath, "app_path", None):
            result = resolve_resource_path("search.svg")

        self.assertEqual(
            result,
            Path(resource_path.__file__).resolve().parents[1]
            / "resources"
            / "search.svg",
        )

    def test_resolves_from_app_path_when_configured(self):
        app_root = Path(__file__).resolve().parents[1] / "fake-app"

        with patch.object(apppath, "app_path", app_root):
            result = resolve_resource_path(Path("icons") / "search.svg")

        self.assertEqual(result, app_root / "resources" / "icons" / "search.svg")


if __name__ == "__main__":
    unittest.main()
