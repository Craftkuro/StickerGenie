# -*- encoding: utf-8 -*-
"""几何算法：凸包、最小面积旋转矩形、四点顺时针排序、单应逆映射透视裁剪。

替代 cv2 的 minAreaRect/boxPoints 与 getPerspectiveTransform+warpPerspective。
"""

import numpy as np


def convex_hull(points: np.ndarray) -> np.ndarray:
    """Andrew 单调链凸包，输入 [N,2] float，返回 CCW 顺序的顶点。"""
    pts = np.unique(np.asarray(points, dtype=np.float64).reshape(-1, 2), axis=0)
    n = len(pts)
    if n <= 2:
        return pts

    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]
    xs = pts[:, 0]
    ys = pts[:, 1]

    def half(seq_xs, seq_ys):
        stack_x, stack_y = [], []
        for x, y in zip(seq_xs, seq_ys):
            while len(stack_x) >= 2:
                cross = _cross(
                    stack_x[-2], stack_y[-2], stack_x[-1], stack_y[-1], x, y
                )
                if cross > 1e-12:
                    break
                stack_x.pop()
                stack_y.pop()
            stack_x.append(x)
            stack_y.append(y)
        return stack_x, stack_y

    lower_x, lower_y = half(xs, ys)
    upper_x, upper_y = half(xs[::-1], ys[::-1])
    hull_x = lower_x[:-1] + upper_x[:-1]
    hull_y = lower_y[:-1] + upper_y[:-1]
    return np.column_stack((hull_x, hull_y))


def _cross(ox, oy, ax, ay, bx, by):
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)


def min_area_rect(points: np.ndarray) -> np.ndarray:
    """最小面积外接旋转矩形，返回 4x2 角点（顺序无关紧要，调用方会重排）。

    最小面积矩形必有一条边与凸包某条边共线，枚举每条凸包边做投影即可。
    退化输入（共线/单点）返回零面积的退化矩形，由上层 sside 过滤兜底。
    """
    points = np.unique(np.asarray(points, dtype=np.float64).reshape(-1, 2), axis=0)
    hull = convex_hull(points)
    if len(hull) == 1:
        return np.repeat(hull, 4, axis=0)
    if len(hull) == 2:
        return np.array([hull[0], hull[1], hull[1], hull[0]])

    best_area = None
    best_rect = None
    count = len(hull)
    for i in range(count):
        p1 = hull[i]
        p2 = hull[(i + 1) % count]
        edge = p2 - p1
        length = np.hypot(edge[0], edge[1])
        if length < 1e-12:
            continue
        u = edge / length
        v = np.array([-u[1], u[0]])

        proj_u = hull @ u
        proj_v = hull @ v
        width = proj_u.max() - proj_u.min()
        height = proj_v.max() - proj_v.min()
        area = width * height
        if best_area is not None and area >= best_area - 1e-9:
            continue

        rect = np.array(
            [
                proj_u.min() * u + proj_v.min() * v,
                proj_u.max() * u + proj_v.min() * v,
                proj_u.max() * u + proj_v.max() * v,
                proj_u.min() * u + proj_v.max() * v,
            ]
        )
        best_area = area
        best_rect = rect

    if best_rect is None:
        return np.repeat(hull[:1], 4, axis=0)
    return best_rect


def get_mini_box(points: np.ndarray) -> tuple:
    """复刻 rapidocr DBPostProcess.get_mini_boxes：返回 (box[4,2], sside)。"""
    rect = min_area_rect(points)

    rect_width = float(np.linalg.norm(rect[1] - rect[0]))
    rect_height = float(np.linalg.norm(rect[3] - rect[0]))
    sside = min(rect_width, rect_height)

    ordered = sorted(rect.tolist(), key=lambda p: p[0])
    index_1, index_2, index_3, index_4 = 0, 1, 2, 3
    if ordered[1][1] > ordered[0][1]:
        index_1, index_4 = 0, 1
    else:
        index_1, index_4 = 1, 0

    if ordered[3][1] > ordered[2][1]:
        index_2, index_3 = 2, 3
    else:
        index_2, index_3 = 3, 2

    box = np.array(
        [ordered[index_1], ordered[index_2], ordered[index_3], ordered[index_4]],
        dtype=np.float64,
    )
    return box, sside


def order_points_clockwise(pts: np.ndarray) -> np.ndarray:
    """按 x 排序后左右两组再按 y 排序，输出 [tl, tr, br, bl]，复刻 rapidocr。"""
    pts = np.asarray(pts, dtype=np.float32)
    x_sorted = pts[np.argsort(pts[:, 0]), :]

    left_most = x_sorted[:2, :]
    right_most = x_sorted[2:, :]
    left_most = left_most[np.argsort(left_most[:, 1]), :]
    tl, bl = left_most
    right_most = right_most[np.argsort(right_most[:, 1]), :]
    tr, br = right_most

    return np.array([tl, tr, br, bl], dtype="float32")


def solve_homography(dst_pts: np.ndarray, src_pts: np.ndarray) -> np.ndarray:
    """解 dst→src 的单应矩阵（h33=1），8x8 线性方程组。"""
    a_rows = []
    for (x, y), (u, v) in zip(dst_pts, src_pts):
        a_rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -x * u, -y * u])
        a_rows.append([0.0, 0.0, 0.0, x, y, 1.0, -x * v, -y * v])
    b = []
    for (u, v) in src_pts:
        b.extend([u, v])

    h = np.linalg.solve(np.array(a_rows, dtype=np.float64), np.array(b, dtype=np.float64))
    return np.append(h, 1.0).reshape(3, 3)


def crop_text_region(img: np.ndarray, box: np.ndarray) -> np.ndarray:
    """单应逆映射透视裁剪一个文本框，语义对齐 get_rotate_crop_image：
    双线性插值、越界坐标 clamp 到边缘（BORDER_REPLICATE）、高宽比 ≥1.5 时 rot90。"""
    points = np.asarray(box, dtype=np.float64)
    img_crop_width = int(
        max(
            np.linalg.norm(points[0] - points[1]),
            np.linalg.norm(points[2] - points[3]),
        )
    )
    img_crop_height = int(
        max(
            np.linalg.norm(points[0] - points[3]),
            np.linalg.norm(points[1] - points[2]),
        )
    )
    img_crop_width = max(img_crop_width, 1)
    img_crop_height = max(img_crop_height, 1)

    dst_std = np.array(
        [
            [0.0, 0.0],
            [img_crop_width, 0.0],
            [img_crop_width, img_crop_height],
            [0.0, img_crop_height],
        ],
        dtype=np.float64,
    )
    homography = solve_homography(dst_std, points)

    grid_x, grid_y = np.meshgrid(
        np.arange(img_crop_width, dtype=np.float64),
        np.arange(img_crop_height, dtype=np.float64),
    )
    ones = np.ones_like(grid_x)
    coords = np.stack([grid_x, grid_y, ones], axis=-1) @ homography.T
    denom = coords[..., 2:3]
    denom = np.where(np.abs(denom) < 1e-12, 1e-12, denom)
    src_x = coords[..., 0] / denom[..., 0]
    src_y = coords[..., 1] / denom[..., 0]

    src_h, src_w = img.shape[:2]
    src_x = np.clip(src_x, 0.0, src_w - 1.0)
    src_y = np.clip(src_y, 0.0, src_h - 1.0)

    x0 = np.floor(src_x).astype(np.int64)
    y0 = np.floor(src_y).astype(np.int64)
    x1 = np.minimum(x0 + 1, src_w - 1)
    y1 = np.minimum(y0 + 1, src_h - 1)

    fx = (src_x - x0)[..., None]
    fy = (src_y - y0)[..., None]

    top_left = img[y0, x0].astype(np.float32)
    top_right = img[y0, x1].astype(np.float32)
    bottom_left = img[y1, x0].astype(np.float32)
    bottom_right = img[y1, x1].astype(np.float32)

    top = top_left * (1.0 - fx) + top_right * fx
    bottom = bottom_left * (1.0 - fx) + bottom_right * fx
    sampled = top * (1.0 - fy) + bottom * fy

    dst_img = np.clip(sampled + 0.5, 0, 255).astype(np.uint8)
    if dst_img.shape[0] * 1.0 / dst_img.shape[1] >= 1.5:
        dst_img = np.ascontiguousarray(np.rot90(dst_img))
    return dst_img
