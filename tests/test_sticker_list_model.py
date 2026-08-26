import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QStandardItem
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication

from blob_storage import BlobFileEntity
from commons.dto import StickerImage, Tag
from commons.roles import ROLE_BLOB_ENTITY, ROLE_STICKER_IMAGE
from commons.sticker_list_model import StickerListModel


def make_dto(sticker_id: int | None) -> StickerImage:
    sticker = StickerImage()
    sticker.id = sticker_id
    sticker.original_file_name = f"sticker-{sticker_id}.png"
    sticker.hash = f"dto-hash-{sticker_id}"
    sticker.tags = []
    return sticker


def append_item(
    model: StickerListModel,
    *,
    sticker_id: int | None = None,
    file_hash: str | None = None,
) -> QStandardItem:
    item = QStandardItem("")
    if sticker_id is not None:
        item.setData(make_dto(sticker_id), ROLE_STICKER_IMAGE)
    if file_hash is not None:
        item.setData(BlobFileEntity(file_hash, ".png"), ROLE_BLOB_ENTITY)
    model.appendRow(item)
    return item


class StickerListModelIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_index_tracks_append_insert_delete_reset(self):
        model = StickerListModel()
        for sticker_id in range(1, 5):
            append_item(
                model,
                sticker_id=sticker_id,
                file_hash=f"hash-{sticker_id}",
            )

        self.assertEqual(
            [0, 1, 2, 3],
            [model.row_for_id(i) for i in range(1, 5)],
        )
        self.assertEqual(
            [0, 1, 2, 3],
            [model.row_for_hash(f"hash-{i}") for i in range(1, 5)],
        )

        # 中间插入让后续行号整体平移，读取时懒重建出当前真值。
        item = QStandardItem("")
        item.setData(make_dto(9), ROLE_STICKER_IMAGE)
        item.setData(BlobFileEntity("hash-9", ".png"), ROLE_BLOB_ENTITY)
        model.insertRow(2, item)

        self.assertEqual(
            [0, 1, 2, 3, 4],
            [model.row_for_id(i) for i in (1, 2, 9, 3, 4)],
        )
        self.assertEqual(2, model.row_for_hash("hash-9"))

        # 离散删除（id=2 与 id=4）后幸存行号前移。
        model.removeRow(1)
        model.removeRow(3)

        self.assertEqual(
            [0, 1, 2],
            [model.row_for_id(i) for i in (1, 9, 3)],
        )
        self.assertIsNone(model.row_for_id(2))
        self.assertIsNone(model.row_for_id(4))
        self.assertIsNone(model.row_for_hash("hash-2"))
        self.assertIsNone(model.row_for_hash("hash-4"))
        self.assertEqual(1, model.row_for_hash("hash-9"))

        # reset 后索引为空，且仍可继续追加使用。
        model.clear()
        self.assertIsNone(model.row_for_id(1))
        self.assertEqual(0, model.rowCount())
        append_item(model, sticker_id=8, file_hash="hash-8")
        self.assertEqual(0, model.row_for_id(8))

    def test_rows_without_expected_roles_are_not_indexed(self):
        model = StickerListModel()
        model.appendRow(QStandardItem(""))
        append_item(model, sticker_id=5, file_hash="hash-5")

        self.assertIsNone(model.row_for_id(999))
        self.assertIsNone(model.row_for_hash("absent"))
        self.assertEqual(1, model.row_for_id(5))
        self.assertEqual(1, model.row_for_hash("hash-5"))

    def test_duplicate_keys_resolve_to_last_row(self):
        model = StickerListModel()
        append_item(model, sticker_id=1, file_hash="dup-hash")
        append_item(model, sticker_id=1, file_hash="dup-hash")

        self.assertEqual(1, model.row_for_id(1))
        self.assertEqual(1, model.row_for_hash("dup-hash"))


class LazyRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_consecutive_deletions_defer_to_single_rebuild(self):
        model = StickerListModel()
        for sticker_id in range(6):
            append_item(
                model,
                sticker_id=sticker_id,
                file_hash=f"hash-{sticker_id}",
            )
        model.row_for_id(0)  # 建立干净索引

        with patch.object(
            model, "_rebuild_index", wraps=model._rebuild_index
        ) as rebuild_spy:
            model.removeRow(1)
            model.removeRow(1)
            model.removeRow(1)
            self.assertEqual(0, rebuild_spy.call_count)

            self.assertIsNone(model.row_for_id(1))
            self.assertEqual(1, rebuild_spy.call_count)

        # 重建后的真值：幸存行为 [0, 4, 5]。
        self.assertEqual(0, model.row_for_id(0))
        self.assertEqual(1, model.row_for_id(4))
        self.assertEqual(2, model.row_for_id(5))

    def test_append_after_clean_index_is_incremental(self):
        model = StickerListModel()
        append_item(model, sticker_id=1, file_hash="hash-1")
        model.row_for_id(1)

        with patch.object(
            model, "_rebuild_index", wraps=model._rebuild_index
        ) as rebuild_spy:
            append_item(model, sticker_id=2, file_hash="hash-2")
            self.assertEqual(1, model.row_for_id(2))
            self.assertEqual(1, model.row_for_hash("hash-2"))
            self.assertEqual(0, rebuild_spy.call_count)


class RefreshStickersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_writes_shared_dto_and_emits_scoped_data_changed(self):
        model = StickerListModel()
        append_item(model, sticker_id=1, file_hash="hash-1")
        target_item = append_item(model, sticker_id=2, file_hash="hash-2")
        append_item(model, sticker_id=3, file_hash="hash-3")
        target_dto = target_item.data(ROLE_STICKER_IMAGE)

        spy = QSignalSpy(model.dataChanged)
        updated = make_dto(2)
        tag = Tag()
        tag.id = 99
        tag.name = "批量新标签"
        updated.tags = [tag]

        changed = model.refresh_stickers([updated])

        self.assertEqual(1, changed)
        self.assertEqual(1, len(spy))
        self.assertEqual(1, spy[0][0].row())
        self.assertEqual([ROLE_STICKER_IMAGE], list(spy[0][2]))
        # 写的是该行挂着的共享 DTO（原地改），不是替换对象。
        self.assertIs(target_dto, model.index(1, 0).data(ROLE_STICKER_IMAGE))
        self.assertEqual(
            ["批量新标签"],
            [t.name for t in target_dto.tags],
        )
        # 其他行不受影响。
        self.assertEqual(
            [],
            model.index(0, 0).data(ROLE_STICKER_IMAGE).tags,
        )

    def test_refresh_skips_unknown_and_idless_entries(self):
        model = StickerListModel()
        append_item(model, sticker_id=1, file_hash="hash-1")

        spy = QSignalSpy(model.dataChanged)
        changed = model.refresh_stickers([make_dto(999), make_dto(None)])

        self.assertEqual(0, changed)
        self.assertEqual(0, len(spy))

    def test_refresh_multiple_ids_update_each_once(self):
        model = StickerListModel()
        append_item(model, sticker_id=1, file_hash="hash-1")
        append_item(model, sticker_id=2, file_hash="hash-2")

        first = make_dto(1)
        first.tags = [Tag()]
        second = make_dto(2)
        second.tags = [Tag(), Tag()]

        changed = model.refresh_stickers([first, second])

        self.assertEqual(2, changed)
        self.assertEqual(1, len(model.index(0, 0).data(ROLE_STICKER_IMAGE).tags))
        self.assertEqual(2, len(model.index(1, 0).data(ROLE_STICKER_IMAGE).tags))


class RemoveStickersByIdsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_discrete_rows_merge_into_fewest_segments(self):
        model = StickerListModel()
        for sticker_id in range(11):
            append_item(
                model,
                sticker_id=sticker_id,
                file_hash=f"hash-{sticker_id}",
            )

        segments = []
        model.rowsRemoved.connect(
            lambda _parent, first, last: segments.append((first, last))
        )
        removed = model.remove_stickers_by_ids([1, 2, 3, 7, 9, 10])

        self.assertEqual(6, removed)
        # 区段合并：[1,2,3]+[7]+[9,10]，从大到小移除。
        self.assertEqual([(9, 10), (7, 7), (1, 3)], segments)
        self.assertEqual(5, model.rowCount())
        for row, sticker_id in enumerate((0, 4, 5, 6, 8)):
            self.assertEqual(row, model.row_for_id(sticker_id))

    def test_contiguous_rows_remove_as_single_segment(self):
        model = StickerListModel()
        for sticker_id in range(5):
            append_item(model, sticker_id=sticker_id)

        segments = []
        model.rowsRemoved.connect(
            lambda _parent, first, last: segments.append((first, last))
        )
        removed = model.remove_stickers_by_ids([1, 2, 3])

        self.assertEqual(3, removed)
        self.assertEqual([(1, 3)], segments)
        self.assertEqual(2, model.rowCount())

    def test_empty_and_unknown_payload_are_noop(self):
        model = StickerListModel()
        append_item(model, sticker_id=1)
        append_item(model, sticker_id=2)

        self.assertEqual(0, model.remove_stickers_by_ids([]))
        self.assertEqual(0, model.remove_stickers_by_ids([42, 99]))
        self.assertEqual(2, model.rowCount())


class ModalRaceSimulationTests(unittest.TestCase):
    """模拟槽函数执行前模型已发生变化：索引现查现用，无陈旧行号。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_lands_on_shifted_row_after_prior_deletion(self):
        model = StickerListModel()
        append_item(model, sticker_id=1, file_hash="hash-1")
        target_item = append_item(model, sticker_id=3, file_hash="hash-3")
        target_dto = target_item.data(ROLE_STICKER_IMAGE)

        # 广播捕获之后、槽执行之前，别的行先被删掉。
        model.remove_stickers_by_ids([1, 2])

        updated = make_dto(3)
        tag = Tag()
        tag.id = 77
        tag.name = "迟到的新标签"
        updated.tags = [tag]

        spy = QSignalSpy(model.dataChanged)
        changed = model.refresh_stickers([updated])

        self.assertEqual(1, changed)
        self.assertEqual(1, len(spy))
        self.assertEqual(0, spy[0][0].row())
        self.assertIs(target_dto, model.index(0, 0).data(ROLE_STICKER_IMAGE))
        self.assertEqual(["迟到的新标签"], [t.name for t in target_dto.tags])


if __name__ == "__main__":
    unittest.main()
