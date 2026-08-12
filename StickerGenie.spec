# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

SPEC_DIR = Path(SPECPATH).resolve()
sys.path.insert(0, str(SPEC_DIR / "src"))
from image_features_extractor.model_specs import DEFAULT_MODEL_SPEC

feature_model_path = SPEC_DIR / "src" / DEFAULT_MODEL_SPEC.model_filename
feature_model_hash_path = feature_model_path.with_suffix(".sha1")


# 运行时从 sys._MEIPASS 读取的应用资源。
# 单目录模式下这些文件会进入 dist/StickerGenie/_internal/，
# 与代码里 apppath.app_path / "ui" / "*.ui" 和 apppath.app_path / "*.onnx" 对应。
datas = [
    ("src/ui/main_window.ui", "ui"),
    ("src/ui/page_finite_sticker_collection.ui", "ui"),
    ("src/ui/page_infinite_sticker_collection.ui", "ui"),
    ("src/ui/dialog_tag_manager.ui", "ui"),
    ("src/ui/dialog_settings.ui", "ui"),
    ("src/ui/dialog_image_viewer.ui", "ui"),
    ("src/ui/dialog_image_import.ui", "ui"),
    ("src/ui/dialog_image_import_progress.ui", "ui"),
    ("src/ui/dialog_database_maintenance.ui", "ui"),
    ("src/resources/thumbnail_placeholder.png", "resources"),
    ("src/resources/menu.svg", "resources"),
    (str(feature_model_path), "."),
    (str(feature_model_hash_path), "."),
]

# ChromaDB 1.x 通过字符串动态加载实现类；Rust 绑定是独立二进制包。
# 不显式收集时，打包后向量库初始化会报找不到模块或 DLL。
chromadb_datas, chromadb_binaries, chromadb_hiddenimports = collect_all("chromadb")
rust_datas, rust_binaries, rust_hiddenimports = collect_all("chromadb_rust_bindings")
rapidocr_datas, rapidocr_binaries, rapidocr_hiddenimports = collect_all("rapidocr")

datas += chromadb_datas + rust_datas + rapidocr_datas
binaries = chromadb_binaries + rust_binaries + rapidocr_binaries
hiddenimports = chromadb_hiddenimports + rust_hiddenimports + rapidocr_hiddenimports + [
    # .ui 通过 uic 在运行时按字符串 header 动态导入该控件模块，
    # PyInstaller 静态分析发现不了，必须显式声明，否则打包后
    # 加载表情包标签页会报 ModuleNotFoundError。
    "ui.widgets.sticker_list_view_widget",
    "ui.widgets.image_text_edit_widget",
    "PyQt6.QtSvg",
]

# NVIDIA CUDA/cuDNN 运行库：打包后统一放到 onnxruntime/capi 目录，
# 与 onnxruntime_providers_cuda.dll 同级，便于 Windows 加载和 preload_dlls() 找到。
#nvidia_dll_dirs = [
#    Path("vendor/nvidia/cu13/bin/x86_64"),
#    Path("vendor/nvidia/cudnn/bin"),
#]
#for dll_dir in nvidia_dll_dirs:
#    if dll_dir.is_dir():
#        for dll in sorted(dll_dir.glob("*.dll")):
#            binaries.append((str(dll), "onnxruntime/capi"))


a = Analysis(
    ["src/main.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 排除 torch 系依赖：运行时只使用 onnxruntime，torch 仅是测试/调试链被
    # PyInstaller 静态分析误收集，剔除后体积可减少约 477 MB。
    excludes=["torch", "torchvision", "functorch"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StickerGenie",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        "onnxruntime*.dll",
        "onnxruntime_providers*.dll",
        "cudart64*.dll",
        "cublas*.dll",
        "cufft64*.dll",
        "cudnn*.dll",
        "nvrtc*.dll",
        "nvJitLink*.dll",
    ],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        "onnxruntime*.dll",
        "onnxruntime_providers*.dll",
        "cudart64*.dll",
        "cublas*.dll",
        "cufft64*.dll",
        "cudnn*.dll",
        "nvrtc*.dll",
        "nvJitLink*.dll",
    ],
    name="StickerGenie",
)
