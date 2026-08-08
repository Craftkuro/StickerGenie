import ast
import unittest
from pathlib import Path


class MainSpawnEntryTests(unittest.TestCase):
    def test_startup_calls_are_guarded_by_main_function(self):
        main_path = Path(__file__).resolve().parents[1] / "src" / "main.py"
        source = main_path.read_text(encoding="utf-8")
        module = ast.parse(source)

        top_level_imports = [
            node
            for node in module.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        imported_names = {
            alias.name
            for node in top_level_imports
            for alias in node.names
        }
        self.assertEqual({"annotations", "multiprocessing"}, imported_names)

        main_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        calls = [
            node
            for node in ast.walk(main_function)
            if isinstance(node, ast.Call)
        ]
        self.assertTrue(
            any(
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "run_startup_tasks"
                for call in calls
            )
        )
        self.assertIn("freeze_support", source)


if __name__ == "__main__":
    unittest.main()
