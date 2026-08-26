import ast
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QSpinBox,
)

import apppath
from services.settings import create_settings_manager
from ui.dialog_settings import SettingsDialog
from ui.main_window import MainWindow
from ui.settings_page_color_preset_manager import ColorPresetManagerWidget


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apppath.app_path = Path(__file__).resolve().parents[1] / "src"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary_directory.name) / "settings.toml"
        self.manager = create_settings_manager(self.config_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_loads_saved_values_and_switches_categories(self):
        self.manager.set("thumbnail_memory_cache_size", 1500)
        self.manager.set("default_icon_size", 140)
        self.manager.set("recent_search_limit", 8)
        self.manager.set("tag_suggestion_limit", 12)
        self.manager.set("similar_image_target_drop_ratio", 0.42)
        self.manager.set("similar_image_min_keep", 7)
        self.manager.set("similar_image_min_similarity", 0.63)
        self.manager.set("similar_image_max_results", 60)
        self.manager.set("similar_image_candidate_count", 300)
        self.manager.save()

        dialog = SettingsDialog(config_manager=self.manager)

        self.assertEqual(3, dialog.listWidget.count())
        self.assertEqual("常规", dialog.listWidget.item(0).text())
        self.assertEqual("搜索", dialog.listWidget.item(1).text())
        self.assertEqual("颜色预设", dialog.listWidget.item(2).text())
        self.assertEqual(0, dialog.stackedWidget.currentIndex())

        self.assertEqual(
            1500, dialog.field_widget("thumbnail_memory_cache_size").value()
        )
        self.assertEqual(140, dialog.field_widget("default_icon_size").value())
        self.assertEqual(8, dialog.field_widget("recent_search_limit").value())
        self.assertEqual(
            12, dialog.field_widget("tag_suggestion_limit").value()
        )
        self.assertEqual(
            0.42,
            dialog.field_widget("similar_image_target_drop_ratio").value(),
        )
        self.assertEqual(7, dialog.field_widget("similar_image_min_keep").value())
        self.assertEqual(
            0.63,
            dialog.field_widget("similar_image_min_similarity").value(),
        )
        self.assertEqual(
            60, dialog.field_widget("similar_image_max_results").value()
        )
        self.assertEqual(
            300, dialog.field_widget("similar_image_candidate_count").value()
        )

        dialog.listWidget.setCurrentRow(1)
        self.assertEqual(1, dialog.stackedWidget.currentIndex())
        dialog.listWidget.setCurrentRow(2)
        self.assertEqual(2, dialog.stackedWidget.currentIndex())
        dialog.listWidget.setCurrentRow(0)
        self.assertEqual(0, dialog.stackedWidget.currentIndex())

    def test_schema_contains_only_active_settings_and_internal_history(self):
        self.assertEqual(
            [
                "library_base_path",
                "thumbnail_memory_cache_size",
                "default_icon_size",
                "recent_search_limit",
                "tag_suggestion_limit",
                "recent_searches",
                "similar_image_target_drop_ratio",
                "similar_image_min_keep",
                "similar_image_min_similarity",
                "similar_image_max_results",
                "similar_image_candidate_count",
                "color_presets",
            ],
            self.manager.schema.keys,
        )

    def test_visible_fields_are_assembled_from_schema(self):
        dialog = SettingsDialog(config_manager=self.manager)

        visible_keys = [
            "thumbnail_memory_cache_size",
            "default_icon_size",
            "recent_search_limit",
            "tag_suggestion_limit",
            "similar_image_target_drop_ratio",
            "similar_image_min_keep",
            "similar_image_min_similarity",
            "similar_image_max_results",
            "similar_image_candidate_count",
        ]
        for key in visible_keys:
            widget = dialog.field_widget(key)
            self.assertEqual(f"field_{key}", widget.objectName())

        for hidden_key in ("library_base_path", "recent_searches", "color_presets"):
            with self.subTest(hidden_key=hidden_key):
                with self.assertRaises(KeyError):
                    dialog.field_widget(hidden_key)

        thumbnail = dialog.field_widget("thumbnail_memory_cache_size")
        self.assertIsInstance(thumbnail, QSpinBox)
        self.assertEqual(100, thumbnail.minimum())
        self.assertEqual(100000, thumbnail.maximum())
        self.assertEqual(100, thumbnail.singleStep())
        self.assertEqual(" 张", thumbnail.suffix())

        default_icon_size = dialog.field_widget("default_icon_size")
        self.assertIsInstance(default_icon_size, QSpinBox)
        self.assertEqual(48, default_icon_size.minimum())
        self.assertEqual(256, default_icon_size.maximum())
        self.assertEqual(120, default_icon_size.value())
        self.assertEqual("", default_icon_size.suffix())

        drop_ratio = dialog.field_widget("similar_image_target_drop_ratio")
        self.assertIsInstance(drop_ratio, QDoubleSpinBox)
        self.assertEqual(2, drop_ratio.decimals())
        self.assertEqual(0.01, drop_ratio.minimum())
        self.assertEqual(0.99, drop_ratio.maximum())
        self.assertEqual(0.05, drop_ratio.singleStep())

        min_similarity = dialog.field_widget("similar_image_min_similarity")
        self.assertIsInstance(min_similarity, QDoubleSpinBox)
        self.assertEqual(0.0, min_similarity.minimum())
        self.assertEqual(1.0, min_similarity.maximum())

        candidate_count = dialog.field_widget("similar_image_candidate_count")
        self.assertEqual(1, candidate_count.minimum())
        self.assertEqual(10000, candidate_count.maximum())
        self.assertEqual(50, candidate_count.singleStep())
        self.assertEqual(" 张", candidate_count.suffix())

        max_results = dialog.field_widget("similar_image_max_results")
        self.assertEqual(1, max_results.minimum())
        self.assertEqual(200, max_results.maximum())

    def test_tooltips_come_from_schema_comments(self):
        dialog = SettingsDialog(config_manager=self.manager)

        self.assertEqual(
            "搜索框中最多显示的最近搜索数量；设为 0 可关闭。",
            dialog.field_widget("recent_search_limit").toolTip(),
        )
        self.assertEqual(
            "相似图片：最低相似度阈值（0-1之间），低于该相似度的候选不会进入结果。",
            dialog.field_widget("similar_image_min_similarity").toolTip(),
        )

    def test_pages_and_groups_follow_schema_order(self):
        dialog = SettingsDialog(config_manager=self.manager)

        general_page = dialog.stackedWidget.widget(0)
        general_groups = general_page.findChildren(QGroupBox)
        self.assertEqual(
            ["缩略图缓存", "视图"],
            [group.title() for group in general_groups],
        )
        general_labels = [
            label.text() for label in general_groups[0].findChildren(QLabel)
        ]
        self.assertEqual(["缩略图内存缓存大小"], general_labels)
        view_labels = [
            label.text() for label in general_groups[1].findChildren(QLabel)
        ]
        self.assertEqual(["图标默认大小"], view_labels)

        search_page = dialog.stackedWidget.widget(1)
        search_groups = search_page.findChildren(QGroupBox)
        self.assertEqual(
            ["搜索候选", "相似图片"],
            [group.title() for group in search_groups],
        )
        suggestion_labels = [
            label.text()
            for label in search_groups[0].findChildren(QLabel)
        ]
        self.assertEqual(["最近搜索候选", "标签搜索候选"], suggestion_labels)
        similar_labels = [
            label.text()
            for label in search_groups[1].findChildren(QLabel)
        ]
        self.assertEqual(
            [
                "累计下降比例",
                "最少保留数",
                "最低相似度",
                "最多返回数",
                "候选总数",
            ],
            similar_labels,
        )

        custom_page = dialog.stackedWidget.widget(2)
        preset_managers = custom_page.findChildren(ColorPresetManagerWidget)
        self.assertIn(dialog.colorPresetManager, preset_managers)

    def test_font_hierarchy_heading_group_and_rows(self):
        dialog = SettingsDialog(config_manager=self.manager)

        general_page = dialog.stackedWidget.widget(0)
        heading = general_page.findChildren(QLabel)[0]
        self.assertTrue(heading.font().bold())
        self.assertEqual(13, heading.font().pointSize())

        group = general_page.findChildren(QGroupBox)[0]
        self.assertTrue(group.font().bold())
        self.assertEqual(11, group.font().pointSize())

        row_labels = group.findChildren(QLabel)
        self.assertTrue(row_labels)
        for label in row_labels:
            self.assertFalse(label.font().bold())
            self.assertEqual(
                dialog.font().pointSize(), label.font().pointSize()
            )

        spin_box = group.findChildren(QSpinBox)[0]
        self.assertFalse(spin_box.font().bold())
        self.assertEqual(
            dialog.font().pointSize(), spin_box.font().pointSize()
        )

    def test_apply_saves_values_without_closing_dialog(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.show()
        dialog.field_widget("thumbnail_memory_cache_size").setValue(1500)
        dialog.field_widget("default_icon_size").setValue(140)
        dialog.field_widget("recent_search_limit").setValue(24)
        dialog.field_widget("tag_suggestion_limit").setValue(7)
        dialog.field_widget("similar_image_target_drop_ratio").setValue(0.33)
        dialog.field_widget("similar_image_min_keep").setValue(4)
        dialog.field_widget("similar_image_min_similarity").setValue(0.71)
        dialog.field_widget("similar_image_max_results").setValue(40)
        dialog.field_widget("similar_image_candidate_count").setValue(250)

        apply_button = dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Apply
        )
        self.assertTrue(apply_button.isEnabled())
        apply_button.click()

        saved_manager = create_settings_manager(self.config_path)
        self.assertEqual(
            1500, saved_manager.get("thumbnail_memory_cache_size")
        )
        self.assertEqual(140, saved_manager.get("default_icon_size"))
        self.assertEqual(24, saved_manager.get("recent_search_limit"))
        self.assertEqual(7, saved_manager.get("tag_suggestion_limit"))
        self.assertEqual(
            0.33,
            saved_manager.get("similar_image_target_drop_ratio"),
        )
        self.assertEqual(4, saved_manager.get("similar_image_min_keep"))
        self.assertEqual(
            0.71,
            saved_manager.get("similar_image_min_similarity"),
        )
        self.assertEqual(40, saved_manager.get("similar_image_max_results"))
        self.assertEqual(
            250, saved_manager.get("similar_image_candidate_count")
        )
        self.assertFalse(apply_button.isEnabled())
        self.assertTrue(dialog.isVisible())
        dialog.close()

    def test_cancel_discards_unapplied_changes(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.field_widget("recent_search_limit").setValue(20)

        dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Cancel
        ).click()

        saved_manager = create_settings_manager(self.config_path)
        self.assertEqual(3, saved_manager.get("recent_search_limit"))
        self.assertEqual(QDialog.DialogCode.Rejected, dialog.result())

    def test_ok_saves_changes_and_accepts_dialog(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.field_widget("tag_suggestion_limit").setValue(18)

        dialog.buttonBox.button(
            QDialogButtonBox.StandardButton.Ok
        ).click()

        saved_manager = create_settings_manager(self.config_path)
        self.assertEqual(18, saved_manager.get("tag_suggestion_limit"))
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())

    def test_save_failure_keeps_dialog_open(self):
        dialog = SettingsDialog(config_manager=self.manager)
        dialog.show()
        dialog.field_widget("recent_search_limit").setValue(20)

        with patch.object(
            self.manager,
            "save",
            side_effect=OSError("磁盘不可写"),
        ), patch(
            "ui.dialog_settings.QMessageBox.critical"
        ) as critical, patch("ui.dialog_settings.logger.exception"):
            dialog.buttonBox.button(
                QDialogButtonBox.StandardButton.Ok
            ).click()

        self.assertTrue(dialog.isVisible())
        self.assertEqual(3, self.manager.get("recent_search_limit"))
        critical.assert_called_once_with(dialog, "保存设置失败", "磁盘不可写")
        dialog.close()


class MainWindowSettingsTests(unittest.TestCase):
    def test_open_settings_runs_modal_dialog(self):
        settings_manager = object()
        refresh_suggestions = Mock()
        window = SimpleNamespace(
            _settings_manager=settings_manager,
            customSearchBox=SimpleNamespace(
                refresh_suggestions=refresh_suggestions,
            ),
        )
        with patch("ui.main_window.SettingsDialog") as dialog_class:
            MainWindow.open_settings(window)

        dialog_class.assert_called_once_with(
            window,
            config_manager=settings_manager,
        )
        dialog_class.return_value.exec.assert_called_once_with()
        refresh_suggestions.assert_called_once_with()


class SettingsServiceTests(unittest.TestCase):
    def test_settings_service_imports_no_qt_or_ui(self):
        settings_path = (
            Path(__file__).resolve().parents[1]
            / "src" / "services" / "settings.py"
        )
        tree = ast.parse(settings_path.read_text(encoding="utf-8"))

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)

        self.assertTrue({"apppath", "config_manager"}.issubset(imported))
        self.assertFalse(
            any(
                name == "PyQt6"
                or name.startswith("PyQt6.")
                or name == "ui"
                or name.startswith("ui.")
                for name in imported
            )
        )


if __name__ == "__main__":
    unittest.main()
