# Image Features Extractor 重写设计文档

## 文档状态

- 状态：设计结论已确认，等待实施
- 确认日期：2026-08-09
- 新模块路径：`src/image_features_extractor`
- 旧模块路径：`src/image_features_extractor_old`

旧模块目前没有被运行时代码或测试引用。向量数据库的设计文档中存在概念性示例，但不构成兼容性约束。因此，新模块无需兼容旧模块的内部结构或公开 API。

## 背景

旧模块围绕多 Worker、任务队列、任务 ID、回调注册表、结果收集线程、健康检查和 GPU 检测构建。当前项目的实际约束已经发生变化：

1. ONNX Runtime 在CPU推理时能够使用多个 CPU 核心，GPU 推理速度也不是当前主要瓶颈，因此只需要一个 Worker。
2. 模型推理必须位于子进程中，使 Job 结束后能够由操作系统完整释放 ONNX、CPU/GPU 和图像相关资源。
3. 图片读取、解码、缩放和归一化也必须位于子进程中，避免阻塞主进程或 Qt 事件循环。
4. 预计需要处理海量图片，因此接口既要简单，也要允许批量流式消费结果。

## 设计目标

- 每个提取 Job 只创建一个子进程和一个 Worker。
- Job 结束后子进程退出，不保留常驻模型进程。
- 主进程只传递图片绝对路径，不读取或传输图片文件内容。
- 子进程完成图片读取、解码、预处理、批量 ONNX 推理和结果生成。
- 提供简单的同步调用接口。
- 提供面向海量数据的批量流式接口。
- 提供 Qt Signal/Slot 适配器，且不阻塞 Qt 主线程。
- 支持确定进度、取消、超时和 Worker 异常传播。
- 单张图片失败不终止整个 Job。
- 保持输入顺序与输出结果顺序一致。

## 非目标

- 不实现多 Worker 或 Worker 池。
- 不为未来的多 Worker 扩展预先增加调度抽象。
- 不实现任务优先级、暂停、恢复、重试队列或健康检查。
- 不实现 NVML、显存探测或推荐 Worker 数量等 GPU 管理功能。
- 不在第一版中支持图片 bytes、网络流或文件对象输入。
- 不负责将生成的向量写入 ChromaDB。
- 不保证与旧模块 API 兼容。

## 已确认的关键决策

### 单进程、单 Worker、Job 级生命周期

每次同步或 Qt 异步提取调用代表一个 Job。主进程为该 Job 启动一个子进程，子进程加载一次模型，处理全部输入，然后退出。

不提供 `num_workers` 参数，也不提供显式的长期 `start()` / `stop()` 生命周期。

这样可以保证：

- 模型只在子进程中加载。
- Job 正常完成、失败、取消或超时后，进程都会被 `join()`。
- 必要时可以 `terminate()` 或 `kill()`，由操作系统回收全部资源。
- 不依赖 Python 对 ONNX Runtime、Torch 或 GPU 内存的垃圾回收行为。

### 主进程传路径，子进程读取文件

主进程向子进程传递规范化后的绝对路径字符串，不通过 IPC 传递文件内容。

原因：

- 路径 IPC 负载很小。
- 避免在主进程读取和保存大块二进制内容。
- 避免 pickle 和管道复制图片数据。
- 图片解码和预处理全部隔离在子进程。
- 当前导入流程并不会在内存中保留完整图片内容。

调用方应优先传入图片复制到 Blob 存储后的稳定绝对路径，不应依赖可能被用户移动或删除的原始导入路径。

### 图片预处理

第一版使用与当前 ViT-B/16 权重一致的预处理语义：

1. 使用上下文管理器打开图片。
2. 应用 EXIF 方向修正。
3. 透明图片合成到纯白背景。
4. 转换为 RGB。
5. 保持宽高比，将较短边缩放到 256 像素。
6. 居中裁剪为 `224 x 224`。
7. 使用双线性插值。
8. 转为连续的 NCHW `float32` 数组。
9. 使用 ImageNet mean/std 归一化。

归一化参数：

```python
mean = (0.485, 0.456, 0.406)
std = (0.229, 0.224, 0.225)
```

优先使用 Pillow 和 NumPy 直接实现预处理，不在运行时依赖 `torchvision` transforms。这样能够减少子进程启动成本、内存占用和依赖复杂度。实现后应使用 `torchvision.models.ViT_B_16_Weights.DEFAULT.transforms()` 作为测试参考，验证数值误差在允许范围内。

### ONNX 批量推理

当前模型已确认具有以下接口：

- 输入：`float32[batch_size, 3, 224, 224]`
- 输出：`float32[batch_size, 768]`
- batch 维度为动态维度。

默认 `batch_size` 为 `32`，并允许调用方配置。第一版不实现自动 batch size 探测。GPU 环境中的合理 batch size 由后续人工性能测试确定。

子进程应先预处理一个路径批次，再将其中成功预处理的图片合并为一个 ONNX 输入 batch。预处理失败的图片不进入推理，但仍在结果中占据与输入对应的位置。

### 输出格式

成功向量保持模型原始输出：

- 维度：768
- 类型：`numpy.float32`
- 不额外执行 L2 normalize

现有向量数据库使用 cosine 距离，因此不要求提取器预先进行 L2 归一化。

### 图片级失败语义

每个输入图片对应一个结果对象，并使用显式的 `success: bool` 字段。

建议的数据结构：

```python
@dataclass(frozen=True, slots=True)
class ImageFeatureResult:
    image_path: str
    success: bool
    vector: np.ndarray | None
    error: str | None
```

必须满足以下不变量：

- `success is True` 时，`vector` 必须是 768 维 `float32` 数组，`error` 必须为 `None`。
- `success is False` 时，`vector` 必须为 `None`，`error` 必须是可供日志和 UI 展示的非空字符串。
- 结果顺序必须与输入路径顺序完全一致。

损坏图片、不支持的格式、文件不存在或单文件预处理失败属于图片级失败。Worker 继续处理同一 Job 中的后续图片。

模型加载失败、ONNX Session 创建失败、批量推理失败、IPC 失败或 Worker 崩溃属于 Job 级失败，应终止 Job 并向调用方抛出或发出 Job 错误。

### ONNX Execution Provider

默认 provider 策略：

1. 如果 `CUDAExecutionProvider` 可用，则优先使用 CUDA。
2. 否则使用 `CPUExecutionProvider`。
3. 启动完成消息中返回实际启用的 provider，供日志和 UI 状态显示。

第一版不实现 NVML 检测、显存估算或自动 GPU 内存限制。允许高级调用方显式传递 provider 配置。

当前开发设备没有兼容 CUDA 的 GPU，GPU 支持需要在其他环境中手动验证。CPU 路径必须具备自动化测试。

## 建议的模块结构

```text
src/image_features_extractor/
├── __init__.py       # 公开 API
├── extractor.py      # 同步接口、流式接口和父进程控制器
├── worker.py         # 可被 spawn 的顶层 Worker 入口和推理实现
├── qt.py             # Qt Signal/Slot 适配器
├── models.py         # 结果、进度和批次数据结构
└── exceptions.py     # 精简的 Job 级异常
```

文件数量可以在实施时进一步减少，但必须保持 Worker 入口为模块顶层可 pickle 函数。

## 公开 API 草案

### 简单同步接口

```python
results = extract_features(
    image_paths,
    model_path=model_path,
    batch_size=32,
    progress=on_progress,
    timeout=None,
)

for result in results:
    if result.success:
        consume(result.image_path, result.vector)
    else:
        handle_failure(result.image_path, result.error)
```

`extract_features()` 启动子进程、收集全部结果、等待子进程退出，然后返回 `list[ImageFeatureResult]`。

该接口适合调用方确实需要一次性获得完整结果的情况。对于海量图片，不应使用该接口累计全部向量。

### 批量流式接口

```python
for batch in iter_features(
    image_paths,
    model_path=model_path,
    batch_size=32,
    progress=on_progress,
):
    store_batch(batch.results)
```

`iter_features()` 每次只返回一个结果批次。调用方可以立即把成功向量写入向量数据库并释放该批次，防止主进程内存随图片总量持续增长。

如果输入对象实现了 `len()`，模块自动获得总数。对于无长度的 iterable，调用方可以显式提供 `total`；未提供时进度为不确定总量模式。

### 进度回调

建议的进度对象：

```python
@dataclass(frozen=True, slots=True)
class ExtractionProgress:
    completed: int
    total: int | None
    succeeded: int
    failed: int
```

进度以“已产生图片结果”为单位，而不是以 ONNX batch 为单位。成功和失败的图片都计入 `completed`。

进度必须单调递增。在总数已知且 Job 正常结束时，最后一次进度必须满足 `completed == total`。

## Qt Signal/Slot 接口

Qt 支持由独立的适配器提供，核心同步模块不依赖 Qt 事件循环。

建议接口：

```python
class QtImageFeaturesExtractor(QObject):
    started = pyqtSignal(object)          # 实际 provider 等启动信息
    progress_changed = pyqtSignal(object) # ExtractionProgress
    batch_ready = pyqtSignal(object)      # FeatureResultBatch
    finished = pyqtSignal(object)         # Job 摘要
    failed = pyqtSignal(str)              # Job 级失败
    cancelled = pyqtSignal()

    @pyqtSlot(object)
    def start(self, request): ...

    @pyqtSlot()
    def cancel(self): ...
```

约束：

- `failed` 只表示 Job 级失败，单图片失败通过 `batch_ready` 中的 `success=False` 返回。
- `start()` 必须立即返回，不得等待模型加载或图片处理。
- Qt 主线程使用 `QTimer` 轮询 IPC 连接并发出信号，不额外创建长期结果收集线程。
- 不在 Worker 子进程中导入或创建 Qt 对象。
- 同一实例同一时间只允许运行一个 Job。
- 取消后必须最终发出 `cancelled`，不能同时发出 `finished`。

## IPC 协议

由于只有一个父进程和一个子进程，使用单条双向 `multiprocessing.Pipe`，不使用任务队列和结果队列。

建议消息类型：

```text
子进程 -> 主进程: INIT_OK(provider_info)
子进程 -> 主进程: INIT_ERROR(error)
子进程 -> 主进程: REQUEST_BATCH
主进程 -> 子进程: PROCESS_BATCH(paths)
主进程 -> 子进程: END_INPUT
主进程 -> 子进程: CANCEL
子进程 -> 主进程: BATCH_RESULT(results, progress)
子进程 -> 主进程: JOB_ERROR(error)
子进程 -> 主进程: DONE(summary)
```

采用请求下一批的握手机制，使父进程只在 Worker 准备好时发送路径，避免在 IPC 中堆积海量路径或结果。

NumPy 向量按 batch 通过 Pipe 传回。默认 batch 32 时数据量较小，不引入共享内存。只有在基准测试证明 IPC 复制成为瓶颈后，才考虑共享内存。

## 取消、超时与资源清理

- 正常完成：接收 `DONE`，关闭 Pipe，`join()` 子进程。
- 图片级失败：继续执行，不影响子进程生命周期。
- Job 级失败：记录错误，关闭输入，等待子进程退出。
- 用户取消：发送 `CANCEL`；如果子进程正在执行 ONNX batch，可等待当前 batch 返回或在短暂宽限期后终止。
- 超时：终止子进程并抛出明确的超时异常，不允许任务继续在后台运行。
- Worker 无响应：先 `terminate()`，仍未退出时再 `kill()`，最后必须 `join()`。
- 所有父进程控制路径使用 `try/finally` 关闭 Pipe 和回收子进程。

子进程不设置为 daemon。资源释放依赖显式生命周期管理，而不是解释器退出时的隐式终止。

## 精简异常层次

只保留 Job 级异常：

```text
ImageFeaturesExtractorError
├── WorkerInitializationError
├── WorkerCrashedError
├── ExtractionTimeoutError
└── ExtractionCancelledError
```

单图片错误不抛出这些异常，而是写入 `ImageFeatureResult.error`。

## 主入口修改

Windows 和 PyInstaller 使用 spawn 启动子进程。当前 `src/main.py` 在主入口保护之外执行应用初始化，子进程导入主模块时可能重复初始化数据库、Blob 存储和 UI 依赖。

实施时需要：

1. 增加 `multiprocessing.freeze_support()`。
2. 将应用路径设置、服务初始化、Qt 导入、`QApplication` 创建和主窗口创建放入 `main()`。
3. 只在 `if __name__ == "__main__":` 中调用 `freeze_support()` 和 `main()`。
4. 确保 Worker spawn 时导入主模块不会执行数据库或 UI 初始化。

建议结构：

```python
def main() -> int:
    # 配置路径、初始化服务、创建 QApplication 和主窗口
    ...
    return app.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
```

## 从旧模块保留的思路

- ONNX SessionOptions 和图优化。
- 使用官方 ViT-B/16 预处理语义。
- 区分 Worker 初始化失败、Worker 崩溃和图片处理失败。
- 单图片异常隔离。
- 超时与强制终止能力。
- Qt 信号通知的外部使用方式。

## 不保留的旧设计

- 多 Worker 和共享任务队列。
- `TaskStatus`、任务 ID 和 pending task 字典。
- 回调注册表和结果收集线程。
- `HEALTH_CHECK` 等没有实际响应协议的控制命令。
- `GPUInfo`、NVML 检测和推荐 Worker 数量。
- 无实际 emit 的进度信号。
- 大量模块级配置常量和文档辅助函数。
- `__del__` 中执行阻塞式进程清理。

## 已发现的旧实现问题

- `use_cuda` 参数没有传递给 Worker。
- Worker 尚未加载模型时，主进程就发出 ready 信号。
- Worker 初始化失败被捕获后正常返回，主进程无法获得可靠的初始化错误。
- `progress_updated` 只有声明，没有进入实际执行链。
- `stop()` 持有普通 `threading.Lock` 时调用 `_cleanup()`，后者再次获取同一把锁，存在死锁。
- 异步任务完成后 pending task 记录没有正常清理。
- 文档声明 metadata 会返回，但结果对象没有 metadata 字段。
- 模型支持动态 batch，但旧实现始终逐张推理。
- `torchvision` transforms 引入了不必要的 Torch 运行时成本。

## 测试计划

### 单元测试

- 路径规范化和输入顺序保持。
- 透明 RGBA、调色板透明和普通 RGB 图片预处理。
- EXIF 方向修正。
- 预处理结果与 torchvision 官方 transform 的数值对比。
- 单张损坏图片返回 `success=False`，后续图片继续处理。
- `ImageFeatureResult` 成功/失败不变量。
- 进度单调递增，成功和失败均计数。
- 无总数 iterable 的不确定进度模式。
- provider 选择逻辑。
- IPC 消息验证和未知消息处理。

### 子进程集成测试

- Worker 初始化成功后才发送 `INIT_OK`。
- 模型不存在时返回初始化错误且无遗留子进程。
- 多张图片输出顺序与输入顺序一致。
- 输出 shape 为 `(N, 768)`，dtype 为 `float32`。
- 图片级失败不会终止 Job。
- Job 超时会终止并回收子进程。
- 取消会终止并回收子进程。
- Worker 异常退出会转换为 `WorkerCrashedError`。

实际 343 MB 模型可以用于本地或可选集成测试，不应让所有快速单元测试都依赖该模型。GPU 测试在具备 CUDA 环境的设备上手动执行。

### Qt 测试

- `start()` 不阻塞 Qt 事件循环。
- `progress_changed` 可直接驱动进度条。
- `batch_ready` 包含图片级成功和失败结果。
- 正常结束只发出 `finished`。
- 取消只发出 `cancelled`。
- Job 级异常发出 `failed`。
- 一个实例不能同时启动两个 Job。

### 主入口测试

- Windows spawn 子进程不会重复运行 `run_startup_tasks()`。
- 普通 Python 启动仍能打开 Qt 主窗口。
- PyInstaller 构建能够启动 Worker。

## 验收标准

1. 新包名为 `image_features_extractor`，旧包保持未引用状态，直到后续单独决定是否删除。
2. 每个 Job 最多只有一个 Worker 子进程。
3. 模型和图片预处理都只在子进程执行。
4. Job 结束后不存在遗留 Worker 进程。
5. 主进程通过路径而不是文件 bytes 提交图片。
6. 同步接口可以用一次函数调用获得有序结果。
7. 流式接口可以逐批消费结果，不随总文件数无限累计向量。
8. 每张图片都有明确的 `success`、`vector` 和 `error` 状态。
9. 单张图片失败不会导致整个 Job 失败。
10. Job 级错误能够被同步接口抛出，并能通过 Qt `failed` Signal 报告。
11. 进度可用于确定型 Qt 进度条，并正确统计失败图片。
12. CPU 环境自动化测试通过；GPU provider 可在其他环境手动验证。
13. `src/main.py` 满足 Windows spawn 和 PyInstaller 的入口保护要求。

## 实施顺序建议

1. 定义结果、进度、异常和 IPC 消息契约。
2. 实现并测试 Pillow/NumPy 预处理。
3. 实现单 Worker 子进程和 ONNX batch 推理。
4. 实现父进程同步流式控制器。
5. 实现一次性收集的同步便捷函数。
6. 实现 Qt Signal/Slot 适配器。
7. 修改 `src/main.py` 的 spawn 入口。
8. 增加 CPU 集成测试和可选真实模型测试。
9. 在 CUDA 环境执行 provider、batch size 和资源释放测试。

## 尚未纳入第一版的事项

- 模型文件最终由 PyInstaller 打包还是作为外部资源分发。
- 模型文件哈希的计算和缓存策略。
- 无文件来源的 bytes/stream 输入。
- 共享内存向量传输。
- GPU batch size 自动调优。
- 自动重试失败图片。

这些事项不阻塞第一版模块开发。
