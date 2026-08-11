# coding=utf-8
"""Create plotting-friendly distribution CSVs and a report from analysis CSVs.

Inputs come from ``similarity_distribution_analysis.py``:
    query_summary.csv
    visual_duplicate_clusters.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import numpy as np


THRESHOLD_FIELDS = [
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
]
BUCKETS = (("0", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3-5", 3, 5),
           ("6-20", 6, 20), ("21-100", 21, 100), ("101-200", 101, 200))


def _bucket(count: int) -> str:
    for label, low, high in BUCKETS:
        if low <= count <= high:
            return label
    return "101-200"


def _quantile(values: np.ndarray, percentile: float) -> float:
    return float(np.percentile(values, percentile))


def _write_distribution_by_threshold(rows: list[dict[str, str]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["threshold", "bucket", "query_count", "percent"])
        for field in THRESHOLD_FIELDS:
            threshold = field.removeprefix("count_ge_")
            counts = Counter(_bucket(int(row[field])) for row in rows)
            total = len(rows)
            for label, _, _ in BUCKETS:
                value = counts.get(label, 0)
                writer.writerow(
                    [
                        threshold,
                        label,
                        value,
                        round(value / total * 100, 2) if total else 0.0,
                    ]
                )


def _write_current_strategy_distribution(
    rows: list[dict[str, str]],
    path: Path,
) -> None:
    counts = Counter(_bucket(int(row["current_strategy_result_count"])) for row in rows)
    total = len(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["bucket", "query_count", "percent"])
        for label, _, _ in BUCKETS:
            value = counts.get(label, 0)
            writer.writerow(
                [label, value, round(value / total * 100, 2) if total else 0.0]
            )


def _summarize_quantiles(rows: list[dict[str, str]]) -> str:
    fields = [
        "top1_similarity",
        "top5_similarity",
        "top10_similarity",
        "top20_similarity",
        "top50_similarity",
        "top100_similarity",
        "top200_similarity",
        "mean_top50_similarity",
        "max_gap",
        "second_max_gap",
    ]
    lines = [
        "| 指标 | min | P25 | P50 | P75 | P95 | max |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for field in fields:
        values = np.array([float(row[field]) for row in rows], dtype=float)
        lines.append(
            f"| {field} | {values.min():.4f} | "
            f"{_quantile(values, 25):.4f} | "
            f"{_quantile(values, 50):.4f} | "
            f"{_quantile(values, 75):.4f} | "
            f"{_quantile(values, 95):.4f} | "
            f"{values.max():.4f} |"
        )
    return "\n".join(lines)


def _summarize_clusters(path: Path) -> str:
    csv.field_size_limit(sys.maxsize)
    with path.open(encoding="utf-8") as handle:
        clusters = list(csv.DictReader(handle))

    lines = [
        "| 阈值 | 簇数 | 成员数 | 大小中位数 | 大小P90 | 最大簇 | >=5个的簇 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for threshold in ("0.95", "0.90", "0.85", "0.80", "0.75", "0.70"):
        subset = [
            int(cluster["size"])
            for cluster in clusters
            if cluster["threshold"] == threshold
        ]
        if not subset:
            lines.append(
                f"| {threshold} | 0 | 0 | - | - | - | 0 |"
            )
            continue
        sizes = np.array(subset, dtype=float)
        lines.append(
            f"| {threshold} | {len(sizes):d} | {int(sizes.sum()):d} | "
            f"{int(np.median(sizes)):d} | {int(np.percentile(sizes, 90)):d} | "
            f"{int(sizes.max()):d} | {int((sizes >= 5).sum()):d} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary",
        type=Path,
        required=True,
        help="path to query_summary.csv from similarity_distribution_analysis.py",
    )
    parser.add_argument(
        "--clusters",
        type=Path,
        required=True,
        help="path to visual_duplicate_clusters.csv",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    with args.summary.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    threshold_path = output_dir / "distribution_by_threshold.csv"
    strategy_path = output_dir / "current_strategy_distribution.csv"
    _write_distribution_by_threshold(rows, threshold_path)
    _write_current_strategy_distribution(rows, strategy_path)

    current_counts = Counter(
        _bucket(int(row["current_strategy_result_count"])) for row in rows
    )
    total = len(rows)
    report_lines = [
        "# 大图库相似度分布分析（SigLIP）",
        "",
        f"- 查询图片数：{total}",
        f"- 每查询候选数：200（不含自身）",
        "- 模型：`siglip_base_patch16_224`，Chroma cosine",
        "",
        "## 相似度曲线分位",
        "",
        _summarize_quantiles(rows),
        "",
        "## 当前落差策略的结果数分布",
        "",
        "| 结果数 | 查询数 | 占比 |",
        "| --- | ---: | ---: |",
    ]
    for label, _, _ in BUCKETS:
        value = current_counts.get(label, 0)
        report_lines.append(
            f"| {label} | {value} | {value / total * 100:.2f}% |"
        )
    report_lines.extend(
        [
            "",
            "## 每个阈值的候选数量分桶",
            "",
            "详细数据见 `distribution_by_threshold.csv`。",
            "",
            "## 双向高相似簇",
            "",
            "这里只统计两个方向都在 top-200 中出现、且相似度达到阈值的边；",
            "可以减少链式传递造成的过度合并。",
            "",
            _summarize_clusters(args.clusters),
            "",
            "## 说明",
            "",
            "- `query_summary.csv` 是每张图一行；`query_scores_top200.csv` 是分数宽表。",
            "- `visual_duplicate_clusters.csv` 里的簇是视觉重复/近重复的连通分量。",
        ]
    )

    report_path = output_dir / "analysis_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"wrote {threshold_path}")
    print(f"wrote {strategy_path}")
    print(f"wrote {report_path}")


if __name__ == "__main__":
    main()
