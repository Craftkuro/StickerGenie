import unittest
from unittest.mock import Mock

from services.similarity_result_filter import (
    SimilarityFilterConfig,
    SimilarityResultFilter,
    create_filter_from_settings,
)
from stickerdb.vectordb.models import SearchResult, VectorMetadata


def _make_result(sqlite_id: int, similarity: float) -> SearchResult:
    metadata = VectorMetadata(
        image_filename=f"{sqlite_id}.png",
        model_hash="test-model",
        sqlite_id=sqlite_id,
        extraction_timestamp=0.0,
        image_width=1,
        image_height=1,
    )
    return SearchResult(
        id=f"vector-{sqlite_id}",
        distance=1.0 - similarity,
        metadata=metadata,
    )


def _make_results(scores: list[float]) -> list[SearchResult]:
    return [
        _make_result(index + 1, score)
        for index, score in enumerate(scores)
    ]


class SimilarityFilterConfigTests(unittest.TestCase):
    def test_rejects_invalid_drop_ratio(self):
        with self.assertRaises(ValueError):
            SimilarityFilterConfig(target_drop_ratio=0.0)
        with self.assertRaises(ValueError):
            SimilarityFilterConfig(target_drop_ratio=1.0)
        with self.assertRaises(ValueError):
            SimilarityFilterConfig(target_drop_ratio=-0.1)

    def test_rejects_negative_min_keep(self):
        with self.assertRaises(ValueError):
            SimilarityFilterConfig(min_keep=-1)

    def test_rejects_out_of_range_similarity(self):
        with self.assertRaises(ValueError):
            SimilarityFilterConfig(min_similarity=-0.1)
        with self.assertRaises(ValueError):
            SimilarityFilterConfig(min_similarity=1.1)

    def test_rejects_non_positive_max_results(self):
        with self.assertRaises(ValueError):
            SimilarityFilterConfig(max_results=0)


class CreateFilterFromSettingsTests(unittest.TestCase):
    def test_reads_similar_image_settings(self):
        settings_manager = Mock()
        settings_manager.get.side_effect = lambda key: {
            "similar_image_target_drop_ratio": "0.35",
            "similar_image_min_keep": 7,
            "similar_image_min_similarity": "0.60",
            "similar_image_max_results": 40,
        }.get(key)

        filt = create_filter_from_settings(settings_manager)

        self.assertEqual(
            SimilarityFilterConfig(
                target_drop_ratio=0.35,
                min_keep=7,
                min_similarity=0.60,
                max_results=40,
            ),
            filt.config,
        )


class SimilarityResultFilterTests(unittest.TestCase):
    def test_empty_results_return_empty(self):
        filt = SimilarityResultFilter()
        self.assertEqual([], filt.filter([]))

    def test_result_below_min_similarity_returns_empty(self):
        filt = SimilarityResultFilter(
            SimilarityFilterConfig(min_similarity=0.60)
        )
        results = _make_results([0.55, 0.50, 0.45])
        self.assertEqual([], filt.filter(results))

    def test_min_keep_prevents_lone_result(self):
        filt = SimilarityResultFilter(
            SimilarityFilterConfig(
                target_drop_ratio=0.5,
                min_keep=5,
                min_similarity=0.40,
                max_results=100,
            )
        )
        # A steep drop after the first candidate would keep only 1 result
        # without min_keep.
        results = _make_results([0.98, 0.85, 0.84, 0.83, 0.82, 0.50])
        kept = filt.filter(results)
        self.assertEqual(5, len(kept))
        self.assertEqual([1, 2, 3, 4, 5], [r.sqlite_id for r in kept])

    def test_drop_rate_keeps_gentle_plateau(self):
        filt = SimilarityResultFilter(
            SimilarityFilterConfig(
                target_drop_ratio=0.5,
                min_keep=0,
                min_similarity=0.40,
                max_results=100,
            )
        )
        # Linear drop from 0.90 to 0.80 over 11 candidates.
        scores = [0.90 - index * 0.01 for index in range(11)]
        results = _make_results(scores)
        kept = filt.filter(results)
        # Total drop 0.10, target 0.05 -> cumulative drop reaches 0.05
        # after the gap between the 5th and 6th candidates, so keep 5.
        self.assertEqual(5, len(kept))
        self.assertEqual([1, 2, 3, 4, 5], [r.sqlite_id for r in kept])

    def test_drop_rate_cuts_after_steep_cliff(self):
        filt = SimilarityResultFilter(
            SimilarityFilterConfig(
                target_drop_ratio=0.5,
                min_keep=0,
                min_similarity=0.40,
                max_results=100,
            )
        )
        results = _make_results([0.95, 0.94, 0.60, 0.59, 0.58])
        kept = filt.filter(results)
        # Total drop 0.37, target 0.185. After index 1 drop is 0.01,
        # after index 2 drop is 0.35 -> keep first 2 candidates.
        self.assertEqual(2, len(kept))
        self.assertEqual([1, 2], [r.sqlite_id for r in kept])

    def test_respects_max_results(self):
        filt = SimilarityResultFilter(
            SimilarityFilterConfig(
                target_drop_ratio=0.9,
                min_keep=200,
                min_similarity=0.40,
                max_results=10,
            )
        )
        results = _make_results([0.95 - index * 0.001 for index in range(200)])
        kept = filt.filter(results)
        self.assertEqual(10, len(kept))

    def test_respects_min_similarity_floor(self):
        filt = SimilarityResultFilter(
            SimilarityFilterConfig(
                target_drop_ratio=0.9,
                min_keep=10,
                min_similarity=0.70,
                max_results=100,
            )
        )
        results = _make_results([0.95, 0.80, 0.75, 0.65, 0.60])
        kept = filt.filter(results)
        # min_keep would ask for 10, but only 3 are above min_similarity.
        self.assertEqual(3, len(kept))
        self.assertEqual([1, 2, 3], [r.sqlite_id for r in kept])

    def test_preserves_input_order(self):
        filt = SimilarityResultFilter()
        results = _make_results([0.80, 0.75, 0.70])
        kept = filt.filter(results)
        similarities = [r.similarity for r in kept]
        self.assertEqual([0.80, 0.75, 0.70], similarities)


if __name__ == "__main__":
    unittest.main()
