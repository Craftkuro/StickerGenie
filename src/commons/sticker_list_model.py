# coding=utf-8
"""自维护身份索引的贴纸列表模型。

行号查询永远返回当前真值：索引在本模型的结构变化漏斗上同步维护，
不存在陈旧行号，调用方无需校验。
"""

from PyQt6.QtGui import QStandardItemModel

from commons.roles import ROLE_BLOB_ENTITY, ROLE_STICKER_IMAGE


def _group_runs(sorted_rows: list[int]) -> list[tuple[int, int]]:
    """把升序行号聚成 (起点, 长度) 的连续区段列表。"""
    runs: list[tuple[int, int]] = []
    for row in sorted_rows:
        if runs and runs[-1][0] + runs[-1][1] == row:
            first, length = runs[-1]
            runs[-1] = (first, length + 1)
        else:
            runs.append((row, 1))
    return runs


class StickerListModel(QStandardItemModel):
    """自维护 id→row / hash→row 身份索引的贴纸列表模型。

    索引维护刻意最简：追加插入增量写入，其余一切结构变化只打脏标记，
    下一次读取时懒重建。因此任何时刻的查询结果都是当前真值。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row_by_id: dict[int, int] = {}
        self._row_by_hash: dict[str, int] = {}
        self._dirty = True
        self.rowsInserted.connect(self._on_rows_inserted)
        self.rowsRemoved.connect(self._mark_dirty)
        self.modelReset.connect(self._mark_dirty)

    # ---- 对外查询 ----

    def row_for_id(self, sticker_id: int) -> int | None:
        """返回 sticker_id 当前所在行；不在本模型时返回 None。"""
        self._ensure_index()
        return self._row_by_id.get(sticker_id)

    def row_for_hash(self, file_hash: str) -> int | None:
        """返回 blob hash 当前所在行；不在本模型时返回 None。"""
        self._ensure_index()
        return self._row_by_hash.get(file_hash)

    # ---- 对外操作 ----

    def refresh_stickers(self, updated_stickers: list) -> int:
        """按 id 定位行，把新标签写进行上挂着的共享 DTO 并局部重绘。

        返回实际更新的行数；id 不在本模型中的条目自然跳过。
        """
        self._ensure_index()
        updated_by_id = {
            s.id: s for s in updated_stickers if getattr(s, "id", None) is not None
        }
        changed = 0
        for sticker_id, source in updated_by_id.items():
            row = self._row_by_id.get(sticker_id)
            if row is None:
                continue
            index = self.index(row, 0)
            dto = index.data(ROLE_STICKER_IMAGE)
            if dto is None:
                continue
            dto.tags = list(source.tags)
            self.dataChanged.emit(index, index, [ROLE_STICKER_IMAGE])
            changed += 1
        return changed

    def remove_stickers_by_ids(self, deleted_ids) -> int:
        """按 id 定位并移除命中行，返回移除数。

        payload 含陌生 id 时为无害 no-op。
        """
        self._ensure_index()
        deleted = set(deleted_ids)
        rows = sorted(
            row for sticker_id, row in self._row_by_id.items()
            if sticker_id in deleted
        )
        if not rows:
            return 0
        # 合并为最大连续区段，从大区段到小区段移除，避免行号平移。
        for first, count in reversed(_group_runs(rows)):
            self.removeRows(first, count)
        return len(rows)

    # ---- 内部维护 ----

    def _on_rows_inserted(self, _parent, first: int, last: int) -> None:
        if self._dirty:
            # 反正要全量重建，不必做增量写入。
            return
        if last != self.rowCount() - 1:
            # 中间插入会让后续行号整体平移，逐行修正不如打脏重建。
            self._mark_dirty()
            return
        for row in range(first, last + 1):
            self._index_row(row)

    def _mark_dirty(self, *_args) -> None:
        self._dirty = True

    def _ensure_index(self) -> None:
        if self._dirty:
            self._rebuild_index()

    def _rebuild_index(self) -> None:
        """遍历全表重建双键索引；重复键后写覆盖（定位到最后出现的行）。"""
        self._row_by_id.clear()
        self._row_by_hash.clear()
        for row in range(self.rowCount()):
            self._index_row(row)
        self._dirty = False

    def _index_row(self, row: int) -> None:
        index = self.index(row, 0)
        dto = index.data(ROLE_STICKER_IMAGE)
        if dto is not None and getattr(dto, "id", None) is not None:
            self._row_by_id[dto.id] = row
        blob_entity = index.data(ROLE_BLOB_ENTITY)
        if blob_entity is not None:
            self._row_by_hash[blob_entity.hash] = row
