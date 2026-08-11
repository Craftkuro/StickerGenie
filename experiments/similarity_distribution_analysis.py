# coding=utf-8
"""Export similarity-query distributions for a StickerGenie library.

The script intentionally mirrors how the app searches: cosine distance from
Chroma, similarity = max(0, 1 - distance), and the query's own vector excluded
from the returned candidates.

Outputs (all UTF-8, comma-separated):
    query_summary.csv        one row per library image with curve/gap stats
    query_scores_topN.csv    wide table: score_1 .. score_N per query
    query_candidates_topN.csv
                             wide table: candidate sqlite id per rank
    visual_duplicate_clusters.csv
                             connected components from high-sim edges
    analysis_meta.json       global summary numbers
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import chromadb
import numpy as np
from chromadb.config import Settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from commons.constants import (
    SIMILAR_IMAGE_CANDIDATE_COUNT,
    SIMILAR_IMAGE_LONE_RESULT_MIN_SIMILARITY,
    SIMILAR_IMAGE_MAX_RESULTS,
    SIMILAR_IMAGE_MIN_GAP,
    SIMILAR_IMAGE_MIN_SIMILARITY,
    SIMILAR_IMAGE_NO_GAP_MIN_TOP_SIMILARITY,
)


COLLECTION_NAME = "sticker_features_v1"
CLUSTER_THRESHOLDS = (0.95, 0.90, 0.85, 0.80, 0.75, 0.70)


def _similarity(distance: float) -> float:
    """Same conversion used by the application's SearchResult.similarity."""

    return max(0.0, 1.0 - float(distance))


def current_strategy_count(scores: list[float]) -> int:
    """Mirror of viewer_service._select_similar_count for analysis."""

    if not scores:
        return 0

    gaps = [
        scores[index] - scores[index + 1]
        for index in range(len(scores) - 1)
    ]
    max_gap = max(gaps, default=0.0)

    if max_gap >= SIMILAR_IMAGE_MIN_GAP:
        keep_count = gaps.index(max_gap) + 1
    elif scores[0] < SIMILAR_IMAGE_NO_GAP_MIN_TOP_SIMILARITY:
        return 0
    else:
        keep_count = len(scores)

    kept_scores = [
        score
        for score in scores[:keep_count]
        if score >= SIMILAR_IMAGE_MIN_SIMILARITY
    ]
    if (
        len(kept_scores) == 1
        and kept_scores[0] < SIMILAR_IMAGE_LONE_RESULT_MIN_SIMILARITY
    ):
        return 0
    return min(len(kept_scores), SIMILAR_IMAGE_MAX_RESULTS)


def _count_ge(scores: list[float], threshold: float) -> int:
    return sum(1 for score in scores if score >= threshold)


def _percentile_at(scores: list[float], rank: int) -> float:
    if not scores or rank <= 0:
        return 0.0
    index = rank - 1
    if index < len(scores):
        return scores[index]
    return scores[-1] if scores else 0.0


def _gap_summary(scores: list[float]) -> tuple[float, int, float, int]:
    gaps = [
        (scores[index] - scores[index + 1], index + 1)
        for index in range(len(scores) - 1)
    ]
    gaps.sort(reverse=True)
    if not gaps:
        return 0.0, 0, 0.0, 0
    max_gap, max_rank = gaps[0]
    if len(gaps) == 1:
        return max_gap, max_rank, 0.0, 0
    second_gap, second_rank = gaps[1]
    return max_gap, max_rank, second_gap, second_rank


def _load_collection(
    library_path: Path,
) -> tuple[chromadb.Collection, list[dict[str, Any]]]:
    vectors_path = library_path / "vectors"
    if not vectors_path.exists():
        raise SystemExit(f"vectors directory not found: {vectors_path}")

    client = chromadb.PersistentClient(
        path=str(vectors_path),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )
    collection = client.get_collection(COLLECTION_NAME)
    data = collection.get(include=["embeddings", "metadatas"])

    rows: list[dict[str, Any]] = []
    for vector_id, metadata, embedding in zip(
        data["ids"],
        data["metadatas"],
        data["embeddings"],
    ):
        rows.append(
            {
                "id": vector_id,
                "sqlite_id": int(metadata["sqlite_id"]),
                "filename": str(metadata["image_filename"]),
                "model_hash": str(metadata["model_hash"]),
                "embedding": np.asarray(embedding, dtype=np.float32),
            }
        )
    rows.sort(key=lambda row: (row["sqlite_id"], row["id"]))
    return collection, rows


def _query_batch(
    collection: chromadb.Collection,
    rows: list[dict[str, Any]],
    top_k: int,
) -> list[tuple[dict[str, Any], list[tuple[str, int, str, float]]]]:
    model_hashes = {row["model_hash"] for row in rows}
    where = {"model_hash": next(iter(model_hashes))} if len(model_hashes) == 1 else None
    query_options = {"where": where} if where else {}

    result = collection.query(
        query_embeddings=[row["embedding"].tolist() for row in rows],
        n_results=top_k + 1,
        include=["distances", "metadatas"],
        **query_options,
    )

    parsed: list[tuple[dict[str, Any], list[tuple[str, int, str, float]]]] = []
    for query_row, ids, distances, metadatas in zip(
        rows,
        result["ids"],
        result["distances"],
        result["metadatas"],
    ):
        candidates: list[tuple[str, int, str, float]] = []
        for candidate_id, distance, metadata in zip(ids, distances, metadatas):
            if candidate_id == query_row["id"]:
                continue
            candidates.append(
                (
                    str(candidate_id),
                    int(metadata["sqlite_id"]),
                    str(metadata["image_filename"]),
                    _similarity(float(distance)),
                )
            )
        parsed.append((query_row, candidates[:top_k]))
    return parsed


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, value: int) -> int:
        parent = self.parent.get(value)
        if parent is None:
            self.parent[value] = value
            return value
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _write_cluster_csv(
    path: Path,
    edges_by_threshold: dict[float, dict[tuple[int, int], int]],
    filename_by_sqlite_id: dict[int, str],
) -> None:
    rows: list[dict[str, Any]] = []
    for threshold, edges in edges_by_threshold.items():
        uf = _UnionFind()
        # Require the high-similarity edge in both directions. This keeps the
        # clusters close to "visual duplicates" instead of chaining unrelated
        # images together through one-directional top-k hits.
        for (left, right), direction_count in edges.items():
            if direction_count < 2:
                continue
            uf.union(left, right)

        members: dict[int, list[int]] = {}
        for sqlite_id in uf.parent:
            members.setdefault(uf.find(sqlite_id), []).append(sqlite_id)

        cluster_id = 1
        for root in sorted(members, key=lambda r: (len(members[r]), r), reverse=True):
            member_ids = sorted(members[root])
            if len(member_ids) < 2:
                continue
            rows.append(
                {
                    "threshold": f"{threshold:.2f}",
                    "cluster_id": f"T{threshold:.2f}_{cluster_id:05d}",
                    "size": len(member_ids),
                    "member_sqlite_ids": ";".join(str(value) for value in member_ids),
                    "member_filenames": "|".join(
                        filename_by_sqlite_id.get(value, "")
                        for value in member_ids
                    ),
                }
            )
            cluster_id += 1

    rows.sort(
        key=lambda row: (
            float(row["threshold"]),
            row["size"],
            row["cluster_id"],
        ),
        reverse=True,
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "threshold",
                "cluster_id",
                "size",
                "member_sqlite_ids",
                "member_filenames",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--library",
        type=Path,
        default=Path(
            r"C:\Users\user\Downloads\StickerGenie Library Large\Default Library"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            r"C:\Users\user\Documents\StickerGenie\experiments"
            r"\similarity_large_library_siglip_2026-08-11"
        ),
    )
    parser.add_argument("--top-k", type=int, default=SIMILAR_IMAGE_CANDIDATE_COUNT)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-queries", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    top_k = args.top_k
    if top_k <= 0:
        raise SystemExit("--top-k must be positive")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print("loading chroma collection...", flush=True)
    started = time.monotonic()
    collection, all_rows = _load_collection(args.library)
    if args.max_queries > 0:
        all_rows = all_rows[: args.max_queries]
    print(
        f"collection count={collection.count()} queries={len(all_rows)} "
        f"top_k={top_k}",
        flush=True,
    )

    filename_by_sqlite_id = {
        row["sqlite_id"]: row["filename"] for row in all_rows
    }
    scores_path = output_dir / f"query_scores_top{top_k}.csv"
    candidates_path = output_dir / f"query_candidates_top{top_k}.csv"
    summary_path = output_dir / "query_summary.csv"
    clusters_path = output_dir / "visual_duplicate_clusters.csv"

    summary_fields = [
        "sqlite_id",
        "original_file_name",
        "candidate_count",
        "top1_similarity",
        "top5_similarity",
        "top10_similarity",
        "top20_similarity",
        "top50_similarity",
        "top100_similarity",
        "top200_similarity",
        "mean_top50_similarity",
        "max_gap",
        "max_gap_rank",
        "second_max_gap",
        "second_max_gap_rank",
        "count_ge_0.99",
        "count_ge_0.98",
        "count_ge_0.95",
        "count_ge_0.92",
        "count_ge_0.90",
        "count_ge_0.85",
        "count_ge_0.80",
        "count_ge_0.75",
        "count_ge_0.70",
        "count_ge_0.60",
        "count_ge_0.50",
        "count_ge_0.40",
        "count_ge_0.35",
        "count_ge_0.30",
        "current_strategy_result_count",
    ]
    rank_columns = [f"score_{rank}" for rank in range(1, top_k + 1)]
    candidate_rank_columns = [
        f"candidate_sqlite_id_{rank}" for rank in range(1, top_k + 1)
    ]

    edges_by_threshold: dict[float, dict[tuple[int, int], int]] = {
        threshold: {} for threshold in CLUSTER_THRESHOLDS
    }
    query_count = 0
    current_count_distribution: dict[int, int] = {}
    top1_values: list[float] = []

    with (
        scores_path.open("w", newline="", encoding="utf-8") as scores_handle,
        candidates_path.open("w", newline="", encoding="utf-8") as candidates_handle,
        summary_path.open("w", newline="", encoding="utf-8") as summary_handle,
    ):
        scores_writer = csv.writer(scores_handle)
        candidates_writer = csv.writer(candidates_handle)
        summary_writer = csv.DictWriter(summary_handle, fieldnames=summary_fields)
        scores_writer.writerow(["sqlite_id", "original_file_name", *rank_columns])
        candidates_writer.writerow(
            ["sqlite_id", "original_file_name", *candidate_rank_columns]
        )
        summary_writer.writeheader()

        for offset in range(0, len(all_rows), args.batch_size):
            batch_rows = all_rows[offset : offset + args.batch_size]
            parsed = _query_batch(collection, batch_rows, top_k)

            for query_row, candidates in parsed:
                sqlite_id = query_row["sqlite_id"]
                filename = query_row["filename"]
                scores = [candidate[3] for candidate in candidates]
                candidate_ids = [candidate[1] for candidate in candidates]

                scores_writer.writerow(
                    [sqlite_id, filename, *scores[:top_k]]
                    + [""] * (top_k - len(scores))
                )
                candidates_writer.writerow(
                    [sqlite_id, filename, *candidate_ids[:top_k]]
                    + [""] * (top_k - len(candidate_ids))
                )

                max_gap, max_gap_rank, second_gap, second_gap_rank = _gap_summary(
                    scores
                )
                top50 = scores[:50]
                summary_row = {
                    "sqlite_id": sqlite_id,
                    "original_file_name": filename,
                    "candidate_count": len(scores),
                    "top1_similarity": _percentile_at(scores, 1),
                    "top5_similarity": _percentile_at(scores, 5),
                    "top10_similarity": _percentile_at(scores, 10),
                    "top20_similarity": _percentile_at(scores, 20),
                    "top50_similarity": _percentile_at(scores, 50),
                    "top100_similarity": _percentile_at(scores, 100),
                    "top200_similarity": _percentile_at(scores, 200),
                    "mean_top50_similarity": (
                        float(np.mean(top50)) if top50 else 0.0
                    ),
                    "max_gap": max_gap,
                    "max_gap_rank": max_gap_rank,
                    "second_max_gap": second_gap,
                    "second_max_gap_rank": second_gap_rank,
                    "count_ge_0.99": _count_ge(scores, 0.99),
                    "count_ge_0.98": _count_ge(scores, 0.98),
                    "count_ge_0.95": _count_ge(scores, 0.95),
                    "count_ge_0.92": _count_ge(scores, 0.92),
                    "count_ge_0.90": _count_ge(scores, 0.90),
                    "count_ge_0.85": _count_ge(scores, 0.85),
                    "count_ge_0.80": _count_ge(scores, 0.80),
                    "count_ge_0.75": _count_ge(scores, 0.75),
                    "count_ge_0.70": _count_ge(scores, 0.70),
                    "count_ge_0.60": _count_ge(scores, 0.60),
                    "count_ge_0.50": _count_ge(scores, 0.50),
                    "count_ge_0.40": _count_ge(scores, 0.40),
                    "count_ge_0.35": _count_ge(scores, 0.35),
                    "count_ge_0.30": _count_ge(scores, 0.30),
                    "current_strategy_result_count": current_strategy_count(scores),
                }
                summary_writer.writerow(summary_row)
                current_count_distribution[
                    summary_row["current_strategy_result_count"]
                ] = current_count_distribution.get(
                    summary_row["current_strategy_result_count"], 0
                ) + 1
                if scores:
                    top1_values.append(scores[0])

                for threshold in CLUSTER_THRESHOLDS:
                    for candidate_id, similarity in zip(candidate_ids, scores):
                        if similarity >= threshold:
                            pair = (
                                (sqlite_id, candidate_id)
                                if sqlite_id < candidate_id
                                else (candidate_id, sqlite_id)
                            )
                            edges_by_threshold[threshold][pair] = (
                                edges_by_threshold[threshold].get(pair, 0) + 1
                            )

            query_count += len(batch_rows)
            elapsed = time.monotonic() - started
            print(
                f"processed {query_count}/{len(all_rows)} "
                f"elapsed={elapsed:.1f}s "
                f"rate={query_count / elapsed:.1f} qps",
                flush=True,
            )

    _write_cluster_csv(
        clusters_path,
        edges_by_threshold,
        filename_by_sqlite_id,
    )

    meta = {
        "library": str(args.library),
        "vector_count": collection.count(),
        "query_count": query_count,
        "top_k": top_k,
        "model_hashes": sorted({row["model_hash"] for row in all_rows}),
        "current_strategy_result_count_distribution": {
            str(key): value
            for key, value in sorted(current_count_distribution.items())
        },
        "top1_similarity": {
            "mean": float(np.mean(top1_values)) if top1_values else 0.0,
            "p5": float(np.percentile(top1_values, 5)) if top1_values else 0.0,
            "p25": float(np.percentile(top1_values, 25)) if top1_values else 0.0,
            "p50": float(np.percentile(top1_values, 50)) if top1_values else 0.0,
            "p75": float(np.percentile(top1_values, 75)) if top1_values else 0.0,
            "p95": float(np.percentile(top1_values, 95)) if top1_values else 0.0,
        },
        "output_files": {
            "summary": str(summary_path),
            "scores": str(scores_path),
            "candidates": str(candidates_path),
            "clusters": str(clusters_path),
        },
    }
    (output_dir / "analysis_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"done in {time.monotonic() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
