# -*- encoding: utf-8 -*-
"""DBNet 检测后处理（无 cv2）：二值化→膨胀→连通域→打分→unclip→阅读顺序排序。"""

from collections import defaultdict

import numpy as np
import pyclipper
from PIL import Image, ImageDraw

from .geometry import get_mini_box, order_points_clockwise

_MINI_BOX_MIN_SIZE = 3
_UNCLIPPED_MIN_SIZE = _MINI_BOX_MIN_SIZE + 2
_BOX_SORT_Y_THRESHOLD = 10


class DetResizeError(Exception):
    pass


def dilate_2x2(mask: np.ndarray) -> np.ndarray:
    """复刻 cv2.dilate 的 2x2 全 1 核（实测语义为向下右扩张，边界按 0 填充）：
    dst(x,y) = src(x,y) | src(x+1,y) | src(x,y+1) | src(x+1,y+1)。"""
    out = mask.copy()
    out[:-1, :] |= mask[1:, :]
    out[:, :-1] |= mask[:, 1:]
    out[:-1, :-1] |= mask[1:, 1:]
    return out


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left: int, right: int):
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _row_runs(row_mask: np.ndarray) -> tuple:
    """一行布尔掩码的游程 [(start_col, end_col_exclusive), ...]。"""
    padded = np.zeros(len(row_mask) + 2, dtype=np.int8)
    padded[1:-1] = row_mask
    diff = np.diff(padded)
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    return zip(starts.tolist(), ends.tolist())


def extract_component_point_sets(mask: np.ndarray) -> list:
    """行游程编码 + 两遍 union-find 连通域标注。

    返回按扫描序（先上后下、先左后右）排列的组件候选点集，
    点集为该组件每行最左/最右像素坐标 [M,2]，凸包与全像素集一致。
    """
    height = mask.shape[0]
    uf = _UnionFind(0)
    run_records = []

    previous_runs = []
    for row in range(height):
        current_runs = []
        for start, end in _row_runs(mask[row]):
            run_id = len(run_records)
            uf.parent.append(run_id)
            run_records.append((row, start, end))
            current_runs.append((start, end, run_id))

        pointer = 0
        for start, end, run_id in current_runs:
            # 8 连通（对齐 cv2.findContours）：列区间相距 ≤1 即合并
            while pointer < len(previous_runs) and previous_runs[pointer][1] < start:
                pointer += 1
            check = pointer
            while check < len(previous_runs) and previous_runs[check][0] <= end:
                uf.union(run_id, previous_runs[check][2])
                check += 1

        previous_runs = current_runs

    extremes = defaultdict(dict)
    for run_id, (row, start, end) in enumerate(run_records):
        component = uf.find(run_id)
        row_extremes = extremes[component]
        if row in row_extremes:
            existing = row_extremes[row]
            existing[0] = min(existing[0], start)
            existing[1] = max(existing[1], end - 1)
        else:
            row_extremes[row] = [start, end - 1]

    components = []
    for row_extremes in extremes.values():
        points = []
        for row in sorted(row_extremes):
            left, right = row_extremes[row]
            points.append((left, row))
            points.append((right, row))
        components.append(np.array(points, dtype=np.float64))
    return components


def polygon_mean_score(prob_map: np.ndarray, box: np.ndarray) -> float:
    """复刻 box_score_fast：框内多边形掩膜的概率均值（PIL 栅格化替代 fillPoly）。"""
    height, width = prob_map.shape[:2]
    xmin = int(np.clip(np.floor(box[:, 0].min()).astype(np.int32), 0, width - 1))
    xmax = int(np.clip(np.ceil(box[:, 0].max()).astype(np.int32), 0, width - 1))
    ymin = int(np.clip(np.floor(box[:, 1].min()).astype(np.int32), 0, height - 1))
    ymax = int(np.clip(np.ceil(box[:, 1].max()).astype(np.int32), 0, height - 1))

    local_box = box.copy()
    local_box[:, 0] -= xmin
    local_box[:, 1] -= ymin

    mask_img = Image.new("L", (xmax - xmin + 1, ymax - ymin + 1), 0)
    ImageDraw.Draw(mask_img).polygon(
        [tuple(point) for point in local_box.astype(np.int32)], fill=1
    )
    mask = np.asarray(mask_img, dtype=bool)
    if not mask.any():
        return 0.0
    return float(prob_map[ymin : ymax + 1, xmin : xmax + 1][mask].mean())


def polygon_perimeter(points: np.ndarray) -> float:
    closed = np.vstack([points, points[:1]])
    return float(np.linalg.norm(closed[1:] - closed[:-1], axis=1).sum())


def polygon_area(points: np.ndarray) -> float:
    """鞋带公式面积。"""
    x = points[:, 0]
    y = points[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2.0


def unclip(box: np.ndarray, unclip_ratio: float):
    """pyclipper 外扩；退化周长或空结果时返回 None。"""
    perimeter = polygon_perimeter(box)
    if perimeter <= 1e-6:
        return None
    distance = polygon_area(box) * unclip_ratio / perimeter
    offset = pyclipper.PyclipperOffset()
    offset.AddPath(box, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    expanded = offset.Execute(distance)
    if not expanded:
        return None
    return np.array(expanded, dtype=np.float64).reshape((-1, 2))


def scale_and_clip_box(box: np.ndarray, map_size: tuple, dest_size: tuple) -> np.ndarray:
    """概率图坐标 → det 输入图坐标，复刻 boxes_from_bitmap 收尾的缩放与 clip。"""
    map_height, map_width = map_size
    dest_width, dest_height = dest_size
    result = box.copy()
    result[:, 0] = np.clip(
        np.round(result[:, 0] / map_width * dest_width), 0, dest_width
    )
    result[:, 1] = np.clip(
        np.round(result[:, 1] / map_height * dest_height), 0, dest_height
    )
    return result


def filter_det_res(
    dt_boxes: list, scores: list, img_height: int, img_width: int
) -> tuple:
    filtered_boxes, filtered_scores = [], []
    for box, score in zip(dt_boxes, scores):
        box = order_points_clockwise(box)
        box[:, 0] = np.clip(box[:, 0], 0, img_width - 1)
        box[:, 1] = np.clip(box[:, 1], 0, img_height - 1)

        rect_width = int(np.linalg.norm(box[0] - box[1]))
        rect_height = int(np.linalg.norm(box[0] - box[3]))
        if rect_width <= 3 or rect_height <= 3:
            continue
        filtered_boxes.append(box)
        filtered_scores.append(score)
    return filtered_boxes, filtered_scores


def sorted_boxes(dt_boxes: np.ndarray) -> np.ndarray:
    """y 轴稳定排序分行（相邻 y 差 ≥10 记新行），行内按 x 排序，复刻 rapidocr。"""
    if len(dt_boxes) == 0:
        return dt_boxes

    y_coords = dt_boxes[:, 0, 1]
    y_order = np.argsort(y_coords, kind="stable")
    boxes_y_sorted = dt_boxes[y_order]
    y_sorted = y_coords[y_order]

    dy = np.diff(y_sorted)
    line_increments = (dy >= _BOX_SORT_Y_THRESHOLD).astype(np.int32)
    line_ids = np.concatenate([[0], np.cumsum(line_increments)])

    x_coords = boxes_y_sorted[:, 0, 0]
    final_order = np.lexsort((x_coords, line_ids))
    return boxes_y_sorted[final_order]


def boxes_from_prob_map(
    prob_map: np.ndarray,
    dest_shape: tuple,
    *,
    thresh: float,
    box_thresh: float,
    max_candidates: int,
    unclip_ratio: float,
    use_dilation: bool,
) -> tuple:
    """概率图 [H,W] → (boxes int32 [N,4,2], scores list[float])，dest_shape 为 det 输入图尺寸。"""
    segmentation = prob_map > thresh
    mask = dilate_2x2(segmentation) if use_dilation else segmentation

    height, width = mask.shape
    dest_height, dest_width = dest_shape

    boxes, scores = [], []
    components = extract_component_point_sets(mask)[:max_candidates]
    for points in components:
        box, sside = get_mini_box(points)
        if sside < _MINI_BOX_MIN_SIZE:
            continue

        score = polygon_mean_score(prob_map, box)
        if box_thresh > score:
            continue

        expanded = unclip(box, unclip_ratio)
        if expanded is None:
            continue
        box, sside = get_mini_box(expanded)
        if sside < _UNCLIPPED_MIN_SIZE:
            continue

        box = scale_and_clip_box(box, (height, width), (dest_width, dest_height))
        boxes.append(box.astype(np.int32))
        scores.append(score)

    boxes, scores = filter_det_res(boxes, scores, dest_height, dest_width)
    if not boxes:
        return [], []
    return sorted_boxes(np.array(boxes, dtype=np.int32)), scores
