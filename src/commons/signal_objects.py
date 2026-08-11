from dataclasses import dataclass
from PyQt6.QtWidgets import QWidget


@dataclass(frozen=True)
class ImportImagesRequest:
    file_paths: tuple[str, ...]
    generate_vectors: bool = False
    extract_text: bool = False


@dataclass
class MainWindowNewTabRequest:
    # 将成为新标签页内容的Widget
    widget: QWidget
    # 新标签页标题
    title: str | None
    # 用户是否可以关闭这个标签页
    closable: bool = True

@dataclass
class CallbackObjHolder:
    callback_obj: object
