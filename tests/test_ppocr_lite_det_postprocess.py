# coding=utf-8
import unittest

import numpy as np

from ppocr_lite.det_postprocess import (
    boxes_from_prob_map,
    dilate_2x2,
    extract_component_point_sets,
    filter_det_res,
    polygon_area,
    polygon_mean_score,
    polygon_perimeter,
    sorted_boxes,
)


def make_prob_map(size=(100, 100), blocks=(), background=0.01, value=0.9):
    prob = np.full(size, background, dtype=np.float32)
    for y0, y1, x0, x1 in blocks:
        prob[y0:y1, x0:x1] = value
    return prob


class DilateTests(unittest.TestCase):
    def test_single_pixel_expands_up_left(self):
        mask = np.zeros((5, 5), dtype=bool)
        mask[2, 2] = True
        dilated = dilate_2x2(mask)
        expected = np.zeros_like(mask)
        for dy, dx in ((0, 0), (-1, 0), (0, -1), (-1, -1)):
            y, x = 2 + dy, 2 + dx
            if 0 <= y < 5 and 0 <= x < 5:
                expected[y, x] = True
        np.testing.assert_array_equal(expected, dilated)

    def test_preserves_original(self):
        mask = np.zeros((4, 6), dtype=bool)
        mask[1:3, 2:5] = True
        np.testing.assert_array_equal(True, dilate_2x2(mask)[1:3, 2:5])


class ComponentExtractionTests(unittest.TestCase):
    def test_two_separated_blobs(self):
        mask = np.zeros((40, 40), dtype=bool)
        mask[5:10, 3:12] = True
        mask[20:26, 30:38] = True
        components = extract_component_point_sets(mask)
        self.assertEqual(2, len(components))

    def test_diagonal_touch_is_one_component(self):
        mask = np.zeros((6, 6), dtype=bool)
        mask[1, 1] = True
        mask[2, 2] = True
        components = extract_component_point_sets(mask)
        self.assertEqual(1, len(components))

    def test_point_sets_contain_row_extremes(self):
        mask = np.zeros((10, 20), dtype=bool)
        mask[4, 5:15] = True
        components = extract_component_point_sets(mask)
        points = components[0]
        row_points = points[points[:, 1] == 4]
        self.assertIn(5.0, row_points[:, 0])
        self.assertIn(14.0, row_points[:, 0])


class PolygonMathTests(unittest.TestCase):
    def test_shoelace_area_and_perimeter(self):
        box = np.array([[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0]])
        self.assertAlmostEqual(12.0, polygon_area(box))
        self.assertAlmostEqual(14.0, polygon_perimeter(box))


class ScoreTests(unittest.TestCase):
    def test_polygon_mean_score_inside_block(self):
        prob = make_prob_map(blocks=[(20, 30, 10, 25)])
        box = np.array(
            [[10.0, 20.0], [24.0, 20.0], [24.0, 29.0], [10.0, 29.0]], dtype=np.float64
        )
        score = polygon_mean_score(prob, box)
        self.assertAlmostEqual(0.9, score, delta=0.01)

    def test_low_value_region_filtered_by_box_thresh(self):
        prob = make_prob_map(
            size=(60, 60),
            blocks=[(10, 16, 10, 30)],
            value=0.35,
        )
        boxes, scores = boxes_from_prob_map(
            prob,
            (60, 60),
            thresh=0.3,
            box_thresh=0.5,
            max_candidates=1000,
            unclip_ratio=1.6,
            use_dilation=True,
        )
        self.assertEqual([], list(boxes))
        self.assertEqual([], scores)


class BoxesFromProbMapTests(unittest.TestCase):
    def _extract(self, prob, dest_shape=None):
        return boxes_from_prob_map(
            prob,
            dest_shape or prob.shape,
            thresh=0.3,
            box_thresh=0.5,
            max_candidates=1000,
            unclip_ratio=1.6,
            use_dilation=True,
        )

    def test_two_blocks_produce_two_ordered_boxes(self):
        prob = make_prob_map(blocks=[(10, 22, 8, 40), (50, 70, 30, 80)])
        boxes, scores = self._extract(prob)

        self.assertEqual(2, len(scores))
        for score in scores:
            self.assertGreater(score, 0.8)

        # 阅读顺序：y 较小的块在前
        first_center_y = boxes[0][:, 1].mean()
        second_center_y = boxes[1][:, 1].mean()
        self.assertLess(first_center_y, second_center_y)

        # 坐标落在对应块附近（unclip 外扩允许少量超出）
        self.assertLess(first_center_y, 35)
        self.assertGreater(second_center_y, 40)

    def test_same_line_left_to_right_ordering(self):
        prob = make_prob_map(size=(120, 200), blocks=[(40, 60, 100, 150), (42, 62, 10, 60)])
        boxes, _ = self._extract(prob)
        self.assertEqual(2, len(boxes))
        self.assertLess(boxes[0][0][0], boxes[1][0][0])

    def test_edge_block_is_clipped_inside_image(self):
        prob = make_prob_map(size=(64, 64), blocks=[(2, 14, 2, 40)])
        boxes, _ = self._extract(prob)
        self.assertEqual(1, len(boxes))
        self.assertGreaterEqual(int(boxes[0][:, 0].min()), 0)
        self.assertLessEqual(int(boxes[0][:, 1].max()), 63)

    def test_reading_order_line_grouping_threshold_ten(self):
        line1_left = np.array([[[0, 0], [50, 0], [50, 10], [0, 10]]], dtype=np.int32)
        line1_right = np.array([[[60, 5], [110, 5], [110, 15], [60, 15]]], dtype=np.int32)
        line2 = np.array([[[0, 40], [50, 40], [50, 50], [0, 50]]], dtype=np.int32)

        ordered = sorted_boxes(
            np.concatenate([line2, line1_right, line1_left])
        )
        # 行 1（y 差 <10 归同一行）内按 x 排序，行 2 在后
        self.assertTrue((ordered[0] == line1_left[0]).all())
        self.assertTrue((ordered[1] == line1_right[0]).all())
        self.assertTrue((ordered[2] == line2[0]).all())


if __name__ == "__main__":
    unittest.main()
