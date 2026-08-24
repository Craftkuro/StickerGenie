# coding=utf-8
import unittest

import numpy as np

from ppocr_lite.geometry import (
    convex_hull,
    crop_text_region,
    get_mini_box,
    min_area_rect,
    order_points_clockwise,
    solve_homography,
)


class ConvexHullTests(unittest.TestCase):
    def test_square_hull_ignores_interior_points(self):
        points = np.array(
            [
                [0, 0],
                [10, 0],
                [10, 10],
                [0, 10],
                [5, 5],
                [3, 7],
            ],
            dtype=np.float64,
        )
        hull = convex_hull(points)
        self.assertEqual(4, len(hull))
        reconstructed = {tuple(np.round(p, 6)) for p in hull}
        self.assertEqual(
            {(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)}, reconstructed
        )

    def test_collinear_points_reduce_to_endpoints(self):
        points = np.array([[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]], dtype=np.float64)
        hull = convex_hull(points)
        self.assertEqual(2, len(hull))

    def test_duplicate_points_are_fine(self):
        points = np.array([[3, 2]] * 5 + [[1, 1]], dtype=np.float64)
        hull = convex_hull(points)
        self.assertEqual(2, len(hull))


class MinAreaRectTests(unittest.TestCase):
    def test_axis_aligned_pixel_block(self):
        ys, xs = np.mgrid[4:12, 7:20]
        points = np.column_stack((xs.ravel(), ys.ravel())).astype(np.float64)
        rect = min_area_rect(points)

        xs_corner = sorted(p[0] for p in rect)
        ys_corner = sorted(p[1] for p in rect)
        self.assertAlmostEqual(7.0, xs_corner[0])
        self.assertAlmostEqual(19.0, xs_corner[-1])
        self.assertAlmostEqual(4.0, ys_corner[0])
        self.assertAlmostEqual(11.0, ys_corner[-1])

    def test_rotated_line_segment(self):
        points = np.array(
            [[0, 0], [2, 2], [4, 4], [6, 6], [8, 8]], dtype=np.float64
        )
        box, sside = get_mini_box(points)
        self.assertLess(sside, 1e-6)

    def test_rotated_square_area(self):
        angle = np.pi / 6
        center = np.array([50.0, 40.0])
        half = 10.0
        basis = np.array(
            [
                [np.cos(angle), np.sin(angle)],
                [-np.sin(angle), np.cos(angle)],
            ]
        )
        corners = np.array(
            [
                (-half, -half),
                (half, -half),
                (half, half),
                (-half, half),
            ],
            dtype=np.float64,
        )
        points = center + corners @ basis.T

        rect = min_area_rect(points)
        side_lengths = [
            float(np.linalg.norm(rect[(i + 1) % 4] - rect[i])) for i in range(4)
        ]
        self.assertAlmostEqual(20.0, max(side_lengths), places=6)
        self.assertAlmostEqual(20.0, min(side_lengths), places=6)


class GetMiniBoxTests(unittest.TestCase):
    def test_matches_rapidocr_ordering_semantics(self):
        pixel_points = np.array(
            [[10, 0], [11, 0], [10, 5], [11, 5], [30, 0], [31, 0], [30, 5], [31, 5]],
            dtype=np.float64,
        )
        box, sside = get_mini_box(pixel_points)
        self.assertEqual(4, len(box))
        # 长边约 20，短边约 5
        self.assertAlmostEqual(5.0, sside, delta=0.01)

        clockwise = order_points_clockwise(box)
        self.assertAlmostEqual(clockwise[0][0], min(p[0] for p in clockwise))
        self.assertLessEqual(clockwise[0][1], clockwise[3][1])


class OrderPointsClockwiseTests(unittest.TestCase):
    def test_known_quad(self):
        quad = np.array(
            [[30, 20], [10, 10], [25, 35], [15, 30]], dtype=np.float32
        )
        ordered = order_points_clockwise(quad)
        expected = np.array([[10, 10], [30, 20], [25, 35], [15, 30]], dtype=np.float32)
        np.testing.assert_array_equal(expected, ordered)


class HomographyTests(unittest.TestCase):
    def test_identity_correspondences(self):
        pts = np.array([[0, 0], [100, 0], [100, 50], [0, 50]], dtype=np.float64)
        homography = solve_homography(pts, pts)
        mapped = (np.append(pts[0], 1.0) @ homography.T)
        np.testing.assert_allclose(pts[0], mapped[:2] / mapped[2], atol=1e-9)

    def test_translation_mapping_roundtrip(self):
        src = np.array(
            [[5, 7], [45, 9], [43, 39], [6, 41]], dtype=np.float64
        )
        dst = np.array(
            [[0, 0], [40, 0], [40, 30], [0, 30]], dtype=np.float64
        )

        image = np.arange(50 * 60 * 3, dtype=np.uint8).reshape(50, 60, 3)
        cropped = crop_text_region(image, src)

        # 裁剪尺寸来自边长 norm 的 int 截断
        width = int(max(np.linalg.norm(src[0] - src[1]), np.linalg.norm(src[2] - src[3])))
        height = int(max(np.linalg.norm(src[0] - src[3]), np.linalg.norm(src[1] - src[2])))
        self.assertEqual((height, width, 3), cropped.shape)

        # 左上角应精确还原原图像素
        np.testing.assert_array_equal(image[7, 5], cropped[0, 0])

    def test_axis_aligned_box_returns_exact_subregion(self):
        image = np.arange(80 * 100 * 3, dtype=np.uint8).reshape(80, 100, 3)
        box = np.array([[10, 20], [30, 20], [30, 40], [10, 40]], dtype=np.float64)
        cropped = crop_text_region(image, box)
        self.assertEqual((20, 20, 3), cropped.shape)
        np.testing.assert_array_equal(image[20:40, 10:30], cropped)

    def test_out_of_bounds_clamps_to_edge(self):
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        image[:, :] = (10, 20, 30)
        box = np.array([[-10, -5], [15, -5], [15, 10], [-10, 10]], dtype=np.float64)
        cropped = crop_text_region(image, box)
        self.assertEqual(cropped.shape[0], 15)
        np.testing.assert_array_equal(np.full_like(cropped, (10, 20, 30)), cropped)

    def test_tall_crop_gets_rot90(self):
        image = np.full((80, 80, 3), 200, dtype=np.uint8)
        box = np.array([[5, 5], [15, 6], [14, 70], [6, 69]], dtype=np.float64)
        cropped = crop_text_region(image, box)
        # 高宽比 ≥1.5 的裁剪应被 rot90 转为横向
        self.assertGreater(cropped.shape[1], cropped.shape[0])


if __name__ == "__main__":
    unittest.main()
