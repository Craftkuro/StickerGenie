import sys


APP_USER_MODEL_ID = "StickerGenie.Desktop"


def _set_windows_app_user_model_id() -> None:
    """Windows 任务栏按 AppUserModelID 分组，避免源码运行时显示 python 图标。"""

    if sys.platform != "win32":
        return

    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        APP_USER_MODEL_ID
    )