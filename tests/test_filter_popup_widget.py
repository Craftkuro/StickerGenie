import unittest

from PyQt6.QtWidgets import QApplication

from services.similarity_result_filter import SimilarityFilterConfig
from ui.widgets.filter_popup_widget import SimilarityFilterPopupWidget


_app = None


def _ensure_qapp():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])


class SimilarityFilterPopupWidgetTests(unittest.TestCase):
    def setUp(self):
        _ensure_qapp()
        self.config = SimilarityFilterConfig(
            target_drop_ratio=0.50,
            min_keep=5,
            min_similarity=0.20,
            max_results=100,
        )
        self.popup = SimilarityFilterPopupWidget(
            initial_config=self.config,
            initial_enabled=True,
        )

    def test_initial_values_match_config(self):
        self.assertTrue(self.popup._enabled_checkbox.isChecked())
        self.assertEqual(50, self.popup._drop_ratio_slider.value())
        self.assertEqual(20, self.popup._min_sim_slider.value())
        self.assertEqual(5, self.popup._min_keep_spin.value())
        self.assertEqual(100, self.popup._max_results_spin.value())

    def test_slider_updates_label_text(self):
        self.popup._drop_ratio_slider.setValue(75)
        self.assertEqual("0.75", self.popup._drop_ratio_label.text())
        self.popup._min_sim_slider.setValue(10)
        self.assertEqual("0.10", self.popup._min_sim_label.text())

    def test_emit_filter_sends_config_and_enabled(self):
        received = []
        self.popup.filter_applied.connect(lambda e, c: received.append((e, c)))
        self.popup._drop_ratio_slider.setValue(30)
        self.popup._min_keep_spin.setValue(7)
        self.popup._apply_button.click()
        self.assertEqual(1, len(received))
        enabled, config = received[0]
        self.assertTrue(enabled)
        self.assertAlmostEqual(0.30, config.target_drop_ratio, places=2)
        self.assertEqual(7, config.min_keep)

    def test_disabling_checkbox_disables_controls(self):
        self.popup._enabled_checkbox.setChecked(False)
        self.assertFalse(self.popup._drop_ratio_slider.isEnabled())
        self.assertFalse(self.popup._min_keep_spin.isEnabled())
        self.assertTrue(self.popup._apply_button.isEnabled())

    def test_update_state_syncs_widgets(self):
        new_config = SimilarityFilterConfig(
            target_drop_ratio=0.80,
            min_keep=3,
            min_similarity=0.60,
            max_results=50,
        )
        self.popup.update_state(False, new_config)
        self.assertFalse(self.popup._enabled_checkbox.isChecked())
        self.assertEqual(80, self.popup._drop_ratio_slider.value())
        self.assertEqual(60, self.popup._min_sim_slider.value())
        self.assertEqual(3, self.popup._min_keep_spin.value())
        self.assertEqual(50, self.popup._max_results_spin.value())


if __name__ == "__main__":
    unittest.main()
