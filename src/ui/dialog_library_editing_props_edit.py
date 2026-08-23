# coding=utf-8
import logging
from typing import Optional

from PyQt6.QtCore import QDateTime
from PyQt6.QtWidgets import (
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)
from sqlalchemy.exc import SQLAlchemyError

import services.global_instances
from commons.dto import StickerImage

logger = logging.getLogger(__name__)


class LibraryEditingPropsEditDialog(QDialog):
    """
    编辑图片的文件属性：原始文件名与记录的修改时间。

    其余字段（hash、尺寸等）由真实文件内容派生或用于存储寻址，不开放编辑。
    """

    def __init__(
        self,
        parent=None,
        database=None,
        sticker: Optional[StickerImage] = None,
    ):
        super().__init__(parent)

        self._database = (
            database
            if database is not None
            else services.global_instances.current_library_db
        )
        self._sticker = sticker
        self._updated_sticker: Optional[StickerImage] = None

        self.setWindowTitle("编辑属性")
        # 默认宽度过小，文件名与时间控件都会被截断。
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        layout.addLayout(form)

        self.lineEditFileName = QLineEdit(self)
        self.lineEditFileName.setObjectName("lineEditFileName")
        if sticker is not None:
            self.lineEditFileName.setText(sticker.original_file_name)
        form.addRow("文件名", self.lineEditFileName)

        self.dateTimeEditModification = QDateTimeEdit(self)
        self.dateTimeEditModification.setObjectName("dateTimeEditModification")
        self.dateTimeEditModification.setCalendarPopup(True)
        self.dateTimeEditModification.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        if sticker is not None and sticker.modification_date is not None:
            self.dateTimeEditModification.setDateTime(
                QDateTime(sticker.modification_date)
            )
        form.addRow("修改时间", self.dateTimeEditModification)

        self.buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttonBox.setObjectName("buttonBox")
        layout.addWidget(self.buttonBox)

        self.buttonBox.accepted.connect(self._on_accepted)
        self.buttonBox.rejected.connect(self.reject)

    def _on_accepted(self):
        sticker = self._sticker
        if sticker is None:
            self.reject()
            return

        new_name = self.lineEditFileName.text().strip()
        if not new_name:
            QMessageBox.warning(self, "无法保存", "文件名不能为空。")
            return
        new_date = self.dateTimeEditModification.dateTime().toPyDateTime()

        try:
            updated = self._database.update_sticker_file_properties(
                sticker.id,
                original_file_name=new_name,
                modification_date=new_date,
            )
        except (ValueError, OSError, SQLAlchemyError) as exc:
            logger.exception("保存图片文件属性失败，id=%s", sticker.id)
            QMessageBox.critical(self, "保存失败", str(exc))
            return

        self._updated_sticker = updated
        self.accept()

    def updated_sticker(self) -> Optional[StickerImage]:
        """返回已保存的更新后 DTO；尚未成功保存时为 None。"""
        return self._updated_sticker
