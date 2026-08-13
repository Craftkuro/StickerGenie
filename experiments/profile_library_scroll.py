# coding=utf-8
r"""大图库滚动翻页性能剖析脚本。

模拟“高分辨率屏幕 + 最小图片尺寸 + 大图库多次翻页”场景：
    - offscreen Qt 平台（无需真实窗口）
    - 大视口（默认 3200x2000，可模拟高分屏的大量可见 item）
    - item 尺寸调到最小（48px，与界面上“图片显示大小”滑块最小值一致）
    - 反复滚动到底部触发分页加载，逐页记录耗时、进程内存、缩略图缓存状态
    - 翻完后再跳回顶部/中部，观察缓存未命中时的回看成本

用法：
    .venv\Scripts\python.exe experiments\profile_library_scroll.py --pages 80
    .venv\Scripts\python.exe experiments\profile_library_scroll.py --profile --profile-jobs

说明：
    - 默认使用 C:\Users\user\Downloads\StickerGenie Library Large 作为测试图库，
      可用 --library 指定其它库（Default Library 目录）。
    - 脚本会读取该库的 db/blob/thumbnails；缺少磁盘缩略图的大图会被异步生成，
      即 thumbnails 目录可能新增文件（与真实运行行为一致）。
    - --profile 用 cProfile 剖析主线程（UI 事件、分页查询、绘制），
      结果写入 experiments\\profile_library_scroll_main.prof，可用 snakeviz 查看。
    - --profile-jobs 只对缩略图生成工作线程做轻量计时统计（cProfile 与
      Qt 线程池一起用会崩溃），打印任务数/总耗时/平均耗时。
"""

from __future__ import annotations

import argparse
import cProfile
import ctypes
import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import apppath  # noqa: E402

apppath.setup_data_path(str(SRC_DIR))

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import services.global_instances as global_instances  # noqa: E402
from blob_storage import BlobStorage  # noqa: E402
from services.thumbnail_provider import ThumbnailProvider  # noqa: E402
from stickerdb.v1.sticker_db import StickerDBV1  # noqa: E402
from thumbnail_disk_storage import ThumbnailDiskStorage  # noqa: E402
import services.sticker_library_viewer_service  # noqa: E402  (import first to break circular import)
from ui.page_infinite_sticker_collection import InfiniteStickerCollectionPage  # noqa: E402

MAIN_PROF_PATH = Path(__file__).with_name("profile_library_scroll_main.prof")


def _working_set_mb() -> float:
    """返回当前进程工作集大小（MB）；非 Windows 或调用失败时返回 0。"""
    try:
        import ctypes as _ctypes
        from ctypes import wintypes

        class _ProcessMemoryCounters(_ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", _ctypes.c_size_t),
                ("WorkingSetSize", _ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", _ctypes.c_size_t),
                ("QuotaPagedPoolUsage", _ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", _ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", _ctypes.c_size_t),
                ("PagefileUsage", _ctypes.c_size_t),
                ("PeakPagefileUsage", _ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = _ctypes.sizeof(_ProcessMemoryCounters)
        kernel32 = _ctypes.windll.kernel32
        # pseudo-handle from GetCurrentProcess() can fail in restricted envs
        handle = kernel32.OpenProcess(0x1000, False, os.getpid())
        if not handle:
            return 0.0
        try:
            if kernel32.K32GetProcessMemoryInfo(
                handle, _ctypes.byref(counters), counters.cb
            ):
                return counters.WorkingSetSize / (1024 * 1024)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return 0.0


def process_events_for(seconds: float) -> None:
    """处理 Qt 事件直到指定时间结束（期间异步缩略图信号会被处理）。"""
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.002)


def _cache_state(provider: ThumbnailProvider) -> tuple[int, int, int]:
    """返回 (内存缓存条目数, 后台生成中数, 失败数)。"""
    return (
        len(provider._memory_cache),
        len(provider._in_flight),
        len(provider._failed_hashes),
    )


def run_scenario(page: InfiniteStickerCollectionPage, args) -> list[float]:
    """反复滚动到底部翻页，返回每页耗时（毫秒）列表。"""
    view = page.listViewStickerList
    scrollbar = view.verticalScrollBar()
    provider = global_instances.current_thumbnail_provider

    # 等首屏的多页增量加载和后台缩略图任务稳定下来
    process_events_for(3.0)

    def report(page_no: int, elapsed_ms: float, note: str = "") -> None:
        rows = view.model().rowCount()
        cached, in_flight, failed = _cache_state(provider)
        print(
            f"page {page_no:>4} | rows {rows:>6} | {elapsed_ms:8.1f} ms "
            f"| mem {_working_set_mb():7.1f} MB | cache {cached:>5} "
            f"| in-flight {in_flight:>4} | failed {failed:>4} {note}"
        )

    print("--- 向下翻页（每次滚动到底部加载 100 条）---")
    timings: list[float] = []
    for page_no in range(1, args.pages + 1):
        t0 = time.perf_counter()
        scrollbar.setValue(scrollbar.maximum())
        process_events_for(0.2)
        view.viewport().update()
        process_events_for(0.1)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        timings.append(elapsed_ms)
        if (
            page_no <= 3
            or page_no > args.pages - 3
            or page_no % args.every == 0
        ):
            report(page_no, elapsed_ms)

    first5 = sum(timings[:5]) / 5
    last5 = sum(timings[-5:]) / 5
    print(
        f"avg first 5 pages {first5:8.1f} ms | avg last 5 pages "
        f"{last5:8.1f} ms | slowdown x{last5 / max(first5, 0.001):.1f}"
    )

    print("--- 回看（跳回顶部/中部，缓存可能已被 LRU 淘汰）---")
    positions = {
        "top": scrollbar.minimum(),
        "25%": int(scrollbar.maximum() * 0.25),
        "50%": int(scrollbar.maximum() * 0.5),
        "75%": int(scrollbar.maximum() * 0.75),
    }
    for label, value in positions.items():
        t0 = time.perf_counter()
        scrollbar.setValue(value)
        process_events_for(0.3)
        view.viewport().update()
        process_events_for(0.2)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        cached, in_flight, failed = _cache_state(provider)
        print(
            f"jump to {label:>4} | rows {view.model().rowCount():>6} "
            f"| {elapsed_ms:8.1f} ms | mem {_working_set_mb():7.1f} MB "
            f"| cache {cached:>5} | in-flight {in_flight:>4} | failed {failed:>4}"
        )
    return timings


class _JobTimingStats:
    """线程安全的缩略图生成任务计时统计。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.count = 0
        self.total_seconds = 0.0
        self.max_seconds = 0.0

    def record(self, seconds: float) -> None:
        with self._lock:
            self.count += 1
            self.total_seconds += seconds
            if seconds > self.max_seconds:
                self.max_seconds = seconds


def patch_job_timing(stats: _JobTimingStats) -> None:
    """包装缩略图工作线程的 run()，只做计时，不启用 cProfile。"""
    import services.thumbnail_provider.job as job_module

    original_run = job_module._ThumbnailGenerationJob.run

    def run_with_timing(self) -> None:
        t0 = time.perf_counter()
        try:
            original_run(self)
        finally:
            stats.record(time.perf_counter() - t0)

    job_module._ThumbnailGenerationJob.run = run_with_timing


def print_top_stats(profiler: cProfile.Profile, title: str, limit: int = 25) -> None:
    import pstats

    stats = pstats.Stats(profiler)
    stats.strip_dirs()
    print(f"\n===== {title}：按自身耗时（tottime）Top {limit} =====")
    stats.sort_stats(pstats.SortKey.TIME)
    stats.print_stats(limit)
    print(f"\n===== {title}：按累计耗时（cumtime）Top {limit} =====")
    stats.sort_stats(pstats.SortKey.CUMULATIVE)
    stats.print_stats(limit)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="大图库滚动翻页性能剖析",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--library",
        default=os.environ.get(
            "STICKERGENIE_TEST_LIBRARY",
            r"C:\Users\user\Downloads\StickerGenie Library Large\Default Library",
        ),
        help="测试图库的 Default Library 目录（含 db/blob/thumbnails）",
    )
    parser.add_argument(
        "--thumbnails",
        default=None,
        help="thumbnail disk cache dir (default: the library thumbnails; "
             "copy to a writable dir to avoid writing into the test library)",
    )
    parser.add_argument("--pages", type=int, default=80, help="向下翻页次数")
    parser.add_argument("--item-size", type=int, default=48, help="item 边长（界面滑块最小值 48）")
    parser.add_argument("--viewport", default="3200x2000", help="模拟视口尺寸 WxH（高分屏）")
    parser.add_argument("--every", type=int, default=5, help="每隔多少页打印一行明细")
    parser.add_argument("--profile", action="store_true", help="用 cProfile 剖析主线程")
    parser.add_argument("--profile-jobs", action="store_true", help="统计缩略图生成工作线程耗时")
    args = parser.parse_args()

    width_s, _, height_s = args.viewport.partition("x")
    viewport_size = (int(width_s), int(height_s))

    library_path = Path(args.library)
    db_path = library_path / "db" / "v1" / "sticker.db"
    if not db_path.exists():
        print(f"未找到图库数据库：{db_path}")
        raise SystemExit(2)
    print(f"测试图库：{library_path}")

    global_instances.current_library_db = StickerDBV1(str(db_path))
    global_instances.current_blob_storage = BlobStorage(str(library_path / "blob"))
    thumbnail_path = (
        Path(args.thumbnails)
        if args.thumbnails
        else library_path / "thumbnails"
    )
    disk_storage = ThumbnailDiskStorage(str(thumbnail_path))
    global_instances.current_thumbnail_disk_storage = disk_storage
    global_instances.current_thumbnail_provider = ThumbnailProvider(
        disk_storage=disk_storage
    )

    app = QApplication.instance() or QApplication([])

    page = InfiniteStickerCollectionPage(auto_refresh=False)
    page.resize(*viewport_size)
    page.listViewStickerList.set_display_size(args.item_size)
    page.show()
    QCoreApplication.processEvents()

    job_stats = _JobTimingStats()
    if args.profile_jobs:
        patch_job_timing(job_stats)

    main_profiler = cProfile.Profile()
    try:
        if args.profile:
            main_profiler.enable()
        run_scenario(page, args)
    finally:
        if args.profile:
            main_profiler.disable()
            main_profiler.dump_stats(str(MAIN_PROF_PATH))
            print(f"\n主线程 profile 已写入：{MAIN_PROF_PATH}")
            print_top_stats(main_profiler, "主线程")

        if job_stats.count:
            avg_ms = job_stats.total_seconds / job_stats.count * 1000
            print(
                f"缩略图生成任务：{job_stats.count} 个，总耗时 "
                f"{job_stats.total_seconds:.1f} s，平均 {avg_ms:.1f} ms，"
                f"最慢 {job_stats.max_seconds * 1000:.1f} ms"
            )

        page.close()
        # 等待后台线程结束，避免进程退出时误报 Qt 线程错误
        pool = global_instances.current_thumbnail_provider._pool
        if pool is not None:
            pool.waitForDone(5000)


if __name__ == "__main__":
    main()
