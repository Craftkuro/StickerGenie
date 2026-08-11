# image_text_extractor（OCR 集成）设计计划

## 文档状态

- 状态：设计已确认，等待实施
- 确认日期：2026-08-11
- 更新：2026-08-11 确认 Qt 适配器为刚需
- 关联文档：
  - `plans/image_features_extractor_rewrite_design.md`（子进程模式参考，不共享代码）
  - `plans/image_import_cancellation_refactor_plan.md`（导入取消语义）
- 实验结论：RapidOCR 3.9.2 默认 PP-OCRv6 small，中英日识别通过；单张 CPU 约 0.8~1 秒；模型随 wheel 打包，无需联网下载。

## 背景与目标

项目已有 `text_in_image` 字段和文本搜索（`search_stickers_by_text`），但导入时该字段固定为 `None`，只能手工填写。目标是在导入和数据库维护时自动识别图中文字，回填该字段。

约束：

- 不引入 PaddlePaddle / PyTorch，继续使用 onnxruntime。
- OCR 独立于向量生成，由复选框控制。
- 新建独立软件包 `image_text_extractor`，子进程管理单独实现，不与 `image_features_extractor` 共享抽象。
- 数据库维护只对 `text_in_image IS NULL` 的图片做 OCR，绝不覆盖手填文本。
- Qt 支持是刚需：新模块提供 Qt Signal/Slot 适配器，可直接在 UI 线程驱动 OCR 任务，不阻塞事件循环。

## 已确认的决策

1. 导入对话框新增“是否 OCR”复选框；未勾选时跳过 OCR 阶段，进度条相应跳段。
2. 单张图片 OCR 失败只跳过该图片；OCR 模块整体故障时跳过整个阶段；两种情况数据库都保持/写入 `NULL`。
3. 无有效文本（无文本或置信度全部 <= 0.9）时写 `NULL`，与向量生成“无结果不入库”的行为一致。
4. OCR 结果按 worker 返回的批次回填 SQLite，便于展示进度，也避免最后一次性写入。
5. 文本拼接规则（见下文），前缀 `[OCR]` 与正文之间不加空格；正文超过 4000 字截断（不计前缀长度）。
6. 新模块自带一套子进程管理（复制精简 `image_features_extractor` 的流程，不抽公共基类）。
7. 导入进度：0-5% 检查和去重，5-15% 写入数据库，15-40% OCR，40-100% 生成向量。
8. 数据库维护增加 OCR 功能，但只处理 `text_in_image` 为 NULL 的图片。
9. 导入和维护对话框 OCR 复选框均默认勾选
10. 维护任务顺序（建议：删孤立 Blob → OCR → 向量 → 删缩略图缓存）
11. 日文假名/谚文也按“中文类”无空格拼接
12. Qt 支持是刚需：提供与 `image_features_extractor` 一致的 Qt 适配器（`QtImageTextExtractor`），支持 start/cancel、信号通知，且不阻塞 Qt 事件循环。

## 导入总体流程

```text
检查文件/去重 (0-5%)
    ↓
写入 SQLite (5-15%)
    ↓
OCR (15-40%，可选；未勾选则跳过)
    ↓
生成向量 (40-100%，可选；未勾选则跳到最后)
    ↓
导入完成 (100%)
```

重复图片在写入 SQLite 前已被过滤（请求内去重 + 库内 hash 去重），不会进入 OCR 阶段。

OCR 阶段只处理“实际插入成功”的图片：`all_inserted_stickers_and_blob_paths`。图片已复制到 Blob 存储，OCR worker 与向量 worker 一样只接收绝对路径，不传输图片内容。

## 进度映射与跳段规则

导入常量改为：

```python
IMPORT_BATCH_SIZE = 32      # SQLite 写入批次（不变）
OCR_BATCH_SIZE = 8          # OCR IPC 批次
PREPROCESS_END_PERCENT = 5
SQLITE_END_PERCENT = 15
OCR_START_PERCENT = 15
OCR_END_PERCENT = 40
VECTOR_START_PERCENT = 40
```

四种组合的进度行为：

| 勾选 OCR | 勾选向量 | 进度序列 |
| --- | --- | --- |
| 否 | 否 | 0 → 5 → 15 → 100 |
| 是 | 否 | 0 → 5 → 15 → 40 → 100 |
| 否 | 是 | 0 → 5 → 15 → 跳到 40 → 100 |
| 是 | 是 | 0 → 5 → 15 → 40 → 100 |

状态文案：

- “正在检查图片和去重”
- “正在写入图库”
- “正在识别图片文字”
- “正在生成图片向量”
- “导入完成”

## 文本拼接规则

### 输入

OCR 每张图片返回若干 `(text, score)` 文本块。

### 过滤

- 只保留 `score > 0.9`（严格大于）且 `strip()` 后非空的文本块。
- 没有有效文本块时，最终写入 `NULL`（`None`）。

### 拼接

对过滤后的文本块依次拼接：

- 前一块最后一个字符是“中文类字符”时，直接拼接下一块，不加空格。
- 否则（英文、数字、符号等），在下一块前加一个空格。
- 每块先 `strip()`，避免积累前后空白；拼接完成后整体再 `strip()`。

“中文类字符”的判定范围（实现时用 Unicode 范围判断，不依赖额外字典）：

- CJK 统一表意文字及扩展区（`\u3400-\u4dbf`、`\u4e00-\u9fff`、`\uf900-\ufaff`）
- 日文假名（`\u3040-\u30ff`，平假名 + 片假名）
- 谚文（`\uac00-\ud7af`）

> 已确认：假名/谚文与汉字一样按“无空格拼接”处理；ASCII 字母、数字、标点都按“加空格”处理。

### 前缀与截断

```python
OCR_TEXT_PREFIX = "[OCR]"
OCR_TEXT_MAX_LENGTH = 4000

body = "".join(parts).strip()
if not body:
    return None
body = body[:OCR_TEXT_MAX_LENGTH]
return OCR_TEXT_PREFIX + body
```

- 前缀 `[OCR]` 与正文之间不加空格。
- 4000 字上限只约束正文；最终数据库值最长 4005 字符。
- 示例：
  - `"你好"` + `"世界"` → `"[OCR]你好世界"`
  - `"Hello"` + `"World"` → `"[OCR]Hello World"`
  - `"你好"` + `"world"` → `"[OCR]你好world"`
  - `"Hello"` + `"你好"` → `"[OCR]Hello 你好"`
  - 全部低于阈值 → `None` → 数据库 `NULL`

## 新模块：`src/image_text_extractor`

### 结构

```text
src/image_text_extractor/
├── __init__.py       # 公开 API
├── models.py         # 结果/进度/批次/摘要数据结构
├── worker.py         # spawn 入口 + RapidOCR 推理 + 文本拼接
├── extractor.py      # 父进程控制器（同步 + 流式）
├── qt.py             # Qt Signal/Slot 适配器
└── exceptions.py     # Job 级异常
```

导入和数据库维护服务在各自 QThread 中调用同步/流式 API；Qt 适配器供 UI 或其他需要信号驱动的调用方直接使用。

### 公开 API

```python
def iter_texts(
    image_paths,
    *,
    batch_size: int = 8,
    total: int | None = None,
    progress=None,
    started=None,
    timeout: float | None = None,
    cancel_event=None,
) -> Iterator[TextResultBatch]:
    ...

def extract_texts(...) -> list[ImageTextResult]:
    ...

def compose_ocr_text(items) -> str | None:
    """纯函数：文本块列表 -> 最终数据库字符串或 None。独立可测。"""
    ...
```

`QtImageTextExtractor` 通过 `__getattr__` 懒加载导出（与 `image_features_extractor` 一致），非 Qt 环境不强制导入 PyQt6。

### 数据结构

```python
@dataclass(frozen=True, slots=True)
class ImageTextResult:
    image_path: str
    success: bool          # True 表示 OCR 正常完成（text 可为 None = 无有效文本）
    text: str | None       # 成功时：str（以 [OCR] 开头）或 None；失败时：None
    error: str | None      # 成功时：None；失败时：非空字符串

@dataclass(frozen=True, slots=True)
class TextResultBatch:
    results: tuple[ImageTextResult, ...]
    progress: TextExtractionProgress

@dataclass(frozen=True, slots=True)
class TextExtractionProgress:
    completed: int
    total: int | None
    succeeded: int
    failed: int

@dataclass(frozen=True, slots=True)
class WorkerStartupInfo:
    engine_name: str = "onnxruntime"

@dataclass(frozen=True, slots=True)
class TextExtractionRequest:
    """Request object accepted by the Qt adapter."""

    image_paths: Iterable[str | os.PathLike[str]]
    batch_size: int = 8
    total: int | None = None
    timeout: float | None = None
    cancel_grace_seconds: float = 1.0
```

不变量：

- `success=True`：`error is None`；`text is None` 或 `text.startswith("[OCR]")`。
- `success=False`：`text is None`；`error` 非空。
- 成功但无文本（`text is None`）与失败（`success=False`）语义不同，调用方据此决定回填 `NULL` 或记录错误。

### Worker 行为

- 初始化：`RapidOCR(params={"Global.log_level": "WARNING"})`，默认模型即 PP-OCRv6 small。
- `process_image_batch(engine, image_paths)` 接收外部传入的 engine 对象，便于测试注入 fake engine。
- 每张图片单独调用 RapidOCR，单图异常只产生 `success=False`，不中断 Job。
- 图片结果经过 `compose_ocr_text` 后，以 `str | None` 形式返回。
- 批大小是 IPC 批次粒度（默认 8），不是模型 batch；RapidOCR 本身逐图推理。

### IPC 协议

复制 `image_features_extractor` 的握手机制：

```text
子进程 → 父进程: INIT_OK(engine_info)
子进程 → 父进程: INIT_ERROR(error)
子进程 → 父进程: REQUEST_BATCH
父进程 → 子进程: PROCESS_BATCH(paths)
父进程 → 子进程: END_INPUT
父进程 → 子进程: CANCEL
子进程 → 父进程: BATCH_RESULT(results)
子进程 → 父进程: JOB_ERROR(error)
子进程 → 父进程: DONE(cancelled)
```

- 取消/超时/资源回收语义与特征提取模块一致：正常 `join()`，宽限期后 `terminate()`/`kill()`。
- 每张图约 1 秒，取消最坏延迟约一个批次，可接受。
- `worker_process_entry` 保持模块顶层函数，满足 Windows spawn 和 PyInstaller 要求。

### 异常层次

```text
ImageTextExtractorError
├── WorkerInitializationError
├── WorkerCrashedError
├── TextExtractionTimeoutError
└── TextExtractionCancelledError
```

### Qt 适配器

`qt.py` 提供 `QtImageTextExtractor(QObject)`，设计对齐 `QtImageFeaturesExtractor`：

- 信号：`started(object)`、`progress_changed(object)`、`batch_ready(object)`、`finished(object)`、`failed(str)`、`cancelled()`。
- `start(request: TextExtractionRequest)` 立即返回，不阻塞事件循环；同一实例同一时间只允许一个 Job。
- 主线程通过 `QTimer` 轮询 IPC（默认 20ms），不在 Worker 子进程中创建或导入 Qt 对象。
- `cancel()` 对尚未开始的 pending 请求直接取消；对运行中的 Job 走 worker 取消协议。
- `close()` 在适配器销毁时同步释放运行中的 worker。
- `__init__.py` 通过 `__getattr__` 懒加载 `QtImageTextExtractor`，避免纯测试/脚本环境强制导入 PyQt6。

## 导入流程集成

### 请求与 UI

- `src/commons/signal_objects.py`：`ImportImagesRequest` 增加字段 `extract_text: bool = False`。
- `src/ui/dialog_image_import.ui`：在 `checkBoxDoVectorGeneration` 后新增：
  - `QCheckBox`，objectName `checkBoxDoTextExtraction`
  - 文本“识别图片中的文字（OCR）”
  - 默认勾选（已确认）
- `src/ui/dialog_image_import.py`：`_send_import_request()` 传入
  `extract_text=self.checkBoxDoTextExtraction.isChecked()`。

### 服务层

`src/services/import_images.py`：

- 新增常量（见上文）。
- 新增 `_extract_texts(...)`，签名与 `_generate_vectors` 对齐：

```python
def _extract_texts(
    stickers_and_blob_paths: list[tuple[StickerImage, str]],
    progress_callback: ProgressCallback | None = None,
    *,
    cancel_event: threading.Event | None = None,
    start_percent: int = OCR_START_PERCENT,
    end_percent: int = OCR_END_PERCENT,
) -> tuple[int, tuple[str, ...]]:
    ...
```

  - 从 `image_text_extractor` 导入 `iter_texts`、`TextExtractionCancelledError`。
  - 按 worker 批次：
    1. 逐结果找到对应 sticker；
    2. `success=False` → 错误收集到 `ocr_errors`；
    3. `success=True` → 更新内存 `sticker.text_in_image = result.text`，收集 `{sticker.id: result.text}`；
    4. 每批调用 `current_library_db.set_sticker_texts(mapping)` 回填；
    5. 用 `result_batch.progress.completed` 映射到 15-40% 进度，`last_file_name` 同步更新。
  - 捕获 `TextExtractionCancelledError` → 返回当前计数（由调用方统一返回 cancelled 结果）。
  - 捕获其他 Job 级异常（模型缺失、worker 崩溃等）→ `logger.exception`，错误追加到 `ocr_errors`，直接返回；**阶段跳过，不抛出**。未识别的行保持 `NULL`（插入时即为 `NULL`，无需显式写 NULL）。
- `import_images_with_result(...)` 增加参数 `extract_text: bool = False`：
  - SQLite 写入后、向量生成前执行 OCR；
  - OCR 与 `generate_vectors` 相互独立；
  - 进度按“四种组合”跳段。
- `ImportImagesResult` 增加：

```python
ocr_count: int = 0
ocr_errors: tuple[str, ...] = ()
```

- `_ImportImagesWorker.run()` 传入 `extract_text=self._request.extract_text`。

### 完成提示

`src/ui/main_window.py`：

- 导入完成消息追加“识别 N 张图片文字”；
- `ocr_errors` 与 `vector_errors` 一起展示警告。

## 数据库维护集成

### 选项与结果

`src/services/database_maintenance.py`：

- `DatabaseMaintenanceOptions` 增加 `extract_text: bool = False`；`__post_init__` 的“至少选择一项”校验包含它。
- `DatabaseMaintenanceResult` 增加：

```python
ocr_count: int = 0
ocr_errors: tuple[str, ...] = ()
```

### 只处理 NULL 文本

- `StickerMaintenanceRecord` 增加字段 `text_in_image: str | None = None`。
- `StickerDBV1.list_maintenance_records()` 的 SELECT 增加 `DBStickerImage.text_in_image`。
- 新函数 `_extract_missing_texts(...)`：
  - 过滤 `record.text_in_image is None` 的记录；
  - 通过 `blob_storage.read_file(BlobFileEntity(hash, extension))` 取得路径；
  - 调用 `iter_texts(...)`，按批回填 `database.set_sticker_texts(...)`；
  - 进度按“待 OCR 候选数”计算，`cancellable=True`；
  - 单图失败记录错误；模块级失败跳过阶段并记录错误，不中断整个维护；
  - 返回 `(ocr_count, ocr_errors, cancelled)`。

**覆盖保护**：只有 `NULL` 才 OCR；空字符串和非空文本都不处理，绝不覆盖手填内容。

### 任务顺序与进度修复

维护任务顺序：

```text
1. 删除孤立 Blob
2. 提取缺失文本（OCR，可选）
3. 生成向量（可选）
4. 删除缩略图缓存（可选）
```

同时修复现有 `run_database_maintenance` 的 `task_index` 递增问题：**每个实际执行的任务结束后都必须 `task_index += 1`**（当前向量任务未递增，导致向量与缩略图任务共用同一进度区间）。每个任务占用 `[i/task_count, (i+1)/task_count)` 的等分区间，`_overall_percent` 现有实现已支持，只需正确递增。

取消语义与现状一致：OCR 阶段取消后置位 `cancelled=True`；后续任务仍会执行（向量任务看到 `cancel_event` 会立即返回 cancelled）。

### 维护对话框

- `src/ui/dialog_database_maintenance.ui`：`groupBoxOperations` 内新增
  - `QCheckBox`，objectName `checkBoxExtractText`
  - 文本“为尚未识别文字的图片提取文字（OCR）”
  - 默认勾选（已确认：仅处理 NULL 文本）
- `src/ui/dialog_database_maintenance.py`：
  - `toggled` 接入 `_update_controls`；
  - `selected_options()` 增加 `extract_text=self.checkBoxExtractText.isChecked()`；
  - `_update_controls()` 的开始按钮启用条件包含该复选框。

### 完成提示

`src/ui/main_window.py`：

- `_database_maintenance_summary` 追加“识别 N 张图片文字”；
- 错误列表合并 `result.ocr_errors`。

## SQLite 层改动

`src/stickerdb/v1/sticker_db.py` 新增：

```python
def set_sticker_texts(self, text_by_sticker_id: dict[int, str | None]) -> None:
    """批量回填图片 OCR 文本；None 表示无有效文本。"""
    if not text_by_sticker_id:
        return
    with self._write_lock, self._get_session() as session:
        db_stickers = session.execute(
            select(DBStickerImage).where(
                DBStickerImage.id.in_(text_by_sticker_id)
            )
        ).scalars().all()
        for db_sticker in db_stickers:
            db_sticker.text_in_image = text_by_sticker_id[db_sticker.id]
        session.commit()
```

语义与 `set_sticker_vector_ids` 一致：按 id 批量更新，不存在的 id 静默忽略（导入/维护传入的 id 均来自库内记录）。

## 打包与分发

`StickerGenie.spec`：

- 增加 `rapidocr_datas, rapidocr_binaries, rapidocr_hiddenimports = collect_all("rapidocr")`，合并进 `datas` / `binaries` / `hiddenimports`。
- RapidOCR 模型位于 `rapidocr/models/`，`collect_all` 会把它带到 `_internal/rapidocr/models`；RapidOCR 按 `Path(__file__).parent / "models"` 解析，冻结环境可正常找到。
- 验证 opencv-python 的 PyInstaller hook 能收集 `cv2` 二进制；若构建后初始化失败，再补 `collect_all("cv2")`。
- 验证 `omegaconf` / `antlr4` / `shapely` / `pyclipper` 等被收集（主要靠 hook 与 collect_all）。
- `requirements.txt` 已包含 `rapidocr>=3.9.2`，无需改动。

根目录 `ocr_experiment.py` 保留为开发实验工具，不参与打包。

## 测试计划

### 单元测试（不依赖真实模型）

- `compose_ocr_text`：
  - 置信度阈值（0.9 严格大于）；
  - 中/日/韩字符结尾直接拼接；
  - 英文字母、数字、符号结尾加空格；
  - 每块 strip 与整体 strip；
  - 前缀无空格、4000/4001 字符截断；
  - 空列表、全低置信度 → `None`。
- `ImageTextResult` 不变量。
- `process_image_batch`：注入 fake engine，验证成功/失败/无文本结果与输入顺序一致。

### Qt 适配器测试

- `start()` 不阻塞 Qt 事件循环。
- `progress_changed` / `batch_ready` 可驱动进度与批量结果。
- 正常结束只发 `finished`；取消只发 `cancelled`。
- Job 级异常发 `failed`。
- 同一实例不能同时启动两个 Job。

### 集成测试（真实模型，可标记为慢速/手动）

- 用 `ocr_experiment.py` 的实验图片跑一次真实 worker，验证中英日识别与回填。
- 取消、超时、worker 崩溃的进程回收。

### 流程测试（手动/半自动）

- 导入四种勾选组合的进度序列。
- 导入取消于 OCR 阶段：图片已入库、文本为 NULL、返回 cancelled。
- 维护只处理 NULL 文本；手填文本不被覆盖。

## 实施顺序

1. 新建 `image_text_extractor` 包：models → worker → extractor → qt → `__init__.py`。
2. 单元测试：`compose_ocr_text`、结果不变量与 Qt 适配器行为。
3. SQLite：`set_sticker_texts` + 维护记录增加 `text_in_image`。
4. 导入集成：请求字段、UI 复选框、`_extract_texts`、进度映射、结果字段、完成提示。
5. 维护集成：选项/结果字段、`_extract_missing_texts`、任务顺序与 `task_index` 修复、UI 复选框、完成提示。
6. 打包：spec 收集 rapidocr，构建冒烟测试。
7. 用真实图片（中英日）端到端验证导入 + 搜索“文本模式”。

## 明确不做 / 边界

- 维护不覆盖非 NULL 文本；本次不提供“强制重跑 OCR”选项。
- 不实现 OCR 模型热切换、多 worker、自动 batch size。
- 不实现旧图批量补 OCR 的独立入口（维护中的“仅 NULL”已覆盖该需求）。
- 不抽公共子进程基类；`image_text_extractor` 与 `image_features_extractor` 各自维护。

## 已确认的实施参数（原待确认项）

- 导入/维护对话框 OCR 复选框均默认勾选。
- 维护任务顺序：删除孤立 Blob → OCR → 向量 → 删除缩略图缓存。
- 假名/谚文按“中文类”无空格拼接。
