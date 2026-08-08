import multiprocessing
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from image_features_extractor import ExtractionRequest
from image_features_extractor.qt import QtImageFeaturesExtractor
from test_image_features_extractor import fake_worker_entry


class QtImageFeaturesExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _run_until_terminal(self, extractor, request, *, cancel_on_started=False):
        loop = QEventLoop()
        events = {"started": [], "batches": [], "finished": [], "failed": [], "cancelled": 0}

        extractor.started.connect(events["started"].append)
        extractor.batch_ready.connect(events["batches"].append)
        extractor.finished.connect(lambda summary: (events["finished"].append(summary), loop.quit()))
        extractor.failed.connect(lambda error: (events["failed"].append(error), loop.quit()))

        def on_cancelled():
            events["cancelled"] += 1
            loop.quit()

        extractor.cancelled.connect(on_cancelled)
        if cancel_on_started:
            extractor.started.connect(extractor.cancel)

        start_time = __import__("time").monotonic()
        extractor.start(request)
        self.assertLess(__import__("time").monotonic() - start_time, 0.05)
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        extractor.close()
        return events

    def test_normal_job_emits_started_batches_progress_and_finished(self):
        extractor = QtImageFeaturesExtractor(poll_interval_ms=5)
        progress = []
        extractor.progress_changed.connect(progress.append)

        with patch(
            "image_features_extractor.extractor.worker_process_entry",
            fake_worker_entry,
        ):
            events = self._run_until_terminal(
                extractor,
                ExtractionRequest(
                    ["one.png", "bad.png", "three.png"],
                    model_path="normal.onnx",
                    batch_size=2,
                    timeout=10,
                ),
            )

        self.assertEqual(1, len(events["started"]))
        self.assertEqual([2, 1], [len(batch.results) for batch in events["batches"]])
        self.assertEqual([2, 3], [item.completed for item in progress])
        self.assertEqual(1, len(events["finished"]))
        self.assertEqual([], events["failed"])
        self.assertEqual(0, events["cancelled"])

    def test_cancel_emits_only_cancelled_terminal_signal(self):
        extractor = QtImageFeaturesExtractor(poll_interval_ms=5)
        with patch(
            "image_features_extractor.extractor.worker_process_entry",
            fake_worker_entry,
        ):
            events = self._run_until_terminal(
                extractor,
                ExtractionRequest(
                    ["one.png", "two.png"],
                    model_path="normal.onnx",
                    timeout=10,
                ),
                cancel_on_started=True,
            )

        self.assertEqual(1, events["cancelled"])
        self.assertEqual([], events["finished"])
        self.assertEqual([], events["failed"])

    def test_job_error_emits_only_failed_terminal_signal(self):
        extractor = QtImageFeaturesExtractor(poll_interval_ms=5)
        with patch(
            "image_features_extractor.extractor.worker_process_entry",
            fake_worker_entry,
        ):
            events = self._run_until_terminal(
                extractor,
                ExtractionRequest([], model_path="init_error.onnx", timeout=10),
            )

        self.assertEqual(["fake initialization failure"], events["failed"])
        self.assertEqual([], events["finished"])
        self.assertEqual(0, events["cancelled"])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    unittest.main()
