# 图片导入中止简化实施计划

## 文档状态

- 状态：重新设计完成，等待实施
- 基线日期：2026-08-09
- 目标范围：图片导入后台任务、进度对话框、主窗口终态处理
- 核心原则：SQLite 是导入结果的唯一事实来源；中止后只要持久化状态合法，就不做跨存储回滚

> 实施前重新检查 `git status`。本文件本身是从其他分支复制后改写的已暂存新增文件；`StickerGenie Settings/` 和 `src/vit_b_16_features.onnx` 是现有未跟踪项，不属于本任务。

## 结论摘要

采用“小批次 Blob 复制 -> SQLite 原子提交 -> 可选向量生成”的流水线，并使用一个共享的 `threading.Event` 协作式中止。

中止的完成口径只有一个：`StickerDBV1.add_stickers()` 已经返回并实际写入 SQLite 的图片。已写入 SQLite 但尚未生成向量的图片直接保留；已经复制但尚未写入 SQLite 的 Blob 也允许保留为不可见、可复用的内容寻址文件。中止不删除 SQLite、Blob 或 Chroma 数据，不引入补偿事务。

相较旧方案，本计划明确删除以下设计：

- Blob staging、`.part`、提交回执和 manifest；
- 中止时反向删除 Chroma、SQLite、Blob；
- 启动时恢复未完成导入任务；
- job ID 和迟到信号路由；
- 为本功能拆分 models/job 模块；
- 修改 BlobStorage、StickerDBV1、metadata hash 工具或启动流程。

## 当前实现基线

当前代码已经确认：

1. `ImageImportService` 为每个请求创建 `QThread`，worker 同步执行 `import_images_with_result()`。
2. 导入先预处理全部路径、计算 metadata/hash 并过滤重复项。
3. 当前实现先复制所有候选图片到正式 Blob，再用一次 `add_stickers()` 写入 SQLite。
4. `add_stickers()` 在一个 SQLAlchemy session 中 `flush()` 后 `commit()`，单次调用是原子的；图片 hash 有唯一约束。
5. 向量生成当前在 SQLite 提交后进行；`set_sticker_vector_ids()` 也在单独事务中原子提交。
6. `extract_features(..., cancel_event=...)` 已支持共享取消事件；取消时抛出 `ExtractionCancelledError`，并在 `finally` 中回收特征提取子进程。
7. `BlobStorage.store_file()` 使用 hash 寻址，目标存在时直接复用；单次 `shutil.copy2()` 不能协作式中断。
8. 进度对话框目前没有按钮，并禁止关闭按钮和 Esc 关闭。

当前分支没有检索到“补齐缺失向量”的维护入口。本计划只把 SQLite-only 图片定义为合法状态，不把维护功能纳入本次实现；维护功能落地前，这类图片仍可正常显示和管理，但相似图片查找会提示尚无特征向量。

## 目标

- 进度对话框提供“中止”按钮。
- 点击后立即设置线程安全的取消事件，不依赖 worker 所在线程处理 queued slot。
- worker 在当前不可中断操作返回后尽快停止后续工作。
- 中止是正常、独立的终态，不显示为“导入失败”。
- 中止结果和进度都只以 SQLite 实际插入记录为准。
- 已写入 SQLite 的图片全部保留，即使 `vectordb_id` 仍为空。
- 正常完成、失败、中止三种终态互斥，线程和特征提取子进程最终被回收。
- 保留现有重复 hash 静默跳过、向量失败不导致图片导入失败等行为。

## 非目标

- 不保证点击按钮后立即打断当前文件的 metadata/hash 读取或 Blob 复制。
- 不回滚任何已经提交的 SQLite 记录。
- 不清理由当前中止留下的无 SQLite 引用 Blob。
- 不为中止补偿或删除已经写入的 Chroma 向量。
- 不实现暂停、恢复、断点续传、崩溃恢复或多任务并发导入。
- 不在本次实现向量维护功能。
- 不使用 `QThread.terminate()`。

## 合法状态边界

| 状态 | 是否允许 | 处理方式 |
|---|---:|---|
| Blob + SQLite + Chroma + `vectordb_id` | 是 | 完整图片 |
| Blob + SQLite，`vectordb_id` 为空 | 是 | 保留；以后由维护操作补向量 |
| Blob 存在，SQLite 无记录 | 是 | 对用户不可见；以后相同 hash 导入可直接复用 |
| SQLite 有记录，Blob 不存在 | 否 | 始终先完成 Blob 复制，再提交 SQLite |
| Chroma 已写入，但 SQLite 未回填 vector ID | 不作为稳定终态 | Chroma add 一旦开始，即使收到中止也继续完成 SQLite 回填 |
| SQLite `vectordb_id` 指向不存在的 Chroma 记录 | 否 | 沿用现有回填失败时删除刚写入向量的逻辑 |

这里的“丢弃”是停止继续处理并接受上述合法状态，不是删除或恢复到任务开始前。

## 导入流水线

### 批次选择

在 `src/services/import_images.py` 内增加一个固定的小批次常量，例如：

```python
IMPORT_BATCH_SIZE = 32
```

每个批次依次完成 Blob、SQLite 和可选向量阶段，再开始下一批。这样不需要修改存储模块，同时把中止时缺向量的 SQLite 记录限制在当前批次内。测试可以临时将批次大小设为 1，精确覆盖阶段边界。

### 预处理

1. 发出 0%、“正在预处理”。
2. 在开始以及每个输入路径之间检查 `cancel_event`。
3. 沿用现有 `get_image_metadata()`、请求内 hash 去重和 SQLite 已有 hash 查询。
4. 本阶段不改 metadata/hash 工具，因此读取单个大文件时需要等当前读取结束后才能响应中止。
5. 中止时直接返回 SQLite 导入数为 0 的取消结果。

### 每批处理

对预处理后的候选项按输入顺序分批：

1. 批次开始前检查取消事件。
2. 逐张调用现有 `store_file()`，并在每张复制前后检查事件。
3. 若复制阶段中止，不提交当前批次 SQLite；已完成的 Blob 文件保留。
4. 所有 Blob 就绪后，再次检查事件。
5. 一次调用 `add_stickers(batch)` 原子提交当前批次。
6. 调用返回后立即把返回 DTO 加入 `imported_stickers`，无论调用期间是否刚收到中止请求。
7. 用 SQLite 实际插入数更新进度和最后完成文件名。
8. 若此时已中止，保留当前批次 SQLite 记录并返回取消结果，不生成本批向量。
9. 若要求生成向量，为当前批次调用 `_generate_vectors()`。
10. 向量阶段结束后检查事件；已中止则不开始下一批。

批次提交仍保留 SQLite 的原子性。中止可能发生在 `add_stickers()` 执行期间，但只能在调用返回后观察到；此时该批次要么整体失败，要么以实际返回的插入记录计入结果，绝不猜测或回滚。

### 向量阶段

保留现有 `_generate_vectors()` 的批量结构，只做以下调整：

- 接受并向 `extract_features()` 传递同一个 `cancel_event`；无需改用 `iter_features()`。
- 单独捕获并重新抛出 `ExtractionCancelledError`，不能把中止转成普通 `vector_errors`。
- 特征提取完成后、调用 Chroma `add_batch()` 前检查取消事件；已中止则不写向量。
- Chroma `add_batch()` 一旦开始，不在它与 `set_sticker_vector_ids()` 之间因中止提前返回。先完成回填，再进入 cancelled 终态，避免产生稳定的 Chroma-only 记录。
- SQLite 回填失败时，继续沿用现有 `delete_batch(vector_ids)` 清理逻辑；这是错误处理，不是中止补偿。
- 向量阶段的进度回调不再改变百分比或 `completed`，只更新状态为“正在生成图片向量”。

如果特征提取期间中止，`extract_features()` 不会返回部分结果，因此本批不会写入 Chroma；本批 SQLite 记录保留且 `vectordb_id` 为空。

## 取消契约

### 结果模型

在现有 `ImportImagesResult` 增加：

```python
cancelled: bool = False
```

不增加 `requested_count`、`invalid_count`、`discarded_count` 或 `cleanup_errors`。取消结果继续携带现有字段，但 UI 的中止汇报只使用：

```python
len(result.imported_stickers)
```

`imported_stickers` 必须严格等于本任务中由 `add_stickers()` 实际返回的 DTO；不能包含只完成 Blob 或向量计算的图片。

### Service 与 worker

1. `ImageImportService` 第一版只允许一个活动任务；活动时再次 `start_import()` 抛出明确错误。
2. `start_import()` 创建 `threading.Event`，并把它直接交给 worker。
3. `cancel_import()` 不接收 job ID；它在 GUI 线程直接调用 `event.set()`，首次有效请求返回 `True`，无活动任务或重复请求返回 `False`。
4. 不把 `cancel()` 做成 worker 的 Qt slot，因为 `run()` 执行期间该线程无法处理 queued slot。
5. worker 根据 `result.cancelled` 发出 `cancelled(result)` 或 `succeeded(result)`，异常仍发 `failed(str)`。
6. service 增加 `import_cancelled` 信号，并为 cancelled 连接与 finished/failed 相同的 `thread.quit()`、`worker.deleteLater()` 路径。
7. 同一任务只发一个终态。若工作已越过最后一个取消检查点，正常完成可以先于迟到的取消请求生效。

不引入 job ID：进度对话框是 application-modal，service 又强制单活动任务，当前 UI 中不存在两个导入任务信号交错的合法场景。

## 进度和结果口径

### 进度

- 预处理期间保持 0%。
- 候选总数确定后，`total` 为预处理过滤后的候选数。
- 每次 SQLite 批次提交返回后：

```text
completed = 本任务已实际插入 SQLite 的图片数
percent = floor(100 * completed / candidate_count)
```

- `last_file_name` 是最后一张实际写入 SQLite 的图片。
- Blob 复制、hash 计算、特征提取、Chroma 写入均不增加 `completed` 或 percent。
- 当全部候选已写入 SQLite 时，进度可以到 100%；若仍在生成向量，状态文字继续显示该阶段。
- 中止不把未完成进度强制改成 100%。

若运行期出现极少数并发 hash 冲突，最终正常终态仍显示 100%，实际导入数以 SQLite 返回值为准；本应用内部通过单活动任务避免这种竞争。

### 中止汇报

主窗口关闭进度对话框、按需刷新图库，然后显示：

```text
导入已中止，已导入 X 张图片。
```

这里的 X 只来自 SQLite。中止消息不报告“丢弃数”、重复数、无效文件数或向量数，避免为了统计继续扫描或把非 SQLite 状态混入完成口径。

正常完成继续沿用现有“已导入 X、重复 Y”和向量警告逻辑。

## 进度对话框和主窗口

### `ImageImportProgressDialog`

- 在 `.ui` 底部增加“中止”按钮。
- 增加无参数 `cancel_requested` 信号。
- 第一次点击后立即禁用按钮，状态切换为“正在中止”。
- 按钮禁用后重复点击不再发信号。
- 进度条保持最后值；对话框等待 worker 的终态信号。
- 中止过程中仍禁止标题栏关闭和 Esc；只有现有 `finish()` 可以关闭。

### `MainWindow`

- 创建进度对话框后，把 `cancel_requested` 连接到 `ImageImportService.cancel_import()`。
- 连接 service 的 `import_cancelled` 到新增 `_on_import_images_cancelled()`。
- cancelled 回调先关闭对话框；SQLite 导入数大于 0 时刷新图库；显示独立的中止消息。
- finished 和 failed 路径保留现有行为。
- 活动任务保护失败时要关闭刚创建的进度对话框并显示错误，不能留下无法关闭的模态框。

## 文件改动范围

### 修改

- `src/services/import_images.py`
  - 增加批次常量、取消事件参数和检查点；
  - 按小批次执行 Blob、SQLite、向量阶段；
  - 进度改为 SQLite 口径；
  - 增加 cancelled 结果、worker/service 信号和单活动任务保护。
- `src/ui/dialog_image_import_progress.ui`
  - 增加“中止”按钮并调整稳定尺寸。
- `src/ui/dialog_image_import_progress.py`
  - 增加取消信号、按钮幂等状态和“正在中止”展示。
- `src/ui/main_window.py`
  - 转发取消请求，处理 cancelled 终态和部分导入刷新。
- `tests/test_import_images_vectors.py`
  - 更新进度断言，验证 `cancel_event` 传入特征提取器。
- `tests/test_image_import_progress_dialog.py`
  - 覆盖按钮、重复点击和关闭拦截。
- `tests/test_main_window_image_import.py`
  - 覆盖取消转发、取消结果和活动任务错误。
- `tests/test_image_import_cancellation.py`（新增）
  - 集中覆盖流水线各阶段的取消语义和 SQLite 结果口径。

### 不修改

- `src/blob_storage/*`
- `src/stickerdb/v1/*`
- `src/stickerdb/vectordb/*`
- `src/image_features_extractor/*`
- `src/utils/image_metadata.py`
- `src/services/startup.py`
- `src/commons/signal_objects.py`

如果实施时发现必须修改这些模块，应先验证是否真的无法通过现有原子 API 和取消事件完成；不得重新引入旧方案的 staging/manifest/补偿架构。

## 实施顺序

### 阶段 0：锁定基线

1. 检查工作树，确认不覆盖其他分支复制来的文件或用户改动。
2. 运行现有导入 service、进度对话框、主窗口和特征提取器测试。
3. 固定现有正常导入、重复过滤和向量失败降级行为。

### 阶段 1：导入核心中止

1. 给结果增加 `cancelled`，给导入函数和 `_generate_vectors()` 增加 `cancel_event`。
2. 将候选项按固定批次执行 Blob -> SQLite -> vector。
3. 在批次和单文件边界增加检查点。
4. 将进度改为 SQLite 实际插入数。
5. 正确区分 `ExtractionCancelledError` 与普通向量错误。

### 阶段 2：service 生命周期

1. 增加共享 `threading.Event` 和单活动任务约束。
2. 增加幂等 `cancel_import()`、worker cancelled 和 service `import_cancelled`。
3. 确认三种终态都退出线程并清空活动任务。

### 阶段 3：UI 闭环

1. 增加中止按钮与对话框 cancelling 状态。
2. 主窗口转发取消并处理 cancelled 结果。
3. 验证有 SQLite 部分结果时刷新图库，无结果时不刷新。

### 阶段 4：回归验证

1. 运行全部单元测试和 Qt 离屏测试。
2. 显式运行已有真实模型 + 临时 Chroma 集成测试。
3. 手工验证大量图片导入时的按钮响应、进度口径和终态消息。

## 测试矩阵

| 场景 | 预期 SQLite/结果 | 允许的其他状态 |
|---|---|---|
| 开始前中止 | 0 张，cancelled | 无新增 |
| 单个 metadata/hash 读取时中止 | 当前读取结束后停止，0 张 | 无新增 |
| 批次 Blob 复制中止 | 当前批不写 SQLite | 已复制 Blob 可保留 |
| SQLite 提交前中止 | 当前批不写 SQLite | 当前批 Blob 可保留 |
| SQLite 提交期间中止 | 调用返回的整批计入 imported | 当前批可无向量 |
| SQLite 提交后中止 | 已提交批次全部计入 imported | 当前批可无向量 |
| 特征提取期间中止 | 当前及之前 SQLite 均保留 | 当前批无向量，子进程被回收 |
| Chroma add 前中止 | SQLite 保留 | 不写当前批向量 |
| Chroma add 期间中止 | SQLite 保留 | 完成 vector ID 回填后再 cancelled |
| SQLite vector ID 回填期间中止 | SQLite 保留 | 回填事务结束后再 cancelled |
| 两批之间中止 | 前批计入 imported，后批不开始 | 前批向量按已完成状态保留 |
| 最后检查点之后点击中止 | finished 或 cancelled 二选一 | 不发双重终态 |

还必须覆盖：

- `imported_stickers` 与任务实际新增的 SQLite 行严格一致；
- progress 的 `completed`、percent 和 `last_file_name` 只随 SQLite 插入变化；
- 向量提取进度不会改变导入百分比；
- 请求内和图库内重复 hash 仍被静默忽略；
- vector store 错误仍降级为 SQLite-only 图片；
- 重复调用 `cancel_import()` 幂等；
- 一个活动任务期间拒绝第二个任务；
- finished、cancelled、failed 只发一个；
- 终态后 `active_job_count == 0`；
- 中止后无遗留 `ImageFeaturesExtractorWorker` 子进程；
- 中止按钮只发一次信号，终态前窗口仍不可关闭；
- 中止回调只在 SQLite 实际新增大于 0 时刷新图库。

## 验收标准

1. 用户可以在图片导入进度对话框点击“中止”。
2. 点击后按钮立即禁用，后台在当前不可中断调用返回后停止。
3. 不调用 `QThread.terminate()`，特征提取使用已有 `cancel_event` 契约。
4. 中止结果中导入数量与本任务实际写入 SQLite 的数量一致。
5. 中止后的持久化状态只落在“合法状态边界”表中允许的状态。
6. 不执行跨 Blob、SQLite、Chroma 的中止回滚或补偿。
7. SQLite-only 图片可正常显示、编辑、删除；相似查找明确提示缺少向量。
8. 进度百分比只由 SQLite 完成数计算。
9. 正常完成、失败、中止终态互斥，QThread 和特征子进程都被回收。
10. 本计划列出的定向测试、全套测试和真实模型集成测试全部通过。

## 已接受的权衡

- 点击中止后可能需要等待当前 metadata/hash、Blob copy、SQLite 或 Chroma 同步调用返回。
- 中止可能留下没有 SQLite 引用的 Blob，占用少量磁盘；它不会出现在图库中，并可被相同 hash 的未来导入复用。
- 当前批次可能只写入 SQLite 而没有向量；批次大小限制了常规中止下的数量。
- 进度到 100% 后可能仍短暂处于“正在生成图片向量”，因为进度定义为 SQLite 导入进度，而不是整个后台任务耗时。
- 当前分支尚无缺向量维护入口；自动补齐属于后续维护功能，不阻塞本次中止能力。

## 实施起点

下一轮从阶段 0 开始，然后先完成导入核心和 service 取消事件，再接 UI 按钮。不要从旧计划恢复 staging、manifest、job ID 或补偿删除逻辑，除非新的明确需求重新要求跨存储一致性。
