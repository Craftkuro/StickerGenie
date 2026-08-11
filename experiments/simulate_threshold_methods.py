# coding=utf-8
"""Compare per-query adaptive similarity thresholds on exported score CSVs.

The goal is: for each query, find a single similarity threshold; keep every
candidate whose similarity is >= that threshold. Duplicates / clusters are
intentionally kept, so the method only needs to separate relevant from
irrelevant results.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np


def _bucket(count: int) -> str:
    if count == 0:
        return "0"
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    if count <= 5:
        return "3-5"
    if count <= 20:
        return "6-20"
    if count <= 50:
        return "21-50"
    if count <= 100:
        return "51-100"
    return "101-200"


def _load_scores(path: Path, top_k: int) -> dict[int, list[float]]:
    result: dict[int, list[float]] = {}
    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            sqlite_id = int(row["sqlite_id"])
            scores: list[float] = []
            for rank in range(1, top_k + 1):
                value = row.get(f"score_{rank}", "")
                if value == "":
                    break
                scores.append(float(value))
            result[sqlite_id] = scores
    return result


def threshold_current_max_gap(scores: list[float], min_gap: float) -> tuple[float, int]:
    """Existing strategy: largest absolute gap, with min absolute gap gate."""

    if not scores:
        return 0.0, 0
    gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
    max_gap = max(gaps, default=0.0)
    if max_gap >= min_gap:
        keep = gaps.index(max_gap) + 1
    elif scores[0] < 0.4:
        return 0.0, 0
    else:
        keep = len(scores)
    if keep == 1 and scores[0] < 0.5:
        return 0.0, 0
    kept = [s for s in scores[:keep] if s >= 0.25]
    return (kept[-1] if kept else 0.0), len(kept)


def threshold_relative_max_gap(
    scores: list[float],
    min_relative_gap: float,
    min_similarity: float,
) -> tuple[float, int]:
    """Largest relative gap: gap_i / score_i."""

    if not scores:
        return 0.0, 0
    if scores[0] < min_similarity:
        return 0.0, 0
    relative_gaps = [
        (scores[i] - scores[i + 1]) / max(scores[i], 1e-6)
        for i in range(len(scores) - 1)
    ]
    if not relative_gaps:
        return scores[0], 1
    max_rg = max(relative_gaps)
    if max_rg < min_relative_gap:
        # No clear elbow; keep a conservative upper portion.
        keep = len(scores)
    else:
        keep = relative_gaps.index(max_rg) + 1
    kept = [s for s in scores[:keep] if s >= min_similarity]
    return (kept[-1] if kept else 0.0), len(kept)


def threshold_curvature(
    scores: list[float],
    min_similarity: float,
) -> tuple[float, int]:
    """Maximum second difference (discrete curvature)."""

    if not scores:
        return 0.0, 0
    if scores[0] < min_similarity:
        return 0.0, 0
    if len(scores) < 3:
        return scores[-1], len(scores)
    curvature = [
        (scores[i - 1] - scores[i]) - (scores[i] - scores[i + 1])
        for i in range(1, len(scores) - 1)
    ]
    max_curv = max(curvature)
    # +1 because curvature[i] corresponds to scores[i+1]
    keep = curvature.index(max_curv) + 2
    kept = [s for s in scores[:keep] if s >= min_similarity]
    return (kept[-1] if kept else 0.0), len(kept)


def threshold_kneedle(
    scores: list[float],
    min_similarity: float,
) -> tuple[float, int]:
    """Kneedle: distance from the diagonal of the normalized score curve."""

    if not scores:
        return 0.0, 0
    if scores[0] < min_similarity:
        return 0.0, 0
    lo, hi = min(scores), max(scores)
    if hi - lo < 0.001:
        return scores[-1], len(scores)
    y = [(s - lo) / (hi - lo) for s in scores]
    n = len(y)
    x = [i / (n - 1) for i in range(n)]
    # Distance from line y = x in the normalized space.
    distances = [yi - xi for yi, xi in zip(y, x)]
    knee = int(np.argmax(distances))
    keep = knee + 1
    kept = [s for s in scores[:keep] if s >= min_similarity]
    return (kept[-1] if kept else 0.0), len(kept)


def threshold_mean_minus_k_std(
    scores: list[float],
    k: float,
    max_keep: int,
    min_similarity: float,
) -> tuple[float, int]:
    """threshold = mean(top-N) - k*std(top-N); keep all above it."""

    if not scores:
        return 0.0, 0
    window = scores[:max_keep]
    if not window:
        return 0.0, 0
    mean = float(np.mean(window))
    std = float(np.std(window))
    threshold = mean - k * std
    threshold = max(threshold, min_similarity)
    count = sum(1 for s in scores if s >= threshold)
    return threshold, count


def threshold_gap_zscore(
    scores: list[float],
    z: float,
    min_similarity: float,
) -> tuple[float, int]:
    """Find the first gap that is z standard deviations above mean gap."""

    if not scores or len(scores) < 2:
        return 0.0, 0
    gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
    mean_gap = float(np.mean(gaps))
    std_gap = float(np.std(gaps))
    cutoff = mean_gap + z * std_gap
    for i, gap in enumerate(gaps):
        if gap >= cutoff:
            threshold = scores[i + 1] + gap / 2
            threshold = max(threshold, min_similarity)
            count = sum(1 for s in scores if s >= threshold)
            return threshold, count
    # No significant gap; fall back to a conservative threshold.
    threshold = max(scores[-1], min_similarity)
    count = sum(1 for s in scores if s >= threshold)
    return threshold, count


def threshold_drop_rate_sum(
    scores: list[float],
    target_drop_ratio: float,
    min_similarity: float,
) -> tuple[float, int]:
    """Keep candidates until cumulative relative drop reaches target ratio of total drop."""

    if not scores:
        return 0.0, 0
    if scores[0] < min_similarity:
        return 0.0, 0
    total_drop = max(scores[0] - scores[-1], 0.001)
    cumulative = 0.0
    for i in range(len(scores) - 1):
        cumulative += scores[i] - scores[i + 1]
        if cumulative >= target_drop_ratio * total_drop:
            keep = i + 1
            break
    else:
        keep = len(scores)
    kept = [s for s in scores[:keep] if s >= min_similarity]
    return (kept[-1] if kept else 0.0), len(kept)


def threshold_curvature_with_min_keep(
    scores: list[float],
    min_similarity: float,
    min_keep: int,
) -> tuple[float, int]:
    """Maximum curvature, but always keep at least min_keep if they are relevant."""

    if not scores:
        return 0.0, 0
    if scores[0] < min_similarity:
        return 0.0, 0
    if len(scores) < 3:
        return scores[-1], len(scores)
    curvature = [
        (scores[i - 1] - scores[i]) - (scores[i] - scores[i + 1])
        for i in range(1, len(scores) - 1)
    ]
    keep = curvature.index(max(curvature)) + 2
    keep = max(keep, min_keep)
    kept = [s for s in scores[:keep] if s >= min_similarity]
    return (kept[-1] if kept else 0.0), len(kept)


def threshold_relative_gap_with_min_keep(
    scores: list[float],
    min_relative_gap: float,
    min_similarity: float,
    min_keep: int,
) -> tuple[float, int]:
    """Largest relative gap with a minimum keep count."""

    if not scores:
        return 0.0, 0
    if scores[0] < min_similarity:
        return 0.0, 0
    relative_gaps = [
        (scores[i] - scores[i + 1]) / max(scores[i], 1e-6)
        for i in range(len(scores) - 1)
    ]
    if not relative_gaps:
        return scores[0], 1
    max_rg = max(relative_gaps)
    if max_rg < min_relative_gap:
        keep = len(scores)
    else:
        keep = relative_gaps.index(max_rg) + 1
    keep = max(keep, min_keep)
    kept = [s for s in scores[:keep] if s >= min_similarity]
    return (kept[-1] if kept else 0.0), len(kept)


def threshold_drop_rate_with_min_keep(
    scores: list[float],
    target_drop_ratio: float,
    min_similarity: float,
    min_keep: int,
) -> tuple[float, int]:
    """Keep candidates until cumulative relative drop reaches target ratio of total drop."""

    if not scores:
        return 0.0, 0
    if scores[0] < min_similarity:
        return 0.0, 0
    total_drop = max(scores[0] - scores[-1], 0.001)
    cumulative = 0.0
    for i in range(len(scores) - 1):
        cumulative += scores[i] - scores[i + 1]
        if cumulative >= target_drop_ratio * total_drop:
            keep = i + 1
            break
    else:
        keep = len(scores)
    keep = max(keep, min_keep)
    kept = [s for s in scores[:keep] if s >= min_similarity]
    return (kept[-1] if kept else 0.0), len(kept)


def threshold_two_phase(
    scores: list[float],
    high_floor: float,
    gap_factor: float,
    min_similarity: float,
) -> tuple[float, int]:
    """Hybrid: if top score is high, require a stronger gap; otherwise use normal gap."""

    if not scores:
        return 0.0, 0
    if scores[0] < min_similarity:
        return 0.0, 0
    gaps = [scores[i] - scores[i + 1] for i in range(len(scores) - 1)]
    if not gaps:
        return scores[0], 1
    # Adaptive gap: tighter when scores are high, looser when low.
    base_gap = 0.02
    if scores[0] >= high_floor:
        required_gap = base_gap * gap_factor
    else:
        required_gap = base_gap
    for i, gap in enumerate(gaps):
        if gap >= required_gap:
            threshold = scores[i + 1] + gap / 2
            count = sum(1 for s in scores if s >= threshold)
            return threshold, count
    threshold = max(scores[-1], min_similarity)
    count = sum(1 for s in scores if s >= threshold)
    return threshold, count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=200)
    args = parser.parse_args()

    scores_path = args.base_dir / f"query_scores_top{args.top_k}.csv"
    scores_by_id = _load_scores(scores_path, args.top_k)
    ids = sorted(scores_by_id)

    methods: dict[str, tuple[callable, dict]] = {
        "current_max_gap_0.02": (
            threshold_current_max_gap,
            {"min_gap": 0.02},
        ),
        "relative_gap_0.02_min0.40": (
            threshold_relative_max_gap,
            {"min_relative_gap": 0.02, "min_similarity": 0.40},
        ),
        "relative_gap_0.03_min0.40": (
            threshold_relative_max_gap,
            {"min_relative_gap": 0.03, "min_similarity": 0.40},
        ),
        "relative_gap_0.05_min0.40": (
            threshold_relative_max_gap,
            {"min_relative_gap": 0.05, "min_similarity": 0.40},
        ),
        "curvature_min0.40": (
            threshold_curvature,
            {"min_similarity": 0.40},
        ),
        "kneedle_min0.40": (
            threshold_kneedle,
            {"min_similarity": 0.40},
        ),
        "mean_minus_1std_top100_min0.40": (
            threshold_mean_minus_k_std,
            {"k": 1.0, "max_keep": 100, "min_similarity": 0.40},
        ),
        "mean_minus_1.5std_top100_min0.40": (
            threshold_mean_minus_k_std,
            {"k": 1.5, "max_keep": 100, "min_similarity": 0.40},
        ),
        "mean_minus_2std_top100_min0.40": (
            threshold_mean_minus_k_std,
            {"k": 2.0, "max_keep": 100, "min_similarity": 0.40},
        ),
        "gap_zscore_2_min0.40": (
            threshold_gap_zscore,
            {"z": 2.0, "min_similarity": 0.40},
        ),
        "gap_zscore_3_min0.40": (
            threshold_gap_zscore,
            {"z": 3.0, "min_similarity": 0.40},
        ),
        "drop_rate_0.5_min0.40": (
            threshold_drop_rate_sum,
            {"target_drop_ratio": 0.5, "min_similarity": 0.40},
        ),
        "drop_rate_0.7_min0.40": (
            threshold_drop_rate_sum,
            {"target_drop_ratio": 0.7, "min_similarity": 0.40},
        ),
        "drop_rate_0.3_min0.40": (
            threshold_drop_rate_sum,
            {"target_drop_ratio": 0.3, "min_similarity": 0.40},
        ),
        "drop_rate_0.4_min0.40": (
            threshold_drop_rate_sum,
            {"target_drop_ratio": 0.4, "min_similarity": 0.40},
        ),
        "drop_rate_0.6_min0.40": (
            threshold_drop_rate_sum,
            {"target_drop_ratio": 0.6, "min_similarity": 0.40},
        ),
        "drop_rate_0.5_min0.40_mink5": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.5, "min_similarity": 0.40, "min_keep": 5},
        ),
        "drop_rate_0.5_min0.40_mink10": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.5, "min_similarity": 0.40, "min_keep": 10},
        ),
        "drop_rate_0.6_min0.40_mink5": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.6, "min_similarity": 0.40, "min_keep": 5},
        ),
        "curvature_min0.40_mink5": (
            threshold_curvature_with_min_keep,
            {"min_similarity": 0.40, "min_keep": 5},
        ),
        "curvature_min0.40_mink10": (
            threshold_curvature_with_min_keep,
            {"min_similarity": 0.40, "min_keep": 10},
        ),
        "relative_gap_0.03_min0.40_mink5": (
            threshold_relative_gap_with_min_keep,
            {"min_relative_gap": 0.03, "min_similarity": 0.40, "min_keep": 5},
        ),
        "relative_gap_0.05_min0.40_mink5": (
            threshold_relative_gap_with_min_keep,
            {"min_relative_gap": 0.05, "min_similarity": 0.40, "min_keep": 5},
        ),
        "drop_rate_0.5_min0.70_mink5": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.5, "min_similarity": 0.70, "min_keep": 5},
        ),
        "drop_rate_0.5_min0.75_mink5": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.5, "min_similarity": 0.75, "min_keep": 5},
        ),
        "drop_rate_0.5_min0.80_mink5": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.5, "min_similarity": 0.80, "min_keep": 5},
        ),
        "drop_rate_0.5_min0.70_mink10": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.5, "min_similarity": 0.70, "min_keep": 10},
        ),
        "drop_rate_0.5_min0.75_mink10": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.5, "min_similarity": 0.75, "min_keep": 10},
        ),
        "drop_rate_0.4_min0.70_mink5": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.4, "min_similarity": 0.70, "min_keep": 5},
        ),
        "drop_rate_0.4_min0.75_mink5": (
            threshold_drop_rate_with_min_keep,
            {"target_drop_ratio": 0.4, "min_similarity": 0.75, "min_keep": 5},
        ),
        "two_phase_high0.95_factor2_min0.40": (
            threshold_two_phase,
            {"high_floor": 0.95, "gap_factor": 2.0, "min_similarity": 0.40},
        ),
    }

    results: dict[str, list[tuple[int, float]]] = {
        name: [] for name in methods
    }
    for sqlite_id in ids:
        scores = scores_by_id[sqlite_id]
        for name, (fn, kwargs) in methods.items():
            _, count = fn(scores, **kwargs)
            results[name].append((sqlite_id, count))

    rows = []
    for name, pairs in results.items():
        counts = [c for _, c in pairs]
        dist = Counter(_bucket(c) for c in counts)
        rows.append(
            {
                "method": name,
                "mean": round(float(np.mean(counts)), 2),
                "median": int(np.median(counts)),
                "min": min(counts),
                "max": max(counts),
                **{f"bucket_{k}": dist.get(k, 0) for k in ["0", "1", "2", "3-5", "6-20", "21-50", "51-100", "101-200"]},
            }
        )

    out_csv = args.base_dir / "threshold_method_distribution.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    # Examples for qualitative inspection.
    example_ids = [1, 2, 3, 4, 5, 28, 33, 41, 3200]
    example_rows = []
    for sqlite_id in example_ids:
        scores = scores_by_id[sqlite_id]
        row = {"sqlite_id": sqlite_id}
        for name, (fn, kwargs) in methods.items():
            threshold, count = fn(scores, **kwargs)
            row[f"{name}_threshold"] = round(threshold, 4)
            row[f"{name}_count"] = count
        example_rows.append(row)

    example_csv = args.base_dir / "threshold_method_examples.csv"
    with example_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=example_rows[0].keys())
        writer.writeheader()
        writer.writerows(example_rows)

    out_json = args.base_dir / "threshold_method_summary.json"
    summary = {
        name: {
            "bucket_distribution": dict(Counter(_bucket(c) for _, c in pairs)),
            "mean": round(float(np.mean([c for _, c in pairs])), 2),
            "median": int(np.median([c for _, c in pairs])),
        }
        for name, pairs in results.items()
    }
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {out_csv}")
    print(f"wrote {example_csv}")
    print(f"wrote {out_json}")


if __name__ == "__main__":
    main()
