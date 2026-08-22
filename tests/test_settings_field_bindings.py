import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QApplication,
    QDoubleSpinBox,
    QSpinBox,
)

from config_manager import ConfigField, ConfigType, FieldUI, WidgetKind
from services.settings import SETTINGS_SCHEMA
from ui.settings_field_bindings import (
    SettingsBindingError,
    build_field_widget,
    connect_field_signal,
    field_label_text,
    read_field_value,
    write_field_value,
)


def make_field(**ui_kwargs) -> ConfigField:
    return ConfigField(
        "demo_key", ConfigType.INT, 7, "演示用配置项", ui=FieldUI(**ui_kwargs)
    )


class SpinBoxBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_spin_box(self):
        field = make_field(
            label="数量",
            suffix=" 张",
            minimum=100,
            maximum=100000,
            step=100,
        )
        widget = build_field_widget(field)
        self.assertIsInstance(widget, QSpinBox)
        return field, widget

    def test_build_applies_range_step_suffix_metadata(self):
        _, widget = self.make_spin_box()

        self.assertEqual("field_demo_key", widget.objectName())
        self.assertEqual("演示用配置项", widget.toolTip())
        self.assertEqual(100, widget.minimum())
        self.assertEqual(100000, widget.maximum())
        self.assertEqual(100, widget.singleStep())
        self.assertEqual(" 张", widget.suffix())

    def test_int_round_trip(self):
        field, widget = self.make_spin_box()

        write_field_value(field, widget, 1500)

        self.assertEqual(1500, widget.value())
        self.assertEqual(1500, read_field_value(field, widget))

    def test_label_falls_back_to_key_then_label(self):
        self.assertEqual("数量", field_label_text(make_field(label="数量")))
        self.assertEqual(
            "demo_key", field_label_text(make_field(label="", page="p"))
        )


class SpinBox2PBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_spin_box_2p(self, default="0.50"):
        field = ConfigField(
            "ratio_key",
            ConfigType.STRING,
            default,
            "比例说明",
            ui=FieldUI(
                kind=WidgetKind.SPIN_BOX_2P,
                minimum=0.01,
                maximum=0.99,
                step=0.05,
            ),
        )
        widget = build_field_widget(field)
        self.assertIsInstance(widget, QDoubleSpinBox)
        self.assertEqual(2, widget.decimals())
        return field, widget

    def test_string_value_formats_to_two_digits(self):
        for stored, expected in [("0.5", "0.50"), ("0.33", "0.33"), ("0.42", "0.42")]:
            with self.subTest(stored=stored):
                field, widget = self.make_spin_box_2p()

                write_field_value(field, widget, stored)

                self.assertEqual(float(expected), widget.value())
                self.assertEqual(expected, read_field_value(field, widget))

    def test_range_and_step_applied(self):
        _, widget = self.make_spin_box_2p()

        self.assertEqual(0.01, widget.minimum())
        self.assertEqual(0.99, widget.maximum())
        self.assertEqual(0.05, widget.singleStep())

    def test_invalid_value_falls_back_to_default(self):
        field, widget = self.make_spin_box_2p(default="0.50")

        write_field_value(field, widget, "not-a-number")

        self.assertEqual(0.5, widget.value())
        self.assertEqual("0.50", read_field_value(field, widget))


class ComboBoxBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_combo_box(self):
        field = ConfigField(
            "mode_key",
            ConfigType.STRING,
            "b",
            "模式说明",
            ui=FieldUI(
                kind=WidgetKind.COMBO_BOX,
                choices=(("甲", "a"), ("乙", "b")),
            ),
        )
        widget = build_field_widget(field)
        self.assertIsInstance(widget, QComboBox)
        return field, widget

    def test_choices_become_items_with_data(self):
        _, widget = self.make_combo_box()

        self.assertEqual(2, widget.count())
        self.assertEqual("甲", widget.itemText(0))
        self.assertEqual("a", widget.itemData(0))
        self.assertEqual("乙", widget.itemText(1))
        self.assertEqual("b", widget.itemData(1))

    def test_select_by_stored_value_and_read_back(self):
        field, widget = self.make_combo_box()

        write_field_value(field, widget, "b")

        self.assertEqual(1, widget.currentIndex())
        self.assertEqual("b", read_field_value(field, widget))

    def test_unknown_value_raises_binding_error(self):
        field, widget = self.make_combo_box()

        with self.assertRaises(SettingsBindingError):
            write_field_value(field, widget, "missing")

    def test_empty_choices_fail_at_assembly_time(self):
        field = make_field(kind=WidgetKind.COMBO_BOX)

        with self.assertRaises(SettingsBindingError):
            build_field_widget(field)


class CheckBoxBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_check_box(self):
        field = ConfigField(
            "flag_key",
            ConfigType.BOOL,
            False,
            "开关说明",
            ui=FieldUI(kind=WidgetKind.CHECK_BOX),
        )
        widget = build_field_widget(field)
        self.assertIsInstance(widget, QCheckBox)
        return field, widget

    def test_bool_round_trip(self):
        field, widget = self.make_check_box()

        write_field_value(field, widget, True)

        self.assertTrue(widget.isChecked())
        self.assertIs(True, read_field_value(field, widget))


class SignalAndErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_change_signals_fire_callback_for_each_kind(self):
        received = []

        spin_field = make_field()
        spin_widget = build_field_widget(spin_field)
        connect_field_signal(spin_field, spin_widget, lambda v: received.append(("spin", v)))
        spin_widget.setValue(9)

        combo_field = ConfigField(
            "combo", ConfigType.STRING, "a", "",
            ui=FieldUI(kind=WidgetKind.COMBO_BOX, choices=(("甲", "a"), ("乙", "b"))),
        )
        combo_widget = build_field_widget(combo_field)
        connect_field_signal(combo_field, combo_widget, lambda i: received.append(("combo", i)))
        combo_widget.setCurrentIndex(1)

        check_field = ConfigField(
            "check", ConfigType.BOOL, False, "",
            ui=FieldUI(kind=WidgetKind.CHECK_BOX),
        )
        check_widget = build_field_widget(check_field)
        connect_field_signal(check_field, check_widget, lambda v: received.append(("check", v)))
        check_widget.setChecked(True)

        self.assertIn(("spin", 9), received)
        self.assertIn(("combo", 1), received)
        self.assertIn(("check", True), received)

    def test_missing_ui_raises_binding_error(self):
        field = ConfigField("bare", ConfigType.INT, 1)

        with self.assertRaises(SettingsBindingError):
            build_field_widget(field)

    def test_unsupported_kind_raises_binding_error(self):
        field = make_field(kind=WidgetKind.HIDDEN)

        with self.assertRaises(SettingsBindingError):
            build_field_widget(field)


class SchemaAnnotationTests(unittest.TestCase):
    """对照迁移清单：可见字段的页面/分组/范围与原 .ui 一致。"""

    UI_FIELDS = {
        "thumbnail_memory_cache_size": dict(
            page="general", minimum=100, maximum=100000, step=100
        ),
        "recent_search_limit": dict(page="search", maximum=100),
        "tag_suggestion_limit": dict(page="search", maximum=100),
        "similar_image_target_drop_ratio": dict(
            page="search", minimum=0.01, maximum=0.99, step=0.05
        ),
        "similar_image_min_keep": dict(page="search", maximum=50),
        "similar_image_min_similarity": dict(
            page="search", minimum=0.0, maximum=1.0, step=0.05
        ),
        "similar_image_max_results": dict(page="search", minimum=1, maximum=200),
        "similar_image_candidate_count": dict(
            page="search", minimum=1, maximum=10000, step=50
        ),
    }
    HIDDEN_KEYS = {
        "library_base_path",
        "recent_searches",
        "color_presets",
    }

    def test_visible_fields_carry_expected_ui_metadata(self):
        fields = {field.key: field for field in SETTINGS_SCHEMA}

        for key in set(self.UI_FIELDS) | self.HIDDEN_KEYS:
            self.assertIn(key, fields)

        for key, expected in self.UI_FIELDS.items():
            with self.subTest(key=key):
                ui = fields[key].ui
                self.assertIsNotNone(ui)
                for attr, value in expected.items():
                    self.assertEqual(value, getattr(ui, attr))
                if "minimum" not in expected:
                    self.assertIsNone(ui.minimum)

    def test_hidden_fields_have_no_ui(self):
        fields = {field.key: field for field in SETTINGS_SCHEMA}
        for key in self.HIDDEN_KEYS:
            self.assertIsNone(fields[key].ui)

    def test_visible_fields_keep_schema_order_of_original_ui_layout(self):
        visible_keys = [
            field.key for field in SETTINGS_SCHEMA if field.ui is not None
        ]
        self.assertEqual(list(self.UI_FIELDS), visible_keys)


if __name__ == "__main__":
    unittest.main()
