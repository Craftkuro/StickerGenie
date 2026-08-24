import logging
import sys

logger = logging.getLogger(__name__)

APP_USER_MODEL_ID = "StickerGenie.Desktop"

_ON_WINDOWS = sys.platform == "win32"

if _ON_WINDOWS:
    import ctypes
    import uuid
    from ctypes import (
        WINFUNCTYPE,
        HRESULT,
        Structure,
        byref,
        c_uint32,
        c_uint16,
        c_uint64,
        c_ubyte,
        c_void_p,
    )

    _CLSCTX_INPROC_SERVER = 0x1
    _COINIT_APARTMENTTHREADED = 0x2
    # 已以其他模式初始化过 COM 时，继续复用现有初始化即可。
    _RPC_E_CHANGED_MODE = -2147417850
    _TBPF_NOPROGRESS = 0x0
    CLSID_TASKBARLIST = "56FDF344-FD6D-11d0-958A-006097C9A090"
    IID_ITASKBARLIST3 = "EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF"
    # ITaskbarList3 vtable 槽位：IUnknown 占 0-2，ITaskbarList 占 3-7，
    # MarkFullscreenWindow 占 8，随后是进度相关方法。
    _VT_HR_INIT = 3
    _VT_SET_PROGRESS_VALUE = 9
    _VT_SET_PROGRESS_STATE = 10

    class _GUID(Structure):
        _fields_ = [
            ("data1", c_uint32),
            ("data2", c_uint16),
            ("data3", c_uint16),
            ("data4", c_ubyte * 8),
        ]

    def _guid(text: str) -> _GUID:
        return _GUID.from_buffer_copy(uuid.UUID(text).bytes_le)

    # COM 方法首个参数固定为接口指针本身。
    _HR_INIT_PROTO = WINFUNCTYPE(HRESULT, c_void_p)
    _SET_PROGRESS_VALUE_PROTO = WINFUNCTYPE(
        HRESULT, c_void_p, c_void_p, c_uint64, c_uint64
    )
    _SET_PROGRESS_STATE_PROTO = WINFUNCTYPE(HRESULT, c_void_p, c_void_p, c_uint32)

    _taskbar_list = None
    _taskbar_failed = False

    def _set_windows_app_user_model_id() -> None:
        """Windows 任务栏按 AppUserModelID 分组，避免源码运行时显示 python 图标。"""

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID
        )

    def _vtable_method(obj: c_void_p, slot: int, prototype):
        """取 COM 接口 vtable 中第 slot 个槽位的函数指针。"""
        table_pointer = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))
        entries = ctypes.cast(table_pointer.contents, ctypes.POINTER(c_void_p))
        return prototype(entries[slot])

    def _init_taskbar_list():
        ole32 = ctypes.windll.ole32
        hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        if hr < 0 and hr != _RPC_E_CHANGED_MODE:
            return None

        handle = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(_guid(CLSID_TASKBARLIST)),
            None,
            _CLSCTX_INPROC_SERVER,
            byref(_guid(IID_ITASKBARLIST3)),
            byref(handle),
        )
        if hr < 0 or not handle:
            return None
        if _vtable_method(handle, _VT_HR_INIT, _HR_INIT_PROTO)(handle) < 0:
            return None

        set_value = _vtable_method(
            handle,
            _VT_SET_PROGRESS_VALUE,
            _SET_PROGRESS_VALUE_PROTO,
        )
        set_state = _vtable_method(
            handle,
            _VT_SET_PROGRESS_STATE,
            _SET_PROGRESS_STATE_PROTO,
        )
        return handle, set_value, set_state

    def _get_taskbar_list():
        global _taskbar_list, _taskbar_failed
        if _taskbar_list is not None:
            return _taskbar_list
        if _taskbar_failed:
            return None
        try:
            _taskbar_list = _init_taskbar_list()
        except Exception:
            logger.warning("初始化任务栏进度支持失败", exc_info=True)
        if _taskbar_list is None:
            _taskbar_failed = True
        return _taskbar_list

    def taskbar_set_progress(hwnd: int, completed: int, total: int) -> bool:
        """在任务栏按钮上显示 completed/total 进度；失败时返回 False。"""

        taskbar = _get_taskbar_list()
        if taskbar is None or hwnd <= 0 or total <= 0:
            return False
        handle, set_value, _ = taskbar
        try:
            hr = set_value(
                handle,
                c_void_p(hwnd),
                c_uint64(max(0, completed)),
                c_uint64(total),
            )
        except Exception:
            logger.warning("设置任务栏进度失败", exc_info=True)
            return False
        return hr == 0

    def taskbar_clear_progress(hwnd: int) -> bool:
        """移除任务栏按钮上的进度显示；失败时返回 False。"""

        taskbar = _get_taskbar_list()
        if taskbar is None or hwnd <= 0:
            return False
        handle, _, set_state = taskbar
        try:
            hr = set_state(handle, c_void_p(hwnd), _TBPF_NOPROGRESS)
        except Exception:
            logger.warning("清除任务栏进度失败", exc_info=True)
            return False
        return hr == 0

else:

    def _set_windows_app_user_model_id() -> None:
        pass

    def taskbar_set_progress(hwnd: int, completed: int, total: int) -> bool:
        return False

    def taskbar_clear_progress(hwnd: int) -> bool:
        return False
