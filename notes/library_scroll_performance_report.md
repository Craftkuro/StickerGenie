# 大图库翻页性能分析报告

- 日期：2026-08-13
- 涉及版本：StickerGenie 源码（`src/` 当前 HEAD）
- 测试图库：`C:\Users\user\Downloads\StickerGenie Library Large\Default Library`
- 复现工具：`experiments/profile_library_scroll.py`

## 1. 结论摘要

“大图库多次翻页后性能明显下降”的根因是**缩略图内存缓存容量（2000 条）小于高分屏最小尺寸下的可见 item 数量（约 2050 个）**，导致滚动时 LRU 缓存持续抖动：每次整屏重绘都会对大多数可见项重新走“磁盘读取 + 解码”，该路径占了主线程约 66% 的耗时。次要热点包括翻页时为每张图创建无用的 `QIcon`（约 13%）、缩略图就绪信号的逐行扫描（约 5%）、以及每次绘制都重新做平滑缩放。

DB 分页查询（约 61ms/页）和后台缩略图生成管线（平均 23ms/任务）都不是瓶颈。

## 2. 背景与复现条件

用户报告：大图库加载后多次翻页，性能明显下降；屏幕分辨率较高、图片显示大小调到最小时更容易复现。

复现脚本用 offscreen Qt 模拟该场景：

- 视口 3200x2000（模拟高分屏，约 2050 个可见 item）
- item 尺寸 48px（与界面“图片显示大小”滑块最小值一致）
- 反复滚动到底部，每页加载 100 条，共 60 页（总行数约 13600）
- 翻完后再跳回顶部/中部，观察缓存未命中时的回看成本

测试图库规模：14716 个 blob（约 10.6GB）、2695 个磁盘缩略图、SQLite 库约 4.5MB。

## 3. 测量结果

### 3.1 逐页耗时（60 页，抽样）

| 页码 | 总行数 | 耗时(ms) | 内存(MB) | 内存缓存 | in-flight |
| --- | --- | --- | --- | --- | --- |
| 1 | 2000 | 819.8 | 412.3 | 2000 | 0 |
| 2 | 2300 | 3802.1 | 434.3 | 2000 | 0 |
| 3 | 2600 | 5149.5 | 435.9 | 2000 | 0 |
| 10 | 3900 | 6828.1 | 451.0 | 2000 | 3 |
| 20 | 5900 | 7283.9 | 414.1 | 2000 | 7 |
| 30 | 7800 | 7951.8 | 404.3 | 2000 | 6 |
| 40 | 9800 | 8124.0 | 430.9 | 2000 | 8 |
| 50 | 11700 | 7531.9 | 449.1 | 2000 | 3 |
| 58 | 13300 | 7245.1 | 503.2 | 2000 | 0 |
| 59 | 13400 | 6405.7 | 505.5 | 2000 | 0 |
| 60 | 13600 | 6860.0 | 508.4 | 2000 | 0 |

- 前 5 页平均：4163.1ms；后 5 页平均：6968.2ms → **1.7 倍退化**
- 退化在第 2-3 页（缓存打满）就出现，之后稳定在 7-8s/页，呈“饱和”而非线性增长

### 3.2 回看成本（跳回顶部/中部）

| 目标位置 | 耗时(ms) |
| --- | --- |
| top | 5145.6 |
| 25% | 4742.8 |
| 50% | 5042.9 |
| 75% | 5223.8 |

回看同样要 5s 左右，说明已看过的页在 LRU 淘汰后回到可见区仍需重新解码。

### 3.3 内存

- 进程工作集从 412MB 增长到 508MB，之后基本稳定，**不是泄漏**，是 2000 条 QPixmap 内存缓存（200x200x4B ≈ 160KB/条，约 320MB）加上 Qt 自身开销。

## 4. cProfile 热点（主线程，60 页，总 435.7s）

| 函数 | 调用次数 | tottime(s) | cumtime(s) | 说明 |
| --- | --- | --- | --- | --- |
| `cache.py:32 load_disk_thumbnail` | 334975 | 227.0 | 289.3 | **主线程 66%**：`QPixmap(file)` 磁盘解码 |
| `sticker_library_viewer_service.py:66 build_sticker_items` | 134 | 58.4 | 61.5 | **13%**：为每张图创建 `QIcon(path)` |
| `provider.py:212 _generate_sync` | 70482 | 29.8 | 30.9 | 小图（≤300px）在 paint 内同步解码 |
| `nt.stat` | 440008 | 21.4 | 37.2 | 磁盘存在性检查（read_file 的 exists） |
| `index.data` | 19313557 | 12.5 | 12.5 | `_on_thumbnail_ready` 与 paint 的数据访问 |
| `scaled` | 351597 | 10.7 | 11.2 | 每次绘制都做平滑缩放 |
| `sticker_list_view_widget.py:367 _on_thumbnail_ready` | 7031 | 7.6 | 23.2 | 每个就绪信号扫描一次可见行 |
| `sticker_list_view_widget.py:96 paint` | 351596 | 6.3 | 456.9 | 整个主线程几乎都发生在 paint 链路内 |
| `provider.py:104 request_thumbnail` | 351596 | 2.5 | 381.7 | paint 内逐项请求缩略图 |
| `sqlite3 execute` | 13534 | 4.7 | 4.7 | 分页查询 + tags 懒加载（N+1） |
| `disk_storage.py:61 save_image` | 7031 | — | 25.8 | 工作线程写 PNG（含失败重试） |

工作线程：7031 个缩略图生成任务，共 160.3s，平均 22.8ms，最慢 970ms —— 生成管线本身健康，不是瓶颈。

## 5. 根因分析

### 5.1 主因：内存缓存容量 < 可见 item 数量，LRU 持续抖动

- 视口 3200x2000、item 48px + spacing 8px → 节距 56px → 约 57 列 x 36 行 ≈ **2050 个可见 item**
- `THUMBNAIL_CACHE_MAX_COUNT = 2000`（`commons/constants.py`），略小于可见数量
- 每次翻页新增 100 条 → 缓存淘汰约 100 条最久未用 → 新进入可见区的 item 基本都缓存未命中
- `StickerItemDelegate.paint()` 对每个可见 item 调用 `request_thumbnail()`（`sticker_list_view_widget.py`）；未命中时同步执行：
  - 有磁盘缩略图 → `load_disk_thumbnail`（`QPixmap(file)` 解码）
  - 小图（≤300px）→ `_generate_sync` 解码原图
- 于是每次整屏重绘 ≈ 2000 次“查缓存 → 未命中 → 磁盘解码”，累计 33.5 万次磁盘解码、227s，占主线程 66%

### 5.2 次因：翻页时为每张图创建无用的 QIcon

`build_sticker_items()` 为每个 item 创建 `QIcon(file_path)` 并写入 DecorationRole（13400 次、约 4.3ms/个、共 58.4s）。但 delegate 绘制时优先使用 `ROLE_BLOB_ENTITY` 走缩略图提供器，只有 blob 缺失才回退到 icon —— 实际场景中该 QIcon 从不用于绘制，属于纯浪费。

### 5.3 次因：缩略图就绪信号 O(可见行) 扫描

`_on_thumbnail_ready()` 对每个就绪信号都从首个可见行扫描到末个可见行（约 2000 行，每次调用 `index.data`），7031 次信号累计 1931 万次 `index.data` 调用（12.5s）。

### 5.4 次因：每次绘制都重新缩放

`paint()` 中每个 item 都执行 `pixmap.scaled(32,32,SmoothTransformation)`（35.2 万次、10.7s）。缩略图固定 200px，显示 32px，缩放结果没有缓存。

### 5.5 明确不是瓶颈的项

- SQLite 分页：`list_stickers` 约 61ms/页（含 tags N+1，134 页共 13534 次 execute，4.7s）
- 后台缩略图生成：平均 23ms/任务
- 内存：有界缓存，非泄漏

## 6. 附带发现

1. **iCCP 损坏导致缩略图保存反复失败**：hash `0b6c1c1c80db49f1d66db97f4a7709f4b4d41c27` 的源图 PNG iCCP 块损坏，`QImage.save(PNG)` 失败（`libpng error: Incorrect data in iCCP`）。由于保存失败不进入 `_failed_hashes`，该图在缓存淘汰后会反复重新生成、反复失败并刷屏日志。建议保存失败也计入失败集合（或跳过重试）。
2. **循环导入**：`services/sticker_library_viewer_service` 与 `ui/page_infinite_sticker_collection` 互相导入，独立脚本需要先导入 services 才能绕过；建议后续解耦。
3. **cProfile 与 Qt 线程池不兼容**：在主线程 cProfile 开启时再对工作线程 `run()` 挂 cProfile 会导致进程崩溃（exit 1，无输出），因此工作线程侧改为轻量计时统计（工具使用经验，供后续排查参考）。

## 7. 优化建议

### 7.1 快赢（改动小、见效大）

1. **按显示尺寸缓存 pixmap，并提高容量**：缩略图固定 200px（约 160KB/条），但网格显示只有 32-48px。改为在 delegate/provider 中缓存“按显示尺寸缩放后”的 pixmap（约 4KB/条），同样内存预算可缓存约 8 万条，彻底消除抖动；同时把 `THUMBNAIL_CACHE_MAX_COUNT` 提到与可见量匹配（如 4096-8192）。
2. **去掉 `build_sticker_items` 中的 `QIcon(path)` 创建**：delegate 只依赖 `ROLE_BLOB_ENTITY`；仅在 blob 缺失时再懒创建 icon。
3. **缩略图保存失败计入 failed 集合**：避免 iCCP 坏图反复生成与日志刷屏。

### 7.2 中等（结构性）

4. **paint 内不触发磁盘/解码**：`request_thumbnail` 在内存未命中时一律先返回占位图并异步请求（含磁盘缩略图路径），paint 只读内存缓存。
5. **`_on_thumbnail_ready` 用 hash → 可见行映射**：替代每信号扫 ~2000 行，消除 1931 万次 `index.data`。
6. **缓存缩放结果**：与 7.1-1 合并实现，paint 不再每次 `scaled`。

### 7.3 可选

7. **滚动预取**：按当前可见区间提前请求上下几屏缩略图，减少“滚到才解码”的卡顿。
8. **按需多档缩略图**：网格用 48/96px 小图、查看器用 200px，进一步降低内存与解码量。
9. **`list_stickers` 消除 tags N+1**（如 `selectinload`）：当前非瓶颈，但 134 页 1.3 万次查询属可清理项。

## 8. 验证与回归方法

修复前后使用同一命令对比（建议各跑 1 次 60 页）：

```
.venv\Scripts\python.exe experiments\profile_library_scroll.py --pages 60 --profile --profile-jobs --thumbnails build\profile_thumbnails
```

对比指标：

- `avg first 5 pages` 与 `avg last 5 pages`（目标：两者都显著下降，且末 5 页不再明显高于前 5 页）
- cProfile 中 `load_disk_thumbnail` 调用次数（目标：下降一个数量级以上）
- 进程内存峰值（目标：不因容量提高而失控，验证“按显示尺寸缓存”生效）
- 回看 4 个跳转点耗时

## 9. 产物清单

- `experiments/profile_library_scroll.py`：复现 + 剖析脚本（offscreen、`--pages/--profile/--profile-jobs/--thumbnails`）
- `experiments/profile_library_scroll_main.prof`：主线程 cProfile（可用 `snakeviz` 查看）
- `build/profile_run_output.txt`：60 页完整逐页明细
- `build/profile_thumbnails/`：测试库 thumbnails 的可写副本（避免剖析时写入测试库）
- `.gitignore` 已追加 `/experiments/*.prof`
