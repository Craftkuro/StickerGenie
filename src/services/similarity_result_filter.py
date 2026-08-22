# coding=utf-8
"""Per-query adaptive filter for vector similarity search results.

The filter takes an ordered list of search candidates and returns only those
that belong to the same "relevant plateau" according to the query's own score
curve. The key idea is drop-rate knee detection: keep candidates until the
cumulative similarity drop reaches a configured ratio of the total drop,
always retaining at least ``min_keep`` results so that a single near-duplicate
does not hide the rest of the similar images.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import commons.constants
from config_manager import ConfigManager
from stickerdb.vectordb.models import SearchResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SimilarityFilterConfig:
    """Tunable parameters for the similarity result filter."""

    # Keep candidates until the cumulative similarity drop from the top score
    # reaches this ratio of the total drop (top_score - bottom_score).
    target_drop_ratio: float = 0.5

    # Always keep at least this many candidates when the query has enough
    # relevant results, preventing a lone duplicate from appearing.
    min_keep: int = 5

    # Absolute floor for a candidate to be considered relevant at all.
    min_similarity: float = 0.50

    # Hard cap on the number of returned candidates.
    max_results: int = commons.constants.SIMILAR_IMAGE_MAX_RESULTS

    def __post_init__(self) -> None:
        if not 0.0 < self.target_drop_ratio < 1.0:
            raise ValueError(
                "target_drop_ratio must be between 0 and 1, "
                f"got {self.target_drop_ratio}"
            )
        if self.min_keep < 0:
            raise ValueError(
                f"min_keep must be non-negative, got {self.min_keep}"
            )
        if not 0.0 <= self.min_similarity <= 1.0:
            raise ValueError(
                "min_similarity must be between 0 and 1, "
                f"got {self.min_similarity}"
            )
        if self.max_results <= 0:
            raise ValueError(
                f"max_results must be positive, got {self.max_results}"
            )


class SimilarityResultFilter:
    """Adaptive filter for a single query's similarity search results."""

    def __init__(self, config: SimilarityFilterConfig | None = None) -> None:
        self.config = config or SimilarityFilterConfig()

    def filter(self, results: Sequence[SearchResult]) -> list[SearchResult]:
        """Return relevant candidates from a similarity-sorted result list.

        Args:
            results: Search results ordered by descending similarity.

        Returns:
            Filtered results, still in descending similarity order.
        """
        if not results:
            return []

        scores = [result.similarity for result in results]
        if scores[0] < self.config.min_similarity:
            return []

        keep_index = self._find_keep_index(scores)
        keep_index = max(keep_index, self.config.min_keep)

        kept: list[SearchResult] = []
        for result in results[:keep_index]:
            if result.similarity >= self.config.min_similarity:
                kept.append(result)
            if len(kept) >= self.config.max_results:
                break

        logger.debug(
            "SimilarityResultFilter kept %d/%d candidates "
            "(target_drop_ratio=%.2f, min_keep=%d, min_similarity=%.2f)",
            len(kept),
            len(results),
            self.config.target_drop_ratio,
            self.config.min_keep,
            self.config.min_similarity,
        )
        return kept

    def _find_keep_index(self, scores: list[float]) -> int:
        """Return the number of leading candidates to keep.

        The index is chosen so that the cumulative similarity drop from the top
        score reaches ``target_drop_ratio`` of the total drop.
        """
        total_drop = max(scores[0] - scores[-1], 1e-9)
        cumulative_drop = 0.0

        for index in range(len(scores) - 1):
            cumulative_drop += scores[index] - scores[index + 1]
            if cumulative_drop >= self.config.target_drop_ratio * total_drop:
                return index + 1

        return len(scores)


def create_filter_from_settings(
    settings_manager: ConfigManager,
) -> SimilarityResultFilter:
    """Create a filter using the current application settings."""
    config = SimilarityFilterConfig(
        target_drop_ratio=settings_manager.get(
            "similar_image_target_drop_ratio"
        ),
        min_keep=int(settings_manager.get("similar_image_min_keep")),
        min_similarity=settings_manager.get("similar_image_min_similarity"),
        max_results=int(settings_manager.get("similar_image_max_results")),
    )
    return SimilarityResultFilter(config)
