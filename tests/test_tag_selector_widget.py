import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from commons.dto import Tag
from stickerdb.v1.sticker_db import StickerDBV1
from ui.dialog_tag_selector import (
    TAG_ACCENT_COLOR_ROLE,
    TAG_DATA_ROLE,
    TAG_ID_ROLE,
    SubstringTagSearchMatcher,
    TagSelectorWidget,
)


def make_tag(
    name: str,
    *,
    enabled: bool = True,
    color: str = "#2196F3",
    order: int = 0,
) -> Tag:
    tag = Tag()
    tag.name = name
    tag.enabled = enabled
    tag.color_rgb = color
    tag.order = order
    return tag


class TagSelectorWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._temp_dir = TemporaryDirectory()
        self.db = StickerDBV1(str(Path(self._temp_dir.name) / "library.db"))
        self.beta = self.add_tag("Beta", order=2)
        self.alpha = self.add_tag("Alpha", order=1, color="#FF0000")
        self.gamma = self.add_tag("Gamma", order=1)
        self.widget = TagSelectorWidget(database=self.db)

    def tearDown(self):
        self.widget.close()
        self.db.engine.dispose()
        self._temp_dir.cleanup()

    def add_tag(
        self,
        name: str,
        *,
        enabled: bool = True,
        color: str = "#2196F3",
        order: int = 0,
    ) -> Tag:
        """新建标签并设置最终 order。

        新建时 StickerDBV1 会强制 order 为当前最大值 + 1，
        因此需要按 id 再写一次来应用指定的顺序。
        """
        created = self.db.add_or_modify_tag(
            make_tag(name, enabled=enabled, color=color)
        )
        created.order = order
        return self.db.add_or_modify_tag(created)

    def item_names(self, list_widget):
        return [
            list_widget.item(row).text()
            for row in range(list_widget.count())
        ]

    def select_available(self, *rows):
        for row in rows:
            self.widget.available_list_widget.item(row).setSelected(True)

    def test_loads_all_tags_sorted_by_order_then_id(self):
        self.assertEqual(
            ["Alpha", "Gamma", "Beta"],
            self.item_names(self.widget.available_list_widget),
        )
        self.assertEqual(
            [],
            self.item_names(self.widget.selected_list_widget),
        )

    def test_items_carry_tag_data_and_accent_color(self):
        item = self.widget.available_list_widget.item(0)
        self.assertIsInstance(item.data(TAG_DATA_ROLE), Tag)
        self.assertEqual(self.alpha.id, item.data(TAG_ID_ROLE))
        self.assertEqual("#FF0000", item.data(TAG_ACCENT_COLOR_ROLE))

    def test_loads_disabled_tags_too(self):
        self.add_tag("Zebra", enabled=False, order=0)
        self.widget.reload_tags()
        self.assertIn(
            "Zebra",
            self.item_names(self.widget.available_list_widget),
        )

    def test_search_filters_available_list_by_substring_case_insensitive(self):
        self.widget.search_box.setText("alp")
        self.assertFalse(self.widget.available_list_widget.item(0).isHidden())
        self.assertTrue(self.widget.available_list_widget.item(1).isHidden())
        self.assertTrue(self.widget.available_list_widget.item(2).isHidden())

        self.widget.search_box.setText("ALPHA")
        self.assertFalse(self.widget.available_list_widget.item(0).isHidden())
        self.assertTrue(self.widget.available_list_widget.item(1).isHidden())

        self.widget.search_box.setText("")
        self.assertTrue(
            all(
                not self.widget.available_list_widget.item(row).isHidden()
                for row in range(self.widget.available_list_widget.count())
            )
        )

    def test_custom_matcher_replaces_default(self):
        class OnlyAlphaMatcher:
            def filter_tags(self, tags, query):
                return [tag for tag in tags if tag.name == "Alpha"]

        widget = TagSelectorWidget(
            database=self.db,
            matcher=OnlyAlphaMatcher(),
        )
        try:
            widget.search_box.setText("zzz-no-match")
            visible_names = [
                widget.available_list_widget.item(row).text()
                for row in range(widget.available_list_widget.count())
                if not widget.available_list_widget.item(row).isHidden()
            ]
            self.assertEqual(["Alpha"], visible_names)
        finally:
            widget.close()

    def test_add_button_moves_tags_to_selected_in_display_order(self):
        self.select_available(0, 2)
        self.widget.add_button.click()
        self.assertEqual(
            [self.alpha.id, self.beta.id],
            self.widget.selected_tag_ids(),
        )
        self.assertEqual(
            [self.alpha.name, self.beta.name],
            [tag.name for tag in self.widget.selected_tags()],
        )
        self.assertEqual(
            ["Alpha", "Beta"],
            self.item_names(self.widget.selected_list_widget),
        )

    def test_remove_button_moves_tags_back_to_available(self):
        self.select_available(0, 2)
        self.widget.add_button.click()
        self.widget.selected_list_widget.item(0).setSelected(True)
        self.widget.remove_button.click()
        self.assertEqual([self.beta.id], self.widget.selected_tag_ids())
        self.assertEqual(
            ["Beta"],
            self.item_names(self.widget.selected_list_widget),
        )

    def test_adding_same_tag_dedupes(self):
        self.select_available(0)
        self.widget.add_button.click()
        self.select_available(0)
        self.widget.add_button.click()
        self.assertEqual([self.alpha.id], self.widget.selected_tag_ids())
        self.assertEqual(
            ["Alpha"],
            self.item_names(self.widget.selected_list_widget),
        )

    def test_ok_button_emits_selected_ids(self):
        received = []
        self.widget.ok_clicked.connect(received.append)
        self.select_available(1)
        self.widget.add_button.click()
        self.widget.ok_button.click()
        self.assertEqual([[self.gamma.id]], received)

    def test_initial_selection_populates_selected_list(self):
        widget = TagSelectorWidget(
            database=self.db,
            selected_tag_ids=[self.alpha.id, self.beta.id],
        )
        try:
            self.assertEqual(
                ["Alpha", "Beta"],
                self.item_names(widget.selected_list_widget),
            )
            self.assertEqual(
                [self.alpha.id, self.beta.id],
                widget.selected_tag_ids(),
            )
            # 已选条目仍保留在左侧列表中，不做隐藏
            self.assertEqual(
                ["Alpha", "Gamma", "Beta"],
                self.item_names(widget.available_list_widget),
            )
        finally:
            widget.close()

    def test_reload_preserves_selection_and_loads_new_tags(self):
        self.select_available(0)
        self.widget.add_button.click()
        self.add_tag("Delta", order=0)
        self.widget.reload_tags()
        self.assertEqual(
            ["Delta", "Alpha", "Gamma", "Beta"],
            self.item_names(self.widget.available_list_widget),
        )
        self.assertEqual(
            ["Alpha"],
            self.item_names(self.widget.selected_list_widget),
        )
        self.assertEqual([self.alpha.id], self.widget.selected_tag_ids())

    def test_selection_survives_search_hiding(self):
        self.select_available(2)
        self.widget.add_button.click()
        self.widget.search_box.setText("zzz-no-match")
        self.assertEqual([self.beta.id], self.widget.selected_tag_ids())
        self.assertEqual(
            ["Beta"],
            self.item_names(self.widget.selected_list_widget),
        )

    def test_set_selected_tag_ids_updates_selected_list(self):
        self.widget.set_selected_tag_ids([self.beta.id])
        self.assertEqual([self.beta.id], self.widget.selected_tag_ids())
        self.assertEqual(
            ["Beta"],
            self.item_names(self.widget.selected_list_widget),
        )
        self.assertEqual(
            ["Alpha", "Gamma", "Beta"],
            self.item_names(self.widget.available_list_widget),
        )

    def test_double_click_moves_tags_between_lists(self):
        self.widget.available_list_widget.item(1).setSelected(True)
        self.widget.available_list_widget.itemDoubleClicked.emit(
            self.widget.available_list_widget.item(1)
        )
        self.assertEqual([self.gamma.id], self.widget.selected_tag_ids())

        self.widget.selected_list_widget.item(0).setSelected(True)
        self.widget.selected_list_widget.itemDoubleClicked.emit(
            self.widget.selected_list_widget.item(0)
        )
        self.assertEqual([], self.widget.selected_tag_ids())

    def test_missing_database_raises(self):
        with patch(
            "services.global_instances.current_library_db",
            None,
        ):
            with self.assertRaises(RuntimeError):
                TagSelectorWidget(database=None)


class SubstringTagSearchMatcherTests(unittest.TestCase):
    def setUp(self):
        self.matcher = SubstringTagSearchMatcher()
        self.tags = [
            make_tag("Alpha"),
            make_tag("beta"),
            make_tag("Beta Plus"),
        ]

    def test_empty_query_returns_all(self):
        self.assertEqual(3, len(self.matcher.filter_tags(self.tags, "")))
        self.assertEqual(3, len(self.matcher.filter_tags(self.tags, "  ")))

    def test_substring_match_is_case_insensitive(self):
        names = [tag.name for tag in self.matcher.filter_tags(self.tags, "BET")]
        self.assertEqual(["beta", "Beta Plus"], names)

    def test_no_match_returns_empty(self):
        self.assertEqual([], self.matcher.filter_tags(self.tags, "zzz"))

    def test_preserves_input_order(self):
        names = [tag.name for tag in self.matcher.filter_tags(self.tags, "a")]
        self.assertEqual(["Alpha", "beta", "Beta Plus"], names)


if __name__ == "__main__":
    unittest.main()
