# batch_job_runner（子进程流水线任务调度）设计

## 文档状态

- 状态：设计已确认，等待实施
- 日期：2026-08-15
- 范围：新建通用子进程流水线调度模块 `src/batch_job_runner`；重构图片导入/数据库维护中的 OCR 与向量生成；删除旧提取器包
- 关联文档：
  - `plans/image_features_extractor_rewrite_design.md`（旧子进程方案，借鉴来源）
  - `plans/image_text_extractor_design.md`
  - `plans/image_import_batch_performance_design.md`
  - `plans/image_import_cancellation_refactor_plan.md`

## 背景与目标

### 现状与问题

图片导入/数据库维护中的 OCR 与向量生成目前分别由 `image_text_extractor` 和
`image_features_extractor` 两套独立的子进程实现。两套实现结构相同：父进程按批次
准备数据，子进程收到一批后串行处理，处理完一批再向父进程拉取下一批（pull 协议）。

主要局限：

- 子进程内部 Python 调度串行执行；虽然图片解码、OCR、ONNX 推理等底层 C 函数会
  释放 GIL，但同一时刻只有单线程在跑，多核利用不充分。
- 进度按批次粒度更新，粒度粗。
- 取消只能等当前整批处理完，响应慢。

### 目标

- 新建通用模块 `batch_job_runner`：在子进程内用线程池并行调度流水线步骤，OCR 与
  向量生成共用一套实现。
- 主模块以同步模式运行：接收一个可迭代数据集，通过同步接口（返回全部结果）或
  迭代接口（逐批产出）输出结果；不包含 Qt 适配与外部 job 管理。
- 保留旧方案中经过验证的设计：优雅停止、单线程管道处理、Job 级进程回收、初始化
  握手、错误分层、取消/超时语义。

## 已确认的决策

1. **结果顺序不做要求**：不保证输出顺序与输入顺序一致，不添加顺序断言。
2. **取消时最后一步在途结果仍送出**：收到中止信号后，所有内部队列内容丢弃；最后
   一步正在执行函数的结果完成一条送出一条；其余中间结果可丢弃。
3. **框架支持每步 batch_size**：声明 `batch_size > 1` 时，该步函数接收列表、返回
   等长列表。
4. **GeneralDataWrapper 携带 stage_name**：记录出错步骤，便于定位。
5. **批次函数异常按数据级处理**：单批次异常将该批所有 item 标记失败，不中断 job；
   不做"连续批次失败升级 job 级失败"的额外检查。
6. **旧 extractor 包删除**：`image_text_extractor` / `image_features_extractor`
   旧实现删除，不做兼容层。
7. **OCR 采用 pool_size=1、batch_size=1**：实测单线程已能充分利用性能。
8. **本模块不含 Qt**：Qt 支持与外部 job 管理由调用方负责；本模块只负责"接收可迭代
   数据集 -> 输出结果"。
9. **进度不再显示最后处理文件名**：改造后进度只显示已处理数量和总数量，不再显示
   `last_file_name`。

## 借鉴旧方案的设计（保留）

- Job 级进程生命周期：每个 Job 一个子进程；正常/失败/取消/超时后进程被 join，
  必要时 terminate/kill，由操作系统完整回收模型与图片资源。
- 单线程持有管道：父进程、子进程各只有一个线程做 poll/recv/send，没有并发管道 I/O。
- 消息信封 `(kind, payload)` + 模块级消息常量；未知消息按协议错误处理。
- 初始化握手 `INIT_OK/INIT_ERROR`：初始化失败（模型缺失、引擎加载失败）是 job 级
  失败，与单条数据失败严格区分。
- 错误字符串统一 `"TypeName: message"` 格式。
- 主进程只传路径，子进程自行读取文件。
- 每张图失败隔离的语义保留（实现方式改为 GeneralDataWrapper）。
- 取消宽限与超时 deadline 语义。

## 模块结构

```
src/batch_job_runner/
  __init__.py       # 公开导出
  models.py         # GeneralDataWrapper、QueueSpec、StageSpec、PipelineSpec、
                    #   ResultBatch、JobProgress、JobSummary
  scheduler.py      # 子进程入口 scheduler_entry(conn, spec)、stage worker 循环、
                    #   IPC 消息常量
  job.py            # 主进程 BatchJobRunner 基类（iter_results / run）与进程生命周期
  exceptions.py     # WorkerInitializationError / WorkerCrashedError /
                    #   JobCancelledError / JobTimeoutError / JobError
```

feature 包改造后：

```
src/image_text_extractor/
  stages.py         # ocr_image(path) 等 stage 函数 + 子进程内 lazy 引擎单例 + 文本拼接逻辑
  runner.py         # OcrBatchJobRunner(BatchJobRunner)
  __init__.py       # 保留必要导出（文本拼接、路径规范化等）
  （删除 worker.py / extractor.py / qt.py / models.py 旧实现）

src/image_features_extractor/
  stages.py         # preprocess_image(path) / run_batch_inference(list) +
                    #   lazy session 单例 + provider 选择
  runner.py         # VectorBatchJobRunner(BatchJobRunner)
  model_specs.py    # 保留（ImageFeatureModelSpec 等）
  __init__.py       # 保留必要导出（模型规格等）
  （删除 worker.py / extractor.py / qt.py / models.py 旧实现）
```

## 数据契约（models.py）

### GeneralDataWrapper

```python
@dataclass(frozen=True, slots=True)
class GeneralDataWrapper:
    data: Any
    hasException: bool = False
    error: str | None = None        # 格式 "TypeName: message"
    stage_name: str | None = None   # 出错步骤名
```

规则：

- 队列中流动的元素统一为 `GeneralDataWrapper`；feeder 对父进程发来的原始 item 先包
  一层，此后各步骤 worker 循环完全对称。
- 用户函数只见裸数据（`func(wrapper.data)`），调度模块负责包装。
- 成功：`GeneralDataWrapper(data=函数输出)`。
- 异常：`GeneralDataWrapper(data=本步输入, hasException=True, error=...,
  stage_name=...)`，不调用函数。
- 上游已失败的 wrapper 由下游步骤原样转发，不进入用户函数。
- 中间数据应自带输入标识（如图片路径），任何一步失败父进程都能定位。

### QueueSpec / StageSpec / PipelineSpec

```python
@dataclass(frozen=True, slots=True)
class QueueSpec:
    name: str
    maxsize: int                # 背压与内存控制

@dataclass(frozen=True, slots=True)
class StageSpec:
    name: str
    input_queue: str
    output_queue: str
    func: Callable              # 必须模块级可导入（spawn 可 pickle）
    pool_size: int              # 本步线程数，>= 1
    batch_size: int = 1         # > 1 时 func 接收 list、返回等长 list

@dataclass(frozen=True, slots=True)
class PipelineSpec:
    queues: tuple[QueueSpec, ...]
    stages: tuple[StageSpec, ...]
    setup_func: Callable | None = None   # 子进程启动时执行一次；返回值随 INIT_OK 发出
    result_batch_size: int = 32          # 子进程攒多少条最终结果发一次
```

约束：

- 队列名唯一；stage 按队列首尾相接成一条链；第一个 stage 输入为首队列，最后一个
  stage 输出为尾队列；至少一个 stage。
- 每个输入 item 恰好产生一个输出 wrapper（1:1），保证完成判定与进度计数简单。
- `batch_size > 1` 时 worker 先阻塞取 1 条，再 `get_nowait()` 尽量凑满；流尾不足
  一批按实际数量处理，不空等。

## 子进程调度模块（scheduler.py）

### 启动流程

```
scheduler_entry(conn, spec):
  校验 spec（队列唯一、连接成链、pool_size/batch_size >= 1、至少一个 stage）
  执行 setup_func（若声明）；失败 -> 发 INIT_ERROR("TypeName: message") 并退出
  创建 Queue；为每个 stage 启动 pool_size 个 worker 线程
  发 INIT_OK(startup_info)（setup_func 返回值或 None）
  进入调度循环
```

### Worker 循环（每个线程）

```python
while not stop_event.is_set():
    wrappers = 取 1 条（get(timeout=0.05)）；batch_size > 1 时再 get_nowait 凑批
    if 空: continue
    in_flight += len(wrappers)
    if stop_event.is_set() and 本步不是最后一步:
        in_flight -= len(wrappers); continue        # 取消后中间结果丢弃
    failed, good = 拆分（failed 原样转发，good 进入函数）
    try:
        outs = func([w.data for w in good])          # batch_size=1 时单条
    except BaseException as e:
        outs = [失败 wrapper（data=原输入, stage_name=本步）] * len(good)
    输出 = failed 透传 + outs 包装结果（保持 1:1）
    in_flight -= len(wrappers)
```

- 线程用 `threading.Thread` 直接管理（需要 stop_event + 非阻塞 get + in_flight
  计数，不用 ThreadPoolExecutor）。
- `in_flight` 为所有 worker 共用的计数（锁保护），仅取消路径使用。

### 调度循环（唯一持有管道的线程）

```
fed = 0; drained = 0; input_exhausted = False
received_items = False; request_sent = False
while True:
    if conn.poll(0.05):
        kind, payload = conn.recv()
        ITEMS     -> 逐个包成 GeneralDataWrapper 放入 queue[0]；fed += n；
                    received_items = True; request_sent = False
        END_INPUT -> input_exhausted = True; request_sent = False
        CANCEL    -> 进入取消路径
        其他      -> 发 JOB_ERROR 并退出
    if request_sent:
        continue        # 已请求下一批输入；结果发送暂停，等待 ITEMS/END_INPUT
    drain 尾队列：攒满 result_batch_size 或流尾 -> conn.send(RESULT_BATCH, wrappers)；drained += n
    if input_exhausted and fed == drained:
        conn.send(DONE, False); break
    if not input_exhausted and received_items and 尾队列空:
        conn.send(REQUEST_INPUT); request_sent = True
```

- 管道只由调度线程访问（send/recv 同线程），worker 线程只碰队列。
- 正常完成判定 `fed == drained` 充分：drain 与检查在同一轮迭代，任何在途 item
  都会使 `fed > drained`。

### 取消路径

收到 `CANCEL` 后：

1. `stop_event.set()`；`clear()` 所有队列（丢弃排队内容）。
2. worker 处理完当前 item 后：中间步骤结果丢弃，最后一步结果仍放入尾队列。
3. 调度线程持续 drain 尾队列并发送，直到 `in_flight == 0` 且尾队列空。
4. 发 `DONE(True)`，退出。

- 取消尾有界：最多 `pool_size[最后一步]` 条在途结果被送出。
- 兜底：等待 `in_flight == 0` 时带超时（默认 1s），超时直接发 `DONE(True)` 退出；
  父进程 join 超时后 terminate/kill 回收。

### 错误分层

| 层级 | 触发 | 表现 |
| --- | --- | --- |
| job 级 | setup_func 异常 | INIT_ERROR -> WorkerInitializationError |
| job 级 | 协议错误 / 未知消息 | JOB_ERROR -> JobError |
| job 级 | 进程崩溃 / EOF | WorkerCrashedError |
| 数据级 | stage 函数异常 | GeneralDataWrapper.hasException=True，job 不中断 |

## 主进程模块（job.py）

### BatchJobRunner 基类

```python
class BatchJobRunner:
    def build_pipeline(self) -> PipelineSpec: ...    # 子类必须实现

    def iter_results(self, items, *, total=None, cancel_event=None,
                     progress=None, started=None, timeout=None,
                     cancel_grace_seconds=1.0) -> Iterator[ResultBatch]:
        """逐批产出结果；取消时抛 JobCancelledError。"""

    def run(self, items, *, total=None, cancel_event=None,
            progress=None, started=None, timeout=None,
            cancel_grace_seconds=1.0) -> JobSummary:
        """收集全部结果；取消时返回 cancelled=True 的 summary。"""
```

- `ResultBatch`：`(results: tuple[GeneralDataWrapper, ...], progress: JobProgress)`。
- `JobProgress`：`completed / total / succeeded / failed`（succeeded = not hasException）。
- `JobSummary`：`results / completed / succeeded / failed / cancelled / duration_seconds`。
- `items` 支持可迭代；`total` 缺省时若 items 为 Sized 取 len。
- `iter_results` 是生成器，调用方提前 break 时在 finally 中回收进程。
- `run()` 内部迭代 `iter_results` 并捕获 `JobCancelledError`，返回 cancelled summary。

### 进程生命周期（沿用旧方案）

- spawn 上下文 + duplex Pipe；父进程启动后关闭子端 connection；`daemon=False`。
- 终态回收：`join(1.0)` -> `terminate()` -> `kill()`，保证无遗留子进程。
- 超时：deadline 在 poll 循环检查，超时终止进程并抛 `JobTimeoutError`。
- 取消：cancel_event 置位后发 `CANCEL`，继续 poll/recv 直到 `DONE(True)`。

### 背压与防死锁

- 父进程只在收到 `INIT_OK` 或 `REQUEST_INPUT` 后下发一个 ITEMS 批（32 条），
  不在收到结果后主动继续推送输入。
- 子进程发出 `REQUEST_INPUT` 后暂停发送 `RESULT_BATCH`，直到收到下一批
  `ITEMS`（或 `END_INPUT`）；两个方向的大消息不会同时进入管道。
- 队列 maxsize 提供子进程内背压；管道内 in-flight 总量有界（约等于首队列
  maxsize + 一个 ITEMS 批）。

## IPC 协议

```
父 -> 子:  ITEMS(tuple) / END_INPUT / CANCEL
子 -> 父:  INIT_OK(startup_info) / INIT_ERROR(str)
           / REQUEST_INPUT
           / RESULT_BATCH(tuple[GeneralDataWrapper, ...])
           / DONE(cancelled: bool) / JOB_ERROR(str)
```

## 场景映射

### OCR（1 步，已确认 pool_size=1、batch_size=1）

```python
PipelineSpec(
    queues=(QueueSpec("input", 64), QueueSpec("output", 64)),
    stages=(StageSpec("ocr", "input", "output", ocr_image, pool_size=1, batch_size=1),),
    setup_func=load_ocr_engine,     # 返回 engine_name 等 startup info
)
```

- item = blob 绝对路径；成功 data = 拼接后的文本（`str | None`），失败由 wrapper
  标记。
- `compose_ocr_text` 等文本拼接逻辑迁入 `stages.py`。

### 向量生成（2 步）

```python
PipelineSpec(
    queues=(
        QueueSpec("input", 64),
        QueueSpec("preprocessed", 16),   # tensor 约 590KB/条，maxsize 保守
        QueueSpec("inferred", 8),
    ),
    stages=(
        StageSpec("preprocess", "input", "preprocessed", preprocess_image, pool_size=4),
        StageSpec("infer", "preprocessed", "inferred", run_batch_inference, pool_size=1, batch_size=32),
    ),
    setup_func=load_session,            # 返回 providers/input_name/model_name 等
)
```

- stage1：逐图预处理（解码/缩放/归一化，CPU 并行，释放 GIL）；item = 路径，
  输出 `(path, tensor)`。
- stage2：批量推理；输入 `list[(path, tensor)]`，输出 `list[(path, vector)]`；
  `pool_size=1` 避免 onnxruntime intra-op 线程叠加超订。
- 推理阶段批量异常 -> 该批所有 item 标记失败（决策 5）。

## 调用方改造

### import_images.py

- `_extract_texts` / `_generate_vectors` 改用对应 runner 的 `iter_results`（或
  `run`）。
- 消费逻辑（按 path 映射回 sticker、批量写 SQLite/Chroma、进度区间映射 15-40 /
  40-100）基本不变。
- 进度报告不再携带最后处理文件名（`last_file_name`），只显示已处理数量/总数量。
- 取消语义不变：捕获 `JobCancelledError` 后返回 `cancelled=True` 结果。

### database_maintenance.py

- 同样改用 runner；维护进度口径（task_fraction）不变。
- 进度同样不显示最后处理文件名，只显示已处理数量/总数量。

### 删除清单

- `src/image_text_extractor/worker.py`、`extractor.py`、`qt.py`、`models.py`
  （文本拼接逻辑迁入 `stages.py`）。
- `src/image_features_extractor/worker.py`、`extractor.py`、`qt.py`、`models.py`
  （预处理/provider 逻辑迁入 `stages.py`，模型规格保留在 `model_specs.py`）。
- 相关测试同步改造（fake worker 注入 -> fake stage 函数注入）。
- 导入进度清理 `last_file_name`：
  - `src/services/import_images.py`：删除 `ImportImagesProgress.last_file_name`
    字段、`_report_progress` 的 `last_file_name` 参数，以及
    `_generate_vectors` / `_extract_texts` 中的 `batch_last_file_name` 跟踪。
  - `src/ui/dialog_image_import_progress.py`：删除"最后完成：xxx"显示逻辑。
  - 相关测试同步移除 `last_file_name` 断言（`test_image_import_progress_dialog.py`、
    `test_import_images_vectors.py`、`test_main_window_image_import.py`、
    `test_main_window_library_export.py` 中仅涉及导入进度的部分）。
  - `export_library.py` 的 `last_file_name` 属于导出进度，不在本次范围内。

## 明确不做 / 边界

- 不做 Qt 适配与外部 job 管理（调用方在 QThread 中同步使用，或自行包装）。
- 不保证结果顺序，不加顺序断言。
- 不实现多子进程 / worker 池 / 常驻模型进程。
- 不做"连续批次失败升级 job 级失败"的检查。
- 不保留旧 extractor 包兼容层。
- 不做自动 batch_size 调优。
- 不传输图片内容，仍只传路径。

## 测试计划

### batch_job_runner 单元测试

- GeneralDataWrapper 不变量、spec 校验（队列唯一、连接成链、参数合法）。
- ResultBatch / JobProgress 校验。

### scheduler 集成测试（fake stage 函数）

- 正常多步流水线：结果正确、进度正确、fed/drained 收敛、DONE(False)。
- 单条异常：hasException 标记、stage_name 正确、下游步骤跳过、job 不中断。
- batch_size > 1：聚合、混合失败透传、流尾不足一批。
- 取消：清队列、最后一步在途结果送出、DONE(True)、进程回收。
- 初始化失败：INIT_ERROR -> WorkerInitializationError。
- 协议错误 / 未知消息 -> JobError。
- worker 崩溃（stage 函数 `os._exit`）-> WorkerCrashedError。
- 超时 -> JobTimeoutError、进程回收。
- 大输入流：背压与防死锁（发送/接收交替）。

### feature 级回归

- `test_import_images_vectors.py`、`test_image_import_cancellation.py`、
  `test_database_maintenance.py` 等改造后回归。
- 真实模型集成用例保留（环境变量跳过，手动运行）。

## 实施顺序

1. 新建 `src/batch_job_runner`：models -> scheduler -> job -> exceptions -> `__init__`。
2. batch_job_runner 单元 + 集成测试（fake stage 函数）。
3. OCR 包改造：`stages.py` + `OcrBatchJobRunner`；接入 import/maintenance；删除旧文件。
4. 向量包改造：`stages.py` + `VectorBatchJobRunner`；接入 import/maintenance；删除旧文件。
5. 全量回归 + 真实模型手动验证（OCR/向量、取消、进度）。
6. PyInstaller 打包冒烟（可选，冻结环境 spawn 正常）。

## 验收标准

1. OCR 与向量生成（导入 + 维护）全部走 `batch_job_runner`，无旧实现残留引用。
2. 成功/失败/取消/进度口径与现状等价（进度细化到 item 粒度）。
3. 取消响应快于现状（item 粒度，不再等整批）。
4. 向量预处理并发利用多核；OCR 性能不劣于现状。
5. 正常/取消/失败/超时后均无遗留子进程。
6. `batch_job_runner` 不含任何 Qt 依赖。
7. 相关测试全部通过。
