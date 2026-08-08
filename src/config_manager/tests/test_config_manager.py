from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tomlkit

from src.config_manager import (
    ConfigField,
    ConfigManager,
    ConfigManagerError,
    ConfigMigrationError,
    ConfigNotFoundError,
    ConfigSchema,
    ConfigType,
    ConfigTypeError,
    ConfigValidationError,
)


def make_schema() -> list[ConfigField]:
    return [
        ConfigField("name", ConfigType.STRING, "default", "Display name"),
        ConfigField("count", ConfigType.INT, 1),
        ConfigField("enabled", ConfigType.BOOL, True),
        ConfigField("tags", ConfigType.LIST_STR, ["initial"]),
        ConfigField("sizes", ConfigType.LIST_INT, [16]),
    ]


class ConfigFieldTests(unittest.TestCase):
    def test_default_must_pass_runtime_validation(self) -> None:
        invalid_fields = [
            (ConfigType.INT, True),
            (ConfigType.LIST_STR, [1]),
            (ConfigType.LIST_INT, [True]),
        ]

        for config_type, default in invalid_fields:
            with self.subTest(config_type=config_type, default=default):
                with self.assertRaises(TypeError):
                    ConfigField("value", config_type, default)

    def test_schema_rejects_duplicate_keys(self) -> None:
        fields = [
            ConfigField("same", ConfigType.STRING, "first"),
            ConfigField("same", ConfigType.STRING, "second"),
        ]

        with self.assertRaises(ValueError):
            ConfigSchema(fields)

    def test_schema_rejects_non_fields(self) -> None:
        with self.assertRaises(TypeError):
            ConfigSchema(["not-a-field"])

    def test_default_values_are_copied(self) -> None:
        field = ConfigField("items", ConfigType.LIST_INT, [1])

        returned = field.get_default()
        returned.append(2)

        self.assertEqual([1], field.get_default())


class ConfigManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.config_path = Path(self._temporary_directory.name) / "settings.toml"

    def create_manager(self, version: str = "1.0.0") -> ConfigManager:
        return ConfigManager(self.config_path, make_schema(), version)

    def read_document(self):
        return tomlkit.loads(self.config_path.read_text(encoding="utf-8"))

    def test_create_set_save_and_reload(self) -> None:
        manager = self.create_manager()

        manager.set("name", "changed")
        manager.set("sizes", [32, 64])
        manager.save()
        manager.reload()

        self.assertEqual("changed", manager.get("name"))
        self.assertEqual([32, 64], manager.get("sizes"))
        self.assertTrue(manager.validate())

    def test_mutable_values_do_not_leak_across_api_boundaries(self) -> None:
        manager = self.create_manager()
        supplied = [32]

        manager.set("sizes", supplied)
        supplied.append(64)
        manager.get("sizes").append(128)
        all_values = manager.get_all()
        all_values["sizes"].append(256)
        manager.save()
        manager.reload()

        self.assertEqual([32], manager.get("sizes"))

    def test_invalid_and_missing_values_are_repaired_before_migration_save(self) -> None:
        self.config_path.write_text(
            '__version__ = "0.9.0"\n[config]\nname = 42\nextra = "keep"\n',
            encoding="utf-8",
        )

        manager = self.create_manager()
        document = self.read_document()

        self.assertEqual("default", manager.get("name"))
        self.assertEqual("1.0.0", document["__version__"])
        self.assertEqual("default", document["config"]["name"])
        self.assertEqual([16], document["config"]["sizes"])
        self.assertEqual("keep", document["config"]["extra"])

    def test_missing_values_are_persisted_without_version_change(self) -> None:
        self.config_path.write_text(
            '__version__ = "1.0.0"\n[config]\nname = "configured"\n',
            encoding="utf-8",
        )

        self.create_manager()
        document = self.read_document()

        self.assertEqual(1, document["config"]["count"])
        self.assertEqual(["initial"], document["config"]["tags"])

    def test_future_version_is_rejected_without_modifying_file(self) -> None:
        original = (
            '__version__ = "2.0.0"\n'
            '[config]\nname = "future"\ncount = 1\nenabled = true\n'
            'tags = []\nsizes = []\n'
        )
        self.config_path.write_text(original, encoding="utf-8")

        with self.assertRaises(ConfigMigrationError):
            self.create_manager()

        self.assertEqual(original, self.config_path.read_text(encoding="utf-8"))

    def test_equivalent_numeric_versions_are_normalized(self) -> None:
        self.config_path.write_text(
            '__version__ = "1.0"\n[config]\n',
            encoding="utf-8",
        )

        self.create_manager("1.0.0")

        self.assertEqual("1.0.0", self.read_document()["__version__"])

    def test_unordered_version_change_is_rejected(self) -> None:
        self.config_path.write_text(
            '__version__ = "release-a"\n[config]\n',
            encoding="utf-8",
        )

        with self.assertRaises(ConfigMigrationError):
            self.create_manager("release-b")

    def test_invalid_config_section_raises_validation_error(self) -> None:
        self.config_path.write_text(
            '__version__ = "1.0.0"\nconfig = 1\n',
            encoding="utf-8",
        )

        with self.assertRaises(ConfigValidationError):
            self.create_manager()

    def test_reload_failure_preserves_current_in_memory_values(self) -> None:
        manager = self.create_manager()
        manager.set("name", "in-memory")
        manager.save()
        self.config_path.write_text(
            '__version__ = "2.0.0"\n[config]\nname = "future"\n',
            encoding="utf-8",
        )

        with self.assertRaises(ConfigMigrationError):
            manager.reload()

        self.assertEqual("in-memory", manager.get("name"))

    def test_atomic_save_failure_preserves_existing_file(self) -> None:
        manager = self.create_manager()
        original = self.config_path.read_text(encoding="utf-8")
        manager.set("name", "not-written")

        with patch(
            "src.config_manager.config_manager.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaises(ConfigManagerError):
                manager.save()

        self.assertEqual(original, self.config_path.read_text(encoding="utf-8"))
        temporary_files = list(
            self.config_path.parent.glob(f".{self.config_path.name}.*.tmp")
        )
        self.assertEqual([], temporary_files)

    def test_missing_file_can_be_rejected(self) -> None:
        with self.assertRaises(ConfigNotFoundError):
            ConfigManager(
                self.config_path,
                make_schema(),
                "1.0.0",
                create_if_not_exists=False,
            )

    def test_version_must_be_a_non_empty_string(self) -> None:
        with self.assertRaises(ValueError):
            ConfigManager(self.config_path, make_schema(), "   ")

    def test_set_raises_public_type_error(self) -> None:
        manager = self.create_manager()

        with self.assertRaises(ConfigTypeError):
            manager.set("count", True)


if __name__ == "__main__":
    unittest.main()
