from dataclasses import dataclass
from PyQt6.QtWidgets import QWidget


@dataclass(frozen=True)
class ImportImagesRequest:
    file_paths: tuple[str, ...]
    generate_vectors: bool = False


@dataclass
class MainWindowNewTabRequest:
    # 将成为新标签页内容的Widget
    widget: QWidget
    # 新标签页标题
    title: str | None

@dataclass
class CallbackObjHolder:
    callback_obj: object
