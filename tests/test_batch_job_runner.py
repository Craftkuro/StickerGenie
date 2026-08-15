import multiprocessing
import os
import threading
import time
import unittest

import numpy as np

from batch_job_runner import (
    BatchJobRunner,
    GeneralDataWrapper,
    JobCancelledError,
    JobError,
    JobProgress,
    JobSummary,
    JobTimeoutError,
    PipelineSpec,
    QueueSpec,
    ResultBatch,
    StageSpec,
    WorkerCrashedError,
    WorkerInitializationError,
    validate_pipeline_spec,
)
from batch_job_runner.scheduler import (
    CANCEL,
    DONE,
    END_INPUT,
    INIT_OK,
    ITEMS,
    JOB_ERROR,
    RESULT_BATCH,
    scheduler_entry,
)


def stage_upper(item):
    if item.startswith("bad"):
        raise ValueError("damaged")
    return item.upper()


def stage_prefix(items):
    return [f"pre:{item}" for item in items]


def stage_double(items):
    return [item * 2 for item in items]


def stage_batch_fail(items):
    if any("bad" in item for item in items):
        raise RuntimeError("batch failed")
    return [item * 2 for item in items]


def stage_wrong_length(items):
    return ["only-one"]


def stage_crash(item):
    os._exit(17)


def stage_slow(item):
    time.sleep(2.0)
    return item


def stage_setup_ok():
    return {"engine": "fake"}


def stage_setup_error():
    raise RuntimeError("engine missing")


def stage_vector_preprocess(path):
    return (path, np.full(4096, 1.0, dtype=np.float32))


def stage_vector_infer(items):
    return [
        (path, np.full(4096, 1.0, dtype=np.float32))
        for path, _ in items
    ]


class _UpperPrefixRunner(BatchJobRunner):
    def build_pipeline(self):
        return PipelineSpec(
            queues=(
                QueueSpec("input", 8),
                QueueSpec("mid", 4),
                QueueSpec("output", 4),
            ),
            stages=(
                StageSpec("upper", "input", "mid", stage_upper, pool_size=2),
                StageSpec(
                    "prefix",
                    "mid",
                    "output",
                    stage_prefix,
                    pool_size=1,
                    batch_size=2,
                ),
            ),
            setup_func=stage_setup_ok,
            result_batch_size=8,
        )


class _SingleStepRunner(BatchJobRunner):
    def __init__(self, func, *, batch_size=1, pool_size=1, setup=None):
        self._func = func
        self._batch_size = batch_size
        self._pool_size = pool_size
        self._setup = setup

    def build_pipeline(self):
        return PipelineSpec(
            queues=(QueueSpec("input", 8), QueueSpec("output", 8)),
            stages=(
                StageSpec(
                    "step",
                    "input",
                    "output",
                    self._func,
                    pool_size=self._pool_size,
                    batch_size=self._batch_size,
                ),
            ),
            setup_func=self._setup,
            result_batch_size=8,
        )


class _LargeVectorRunner(BatchJobRunner):
    def build_pipeline(self):
        return PipelineSpec(
            queues=(
                QueueSpec("input", 64),
                QueueSpec("preprocessed", 16),
                QueueSpec("inferred", 8),
            ),
            stages=(
                StageSpec(
                    "preprocess",
                    "input",
                    "preprocessed",
                    stage_vector_preprocess,
                    pool_size=4,
                ),
                StageSpec(
                    "infer",
                    "preprocessed",
                    "inferred",
                    stage_vector_infer,
                    pool_size=1,
                    batch_size=8,
                ),
            ),
            setup_func=stage_setup_ok,
            result_batch_size=8,
        )


def _spawn_scheduler(spec):
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=True)
    process = context.Process(
        target=scheduler_entry,
        args=(child_connection, spec),
        name="TestSchedulerWorker",
    )
    process.daemon = False
    process.start()
    child_connection.close()
    return process, parent_connection


def _active_worker_names():
    return {child.name for child in multiprocessing.active_children()}


class GeneralDataWrapperTests(unittest.TestCase):
    def test_invariants(self):
        ok = GeneralDataWrapper(data="x")
        self.assertFalse(ok.hasException)
        self.assertIsNone(ok.error)
        self.assertIsNone(ok.stage_name)

        failed = GeneralDataWrapper(
            data="x",
            hasException=True,
            error="ValueError: damaged",
            stage_name="upper",
        )
        self.assertTrue(failed.hasException)

        with self.assertRaises(ValueError):
            GeneralDataWrapper(
                data="x",
                hasException=True,
                error="",
                stage_name="upper",
            )
        with self.assertRaises(ValueError):
            GeneralDataWrapper(
                data="x",
                hasException=True,
                error="ValueError: damaged",
                stage_name=None,
            )
        with self.assertRaises(ValueError):
            GeneralDataWrapper(
                data="x",
                hasException=False,
                error="oops",
            )

    def test_progress_and_result_batch_invariants(self):
        progress = JobProgress(3, 5, 2, 1)
        self.assertEqual(3, progress.completed)
        with self.assertRaises(ValueError):
            JobProgress(3, 5, 1, 1)
        with self.assertRaises(ValueError):
            JobProgress(6, 5, 6, 0)

        batch = ResultBatch(
            results=(GeneralDataWrapper(data=1), GeneralDataWrapper(data=2)),
            progress=progress,
        )
        self.assertEqual(2, len(batch.results))
        with self.assertRaises(ValueError):
            ResultBatch(results=(), progress=progress)
        with self.assertRaises(ValueError):
            ResultBatch(results=(object(),), progress=progress)

        summary = JobSummary(
            results=(),
            completed=2,
            succeeded=2,
            failed=0,
            cancelled=False,
            duration_seconds=0.5,
            total=2,
        )
        self.assertEqual(2, summary.completed)
        with self.assertRaises(ValueError):
            JobSummary(
                results=(),
                completed=2,
                succeeded=2,
                failed=0,
                cancelled=False,
                duration_seconds=-1,
                total=2,
            )


class PipelineSpecValidationTests(unittest.TestCase):
    def test_valid_two_stage_chain(self):
        spec = PipelineSpec(
            queues=(
                QueueSpec("input", 8),
                QueueSpec("mid", 4),
                QueueSpec("output", 4),
            ),
            stages=(
                StageSpec("a", "input", "mid", stage_upper, pool_size=1),
                StageSpec(
                    "b", "mid", "output", stage_prefix, pool_size=1, batch_size=2
                ),
            ),
        )
        validate_pipeline_spec(spec)

    def test_duplicate_queue_names(self):
        spec = PipelineSpec(
            queues=(QueueSpec("input", 8), QueueSpec("input", 8)),
            stages=(StageSpec("a", "input", "input", stage_upper, pool_size=1),),
        )
        with self.assertRaises(ValueError):
            validate_pipeline_spec(spec)

    def test_broken_chain(self):
        spec = PipelineSpec(
            queues=(QueueSpec("input", 8), QueueSpec("other", 8)),
            stages=(
                StageSpec("a", "input", "other", stage_upper, pool_size=1),
                StageSpec("b", "input", "other", stage_prefix, pool_size=1),
            ),
        )
        with self.assertRaises(ValueError):
            validate_pipeline_spec(spec)

    def test_unknown_queue_reference(self):
        spec = PipelineSpec(
            queues=(QueueSpec("input", 8),),
            stages=(StageSpec("a", "input", "missing", stage_upper, pool_size=1),),
        )
        with self.assertRaises(ValueError):
            validate_pipeline_spec(spec)

    def test_invalid_parameters(self):
        with self.assertRaises(ValueError):
            QueueSpec("input", 0)
        with self.assertRaises(ValueError):
            StageSpec("a", "input", "output", stage_upper, pool_size=0)
        with self.assertRaises(ValueError):
            StageSpec("a", "input", "output", stage_upper, pool_size=1, batch_size=0)
        with self.assertRaises(ValueError):
            PipelineSpec(
                queues=(QueueSpec("input", 8),),
                stages=(StageSpec("a", "input", "input", stage_upper, pool_size=1),),
                result_batch_size=0,
            )
        with self.assertRaises(TypeError):
            validate_pipeline_spec(object())


class SchedulerIntegrationTests(unittest.TestCase):
    def test_normal_two_stage_pipeline(self):
        runner = _UpperPrefixRunner()
        progress_updates = []
        startup = []
        batches = list(
            runner.iter_results(
                ["one", "two", "bad", "three"],
                progress=progress_updates.append,
                started=startup.append,
                timeout=10,
            )
        )

        results = [w for batch in batches for w in batch.results]
        by_data = {w.data: w for w in results}
        self.assertEqual(4, len(results))
        self.assertFalse(by_data["pre:ONE"].hasException)
        self.assertFalse(by_data["pre:TWO"].hasException)
        self.assertFalse(by_data["pre:THREE"].hasException)

        failed = by_data["bad"]
        self.assertTrue(failed.hasException)
        self.assertEqual("upper", failed.stage_name)
        self.assertIn("ValueError", failed.error)

        self.assertEqual({"engine": "fake"}, startup[0])
        self.assertEqual(4, progress_updates[-1].completed)
        self.assertEqual(4, progress_updates[-1].total)
        self.assertEqual(3, progress_updates[-1].succeeded)
        self.assertEqual(1, progress_updates[-1].failed)
        self.assertNotIn("BatchJobRunnerWorker", _active_worker_names())

    def test_single_item_failure_does_not_interrupt_job(self):
        runner = _SingleStepRunner(stage_upper)
        batches = list(runner.iter_results(["good", "bad", "fine"], timeout=10))
        results = [w for batch in batches for w in batch.results]
        self.assertEqual(3, len(results))
        self.assertEqual(
            [False, True, False],
            [w.hasException for w in results],
        )
        self.assertEqual("step", results[1].stage_name)

    def test_batch_stage_aggregates_and_passes_failures_through(self):
        runner = _SingleStepRunner(stage_double, batch_size=2, pool_size=1)
        batches = list(runner.iter_results(["a", "b", "c"], timeout=10))
        results = [w for batch in batches for w in batch.results]
        self.assertEqual(3, len(results))
        self.assertEqual(
            {"aa", "bb", "cc"},
            {w.data for w in results},
        )

    def test_batch_stage_failure_marks_the_whole_batch_failed(self):
        runner = _SingleStepRunner(stage_batch_fail, batch_size=2, pool_size=1)
        batches = list(runner.iter_results(["a", "bad", "c"], timeout=10))
        results = [w for batch in batches for w in batch.results]
        self.assertEqual(3, len(results))
        # "a" and "bad" share the failing batch; "c" runs alone and succeeds.
        self.assertEqual(2, sum(w.hasException for w in results))
        self.assertTrue(
            all(w.stage_name == "step" for w in results if w.hasException)
        )

    def test_run_collects_all_results(self):
        runner = _SingleStepRunner(stage_upper, setup=stage_setup_ok)
        summary = runner.run(["a", "b", "bad"], timeout=10)
        self.assertIsInstance(summary, JobSummary)
        self.assertEqual(3, summary.completed)
        self.assertEqual(2, summary.succeeded)
        self.assertEqual(1, summary.failed)
        self.assertFalse(summary.cancelled)
        self.assertEqual(3, len(summary.results))
        self.assertEqual({"engine": "fake"}, summary.startup_info)

    def test_empty_input_completes_normally(self):
        runner = _SingleStepRunner(stage_upper)
        summary = runner.run([], timeout=10)
        self.assertFalse(summary.cancelled)
        self.assertEqual(0, summary.completed)

    def test_cancellation_raises_and_reaps_worker(self):
        runner = _SingleStepRunner(stage_upper)
        cancel_event = threading.Event()

        def cancel_after_first(progress):
            if progress.completed:
                cancel_event.set()

        with self.assertRaises(JobCancelledError):
            list(
                runner.iter_results(
                    ["one", "two", "three"],
                    cancel_event=cancel_event,
                    progress=cancel_after_first,
                    timeout=10,
                )
            )
        self.assertNotIn("BatchJobRunnerWorker", _active_worker_names())

    def test_run_cancelled_returns_cancelled_summary(self):
        runner = _SingleStepRunner(stage_upper)
        cancel_event = threading.Event()

        def cancel_after_first(progress):
            if progress.completed:
                cancel_event.set()

        summary = runner.run(
            ["one", "two", "three"],
            cancel_event=cancel_event,
            progress=cancel_after_first,
            timeout=10,
        )
        self.assertTrue(summary.cancelled)
        self.assertGreaterEqual(summary.completed, 1)

    def test_timeout_raises_and_reaps_worker(self):
        runner = _SingleStepRunner(stage_slow)
        with self.assertRaises(JobTimeoutError):
            runner.run(["one"], timeout=0.2)
        self.assertNotIn("BatchJobRunnerWorker", _active_worker_names())

    def test_worker_crash_raises_worker_crashed_error(self):
        runner = _SingleStepRunner(stage_crash)
        with self.assertRaises(WorkerCrashedError):
            runner.run(["one"], timeout=10)
        self.assertNotIn("BatchJobRunnerWorker", _active_worker_names())

    def test_initialization_failure_is_reported(self):
        runner = _SingleStepRunner(stage_upper, setup=stage_setup_error)
        with self.assertRaises(WorkerInitializationError):
            runner.run([], timeout=10)
        self.assertNotIn("BatchJobRunnerWorker", _active_worker_names())

    def test_contract_violation_is_job_error(self):
        runner = _SingleStepRunner(stage_wrong_length, batch_size=2, pool_size=1)
        with self.assertRaises(JobError):
            runner.run(["a", "b"], timeout=10)
        self.assertNotIn("BatchJobRunnerWorker", _active_worker_names())

    def test_large_input_stream_does_not_deadlock(self):
        runner = _UpperPrefixRunner()
        items = [f"item-{index}" for index in range(500)]
        summary = runner.run(items, timeout=60)
        self.assertEqual(500, summary.completed)
        self.assertEqual(500, summary.succeeded)
        self.assertEqual(500, len(summary.results))
        self.assertNotIn("BatchJobRunnerWorker", _active_worker_names())

    def test_large_result_payload_stream_does_not_deadlock(self):
        runner = _LargeVectorRunner()
        items = [f"item-{index}" for index in range(256)]
        summary = runner.run(items, timeout=60)
        self.assertEqual(256, summary.completed)
        self.assertEqual(256, summary.succeeded)
        self.assertEqual(256, len(summary.results))
        self.assertNotIn("BatchJobRunnerWorker", _active_worker_names())


class SchedulerProtocolTests(unittest.TestCase):
    def test_unknown_parent_message_sends_job_error(self):
        spec = PipelineSpec(
            queues=(QueueSpec("input", 8), QueueSpec("output", 8)),
            stages=(
                StageSpec("step", "input", "output", stage_upper, pool_size=1),
            ),
            setup_func=stage_setup_ok,
        )
        process, connection = _spawn_scheduler(spec)
        try:
            kind, payload = connection.recv()
            self.assertEqual(INIT_OK, kind)
            connection.send(("BOGUS", None))
            kind, payload = connection.recv()
            self.assertEqual(JOB_ERROR, kind)
            self.assertIn("unknown parent IPC message", payload)
        finally:
            connection.close()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)

    def test_cancel_flushes_in_flight_last_stage_results(self):
        spec = PipelineSpec(
            queues=(QueueSpec("input", 8), QueueSpec("output", 8)),
            stages=(
                StageSpec("step", "input", "output", stage_slow, pool_size=1),
            ),
            setup_func=stage_setup_ok,
            result_batch_size=8,
        )
        process, connection = _spawn_scheduler(spec)
        try:
            kind, payload = connection.recv()
            self.assertEqual(INIT_OK, kind)
            connection.send((ITEMS, ("a", "b", "c")))
            time.sleep(0.3)
            connection.send((CANCEL, None))
            flushed = 0
            while True:
                kind, payload = connection.recv()
                if kind == RESULT_BATCH:
                    flushed += len(payload)
                elif kind == DONE:
                    self.assertTrue(payload)
                    break
            self.assertLessEqual(flushed, 3)
        finally:
            connection.close()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    unittest.main()
