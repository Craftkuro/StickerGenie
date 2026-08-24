# -*- encoding: utf-8 -*-
"""方向分类（cls）+ 批量识别（rec）+ CTC 解码。"""

import numpy as np

from .preprocess import resize_norm_cls, resize_norm_rec
from .sessions import run_session


def rotate_crops(crops: list, cls_session, params) -> list:
    """对裁剪行做方向分类，180° 且置信度 >0.9 的行原地翻转。"""
    img_list = list(crops)

    width_list = [img.shape[1] / float(img.shape[0]) for img in img_list]
    indices = np.argsort(np.array(width_list))

    image_shape = params.cls_image_shape
    batch_num = params.cls_batch_num
    img_num = len(img_list)
    for begin in range(0, img_num, batch_num):
        end = min(img_num, begin + batch_num)
        norm_batch = np.stack(
            [resize_norm_cls(img_list[indices[i]], image_shape) for i in range(begin, end)]
        ).astype(np.float32)

        prob = run_session(cls_session, norm_batch)
        pred_idxs = prob.argmax(axis=1)
        for offset, idx in enumerate(pred_idxs):
            score = prob[offset, int(idx)]
            if int(idx) == 1 and score > params.cls_thresh:
                position = int(indices[begin + offset])
                img_list[position] = np.ascontiguousarray(
                    img_list[position][::-1, ::-1]
                )
    return img_list


def recognize_crops(rec_session, characters: list, crops: list, params) -> list:
    """批量识别，返回与 crops 同序的 [(text, score)] 列表。"""
    width_list = [img.shape[1] / float(img.shape[0]) for img in crops]
    indices = np.argsort(np.array(width_list))

    img_num = len(crops)
    results = [("", 0.0)] * img_num

    image_shape = params.rec_image_shape
    batch_num = params.rec_batch_num
    for begin in range(0, img_num, batch_num):
        end = min(img_num, begin + batch_num)

        _, img_height, default_width = image_shape
        max_wh_ratio = default_width / float(img_height)
        for i in range(begin, end):
            h, w = crops[int(indices[i])].shape[:2]
            max_wh_ratio = max(max_wh_ratio, w / float(h))

        target_width = int(img_height * max_wh_ratio)
        norm_batch = np.stack(
            [
                resize_norm_rec(crops[int(indices[i])], max_wh_ratio, image_shape)
                for i in range(begin, end)
            ]
        ).astype(np.float32)

        preds = run_session(rec_session, norm_batch)
        batch_results = ctc_decode(preds, characters)
        for offset, one_result in enumerate(batch_results):
            results[int(indices[begin + offset])] = one_result

    return results


def ctc_decode(preds: np.ndarray, characters: list) -> list:
    """CTC 解码：argmax → 相邻去重 → 去 blank(0) → 查表 join；
    score 为选中位置概率均值（逐位 round5 后均值再 round5）。空序列得 ("", 0)。"""
    preds_idx = preds.argmax(axis=2)
    preds_prob = preds.max(axis=2)

    batch_size = len(preds_idx)
    results = []
    for batch_idx in range(batch_size):
        token_indices = preds_idx[batch_idx]

        selection = np.ones(len(token_indices), dtype=bool)
        selection[1:] = token_indices[1:] != token_indices[:-1]
        selection &= token_indices != 0

        conf_list = [round(conf, 5) for conf in preds_prob[batch_idx][selection].tolist()]
        if not conf_list:
            conf_list = [0]

        text = "".join(characters[text_id] for text_id in token_indices[selection])
        score = float(np.mean(conf_list).round(5))
        results.append((text, score))
    return results
