# 图片导入批次间性能优化设计

## 文档状态

- 状态：设计已确认，实施已完成（2026-08-14）
- 日期：2026-08-14
- 范围：图片导入/数据库维护中向量生成与 OCR 的批次间空闲问题

## 问题定位

### 现状流程

`import_images_with_result()` 的执行顺序：

1. 预处理：逐张读取 metadata/hash，过滤重复项。
2. 按 `IMPORT_BATCH_SIZE = 32` 分批：`store_file()` 复制 Blob -> `add_stickers()` 写入 SQLite。
3. 若开启 OCR：`iter_texts()` 按 `OCR_BATCH_SIZE = 8` 逐批识别并回填 SQLite。
4. 若开启向量：`iter_features()` 按 32/批 推理并 `add_batch()` 写 ChromaDB + 回填 `vectordb_id`。

`database_maintenance.py` 的 OCR/向量回填走同一套提取器，结构相同。

### 根因：提取器“拉取式 + 单缓冲”协议

两个提取器（`image_features_extractor` / `image_text_extractor`）的 IPC 协议是：

```
Worker: 计算批次 N -> 发 BATCH_RESULT(N) -> 发 REQUEST_BATCH -> 阻塞等待
父进程: 收到 BATCH_RESULT(N) -> 把结果同步交给消费方（写 ChromaDB/SQLite）
       -> 消费方返回后，才回到 poll 循环收到 REQUEST_BATCH -> 发 PROCESS_BATCH(N+1)
```

关键点：**父进程在“消费方写库”期间，Worker 完全空闲**。每一批的写库耗时（
`ChromaVectorStore.add_batch()` + `set_sticker_vector_ids()`；OCR 路径为
`set_sticker_texts()`）都被串行地加在推理耗时之后，GPU/CPU 利用率约为
`T_infer / (T_infer + T_write)`。批次越小（32/8），固定写库开销占比越大。

## 方案：父进程侧预取（双缓冲）

### 思路

收到 `BATCH_RESULT(N)` 时，父进程在把结果交给消费方**之前**，立即把
`PROCESS_BATCH(N+1)` 发给 Worker。Worker 本已在管道中排队了
`REQUEST_BATCH(N)`，因此预取不会改变协议，只是让 Worker 的下一批计算与父进程
当前批的写库重叠，把写库耗时隐藏到推理耗时后面。

### 改动点（两个提取器对称）

1. `_handle_message()` 的 `REQUEST_BATCH` 分支：
   - 当前：`_inflight_paths is not None` 时判定为协议错误。
   - 改为：`_inflight_paths is not None` 或 `_input_exhausted` 时视为已被预取
     满足/输入已耗尽，直接 no-op。
2. `_handle_batch_result()`：
   - 清空 `_inflight_paths`、更新统计后、返回 batch 事件前，若
     `not self._cancel_requested`，调用新增的 `_prefetch_next_batch()` 预取下一批。
   - 预取时的管道 I/O 错误（`BrokenPipeError`/`EOFError`/`OSError`）容错忽略，
     由后续 `poll()` 检测 Worker 退出，保持 `WorkerCrashedError` 语义不变。
3. Worker 代码、IPC 消息、`iter_features()`/`iter_texts()` API、进度、顺序、
   取消语义全部不变。Qt 适配器复用同一控制器，自动受益。

### 正确性论证

- 消息顺序：`BATCH_RESULT(N)` -> 预取 `N+1` -> yield `N`；随后收到
  `REQUEST_BATCH(N)` no-op；`BATCH_RESULT(N+1)` -> 预取 `N+2`。顺序保持。
- 最后一批：预取时迭代器耗尽，发送 `END_INPUT`；`_handle_done()` 的
  `_input_exhausted` 与 `_completed == _submitted` 校验仍成立。
- 取消：`_cancel_requested` 时跳过预取；后续 `REQUEST_BATCH` 走 `_send_cancel()`，
  `DONE(cancelled)` 触发 cancelled 事件，与现状一致。
- 超时/崩溃：预取发送失败被容错后，由 `poll()` 的 `process.is_alive()` 检测，
  与现状一致。
- 内存：仅多预取一批（32 x 3 x 224 x 224 float32 约 19 MB），可接受。

### 预期收益

- `T_write <= T_infer` 时，Worker 利用率趋近 100%，批次间空闲被完全隐藏。
- `T_write > T_infer` 时，空闲窗口从 `T_write` 缩减为 `T_write - T_infer`。
- OCR 路径（RapidOCR 推理耗时通常远大于 SQLite 回填）同样受益。

## 明确不做（保持简单）

- **阶段级流水线**（Blob/SQLite 导入与 OCR/向量并行）：会破坏“SQLite 先完整
  提交再生成向量”的简单性与进度口径，且导入/维护互斥由 UI 保证，收益与复杂度
  不成比例。
- **Worker 内预取双缓冲**（预处理 N+1 与推理 N 重叠）：仅对 GPU 推理有明显
  收益，需要在 Worker 内引入线程/状态机；提取器设计文档明确“先预处理一批再
  推理”串行是既定决策。留待 GPU 环境实测后再评估。
- **批大小自动调优**：维持 32/8；预取落地后若仍低效，可手工调整
  `batch_size` 复测。

## 实施与验证

1. 修改 `src/image_features_extractor/extractor.py` 与
   `src/image_text_extractor/extractor.py`（对称改动）。
2. 测试：
   - 现有 fake worker 回归：确认“消费方写库慢时，下一批在写库期间已开始”，
     可在 fake worker 记录 `PROCESS_BATCH` 到达时间与消费方阻塞区间对比。
   - `REQUEST_BATCH` 在 inflight 时 no-op 不再抛协议错误。
   - 最后一批、取消、超时、崩溃场景回归。
3. 运行现有测试：`test_image_features_extractor.py`、
   `test_image_text_extractor.py`、`test_import_images_vectors.py`、
   `test_image_import_cancellation.py`。
4. 真实环境：真实模型 + 图片集统计每批 `T_infer`/`T_write`，对比总耗时与
   Worker 利用率。

## 实施记录

- 已修改 `src/image_features_extractor/extractor.py` 与
  `src/image_text_extractor/extractor.py`：`_handle_batch_result()` 在返回
  批次事件前调用新增的 `_prefetch_next_batch()` 预取下一批；
  `REQUEST_BATCH` 在 `_inflight_paths is not None` 或 `_input_exhausted`
  时按已满足处理（no-op），不再视为协议错误。
- Worker、IPC 协议、`iter_features()`/`iter_texts()` API 与进度/顺序/取消
  语义均未改动；Qt 适配器因复用同一控制器自动受益。
- 新增回归测试：
  - `tests/test_image_features_extractor.py`：probe worker 记录每批
    `PROCESS_BATCH` 到达时间，断言第二批在消费方处理第一批期间已下发。
  - `tests/test_image_text_extractor.py`：同上（记录路径经环境变量传入）。
  - 临时禁用预取后两个测试均按预期失败，确认测试能捕获回归。
- 测试结果：提取器、Qt 适配器、导入向量、导入取消、数据库维护、导入对话框
  相关套件共 90 项全部通过（真实模型用例按环境变量跳过）。