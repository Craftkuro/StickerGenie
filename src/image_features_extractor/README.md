# Image Features Extractor 使用文档

## 目录

- [模块概述](#模块概述)
- [安装说明](#安装说明)
- [快速开始](#快速开始)
- [详细使用指南](#详细使用指南)
  - [初始化配置](#初始化配置)
  - [同步提取特征](#同步提取特征)
  - [异步提取特征](#异步提取特征)
  - [使用 PyQt 信号](#使用-pyqt-信号)
  - [上下文管理器](#上下文管理器)
- [API 参考](#api-参考)
- [配置选项](#配置选项)
- [错误处理](#错误处理)
- [性能优化建议](#性能优化建议)
- [故障排查](#故障排查)
- [完整示例](#完整示例)

---

## 模块概述

`image_features_extractor` 是一个高性能的图像特征提取模块，专为 StickerGenie 项目设计。它采用多进程架构，利用 ONNX 模型进行高效的并发特征提取。

### 核心特性

- **多进程并行处理**: 绕过 Python GIL，充分利用多核 CPU
- **同步/异步两种 API**: 灵活适应不同使用场景
- **PyQt6 信号系统**: 无缝集成 GUI 应用，保持 UI 响应
- **完善的错误处理**: 健壮的异常处理机制
- **资源管理**: 支持上下文管理器，自动清理资源

### 架构特点

```
主线程 (UI)
    ↓ 任务提交
[ImageFeaturesExtractor]
    ↓ 任务队列
[Worker 进程 1] [Worker 进程 2] ... [Worker 进程 N]
    ↓ ONNX 模型推理
    ↓ 结果队列
[结果收集线程]
    ↓ PyQt 信号
主线程 (UI 更新)
```

---

## 安装说明

### 1. 安装依赖

从项目根目录运行：

```bash
pip install -r requirements.txt
```

### 2. 准备 ONNX 模型

确保你有可用的 ONNX 模型文件（例如 `vit_b_16_features.onnx`），并将其放置在合适的位置。

### 3. 验证安装

运行测试脚本验证安装：

```bash
python test_extractor.py
```

如果看到所有测试通过的提示，说明安装成功。

---

## 快速开始

### 基本使用示例

```python
from image_features_extractor import ImageFeaturesExtractor

# 1. 创建提取器实例
extractor = ImageFeaturesExtractor(
    model_path="vit_b_16_features.onnx",
    num_workers=2,
    use_cuda=False
)

# 2. 启动 Worker 进程
extractor.start()

try:
    # 3. 同步提取特征
    features = extractor.extract_features_sync("path/to/image.jpg")
    print(f"特征维度: {features.shape}")
    
    # 4. 异步提取特征
    def on_complete(result):
        if result.is_success:
            print(f"异步提取完成: {result.features.shape}")
    
    task_id = extractor.extract_features_async(
        "path/to/another_image.jpg",
        callback=on_complete
    )
    print(f"任务已提交: {task_id}")
    
finally:
    # 5. 停止提取器
    extractor.stop()
```

---

## 详细使用指南

### 初始化配置

[`ImageFeaturesExtractor`](extractor.py:56) 类提供了灵活的初始化选项：

```python
extractor = ImageFeaturesExtractor(
    model_path="vit_b_16_features.onnx",  # ONNX 模型路径
    num_workers=2,                         # Worker 进程数量
    max_queue_size=100,                    # 任务队列最大长度
    use_cuda=False                         # 是否使用 CUDA 加速
)
```

#### 参数说明

- **`model_path`**: ONNX 模型文件的路径（绝对路径或相对路径）
- **`num_workers`**: Worker 进程数量，建议设为 `CPU 核心数 - 1`
- **`max_queue_size`**: 任务队列最大容量，防止内存溢出
- **`use_cuda`**: 是否使用 GPU 加速（需要安装 `onnxruntime-gpu`）

#### 推荐配置

```python
import os

# 根据 CPU 核心数自动设置 Worker 数量
cpu_count = os.cpu_count() or 1
num_workers = max(1, cpu_count - 1)

extractor = ImageFeaturesExtractor(
    model_path="models/vit_b_16_features.onnx",
    num_workers=num_workers,
    max_queue_size=200
)
```

### 同步提取特征

[`extract_features_sync()`](extractor.py:399) 方法提供阻塞式的特征提取：

```python
# 基本使用
features = extractor.extract_features_sync("image.jpg")

# 设置超时时间
try:
    features = extractor.extract_features_sync(
        "large_image.jpg",
        timeout=60.0  # 60秒超时
    )
except TimeoutError:
    print("提取超时")
except Exception as e:
    print(f"提取失败: {e}")
```

#### 适用场景

- 简单的命令行工具
- 批量处理脚本
- 不需要 UI 响应的场景

#### 注意事项

- 会阻塞调用线程，不适合在 UI 线程中使用
- 超时后任务仍会在后台继续处理
- 返回的是 NumPy 数组对象

### 异步提取特征

[`extract_features_async()`](extractor.py:328) 方法提供非阻塞式的特征提取：

```python
# 使用回调函数
def on_complete(result):
    if result.is_success:
        print(f"成功: {result.image_path}")
        print(f"特征: {result.features.shape}")
        print(f"耗时: {result.processing_time:.2f}s")
    else:
        print(f"失败: {result.error_message}")

task_id = extractor.extract_features_async(
    image_path="image.jpg",
    callback=on_complete,
    metadata={"user_id": 123}  # 可选的元数据
)

print(f"任务ID: {task_id}")
```

#### 适用场景

- GUI 应用（保持界面响应）
- 需要并发处理多个图像
- 需要进度反馈的场景

#### 回调函数

回调函数接收一个 [`ExtractionResult`](tasks.py:288) 对象：

```python
def my_callback(result):
    """
    参数:
        result.task_id: 任务ID
        result.image_path: 图像路径
        result.is_success: 是否成功
        result.features: 特征向量 (成功时)
        result.error_message: 错误信息 (失败时)
        result.worker_id: 处理该任务的 Worker ID
        result.processing_time: 处理耗时(秒)
        result.metadata: 提交任务时的元数据
    """
    pass
```

### 使用 PyQt 信号

模块提供了丰富的 [`ExtractorSignals`](signals.py:232) 用于异步通知：

```python
from PyQt6.QtWidgets import QMainWindow

class MyWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # 创建提取器
        self.extractor = ImageFeaturesExtractor(num_workers=2)
        
        # 连接信号
        self.extractor.signals.features_extracted.connect(self.on_features_ready)
        self.extractor.signals.extraction_failed.connect(self.on_extraction_failed)
        self.extractor.signals.task_submitted.connect(self.on_task_submitted)
        self.extractor.signals.all_workers_ready.connect(self.on_workers_ready)
        
        # 启动提取器
        self.extractor.start()
    
    def on_features_ready(self, task_id, image_path, features):
        """特征提取完成"""
        print(f"提取完成: {image_path}")
        print(f"特征维度: {features.shape}")
    
    def on_extraction_failed(self, task_id, image_path, error_message):
        """特征提取失败"""
        print(f"提取失败: {image_path} - {error_message}")
    
    def on_task_submitted(self, task_id, image_path):
        """任务已提交"""
        print(f"任务已提交: {task_id}")
    
    def on_workers_ready(self):
        """所有 Worker 已就绪"""
        print("提取器就绪，可以开始提交任务")
    
    def closeEvent(self, event):
        """窗口关闭时停止提取器"""
        self.extractor.stop()
        event.accept()
```

#### 可用信号

| 信号 | 参数 | 说明 |
|------|------|------|
| `features_extracted` | `(task_id, image_path, features)` | 特征提取成功 |
| `extraction_failed` | `(task_id, image_path, error_message)` | 特征提取失败 |
| `task_submitted` | `(task_id, image_path)` | 任务已提交到队列 |
| `worker_started` | `(worker_id,)` | Worker 进程已启动 |
| `worker_stopped` | `(worker_id,)` | Worker 进程已停止 |
| `all_workers_ready` | `()` | 所有 Worker 已就绪 |
| `progress_updated` | `(completed, total)` | 进度更新（批量处理时） |

### 上下文管理器

使用 `with` 语句自动管理生命周期：

```python
# 自动启动和停止
with ImageFeaturesExtractor(num_workers=2) as extractor:
    features = extractor.extract_features_sync("image.jpg")
    # 退出 with 块时自动调用 stop()

# 也可以用于异步操作
with ImageFeaturesExtractor() as extractor:
    task_id = extractor.extract_features_async("image.jpg")
    # 等待完成...
    import time
    time.sleep(2)
```

---

## API 参考

### [`ImageFeaturesExtractor`](extractor.py:56) 类

主要的管理类，协调多个 Worker 进程。

#### 方法

##### `__init__(model_path, num_workers, max_queue_size, use_cuda, **kwargs)`

初始化提取器。

##### `start() -> None`

启动所有 Worker 进程。抛出 [`WorkerInitError`](exceptions.py:48) 如果启动失败。

##### `stop(timeout=5.0) -> None`

优雅地停止所有 Worker 进程。

##### `extract_features_sync(image_path, timeout=30.0) -> np.ndarray`

同步提取特征（阻塞）。

##### `extract_features_async(image_path, callback=None, metadata=None) -> str`

异步提取特征（非阻塞），返回任务ID。

#### 属性

- `signals`: [`ExtractorSignals`](signals.py:232) 对象
- `is_running()`: 检查提取器是否运行
- `queue_size`: 当前队列中的任务数
- `pending_tasks_count`: 待处理任务数量

### [`ExtractionResult`](tasks.py:288) 数据类

表示提取结果。

#### 属性

- `task_id`: 任务唯一标识
- `image_path`: 图像文件路径
- `is_success`: 是否成功
- `features`: NumPy 特征数组（成功时）
- `error_message`: 错误信息（失败时）
- `worker_id`: Worker 标识
- `processing_time`: 处理耗时（秒）
- `metadata`: 元数据字典

### 异常类

所有异常继承自 [`ExtractorError`](exceptions.py:21)：

- [`ModelNotFoundError`](exceptions.py:30): 模型文件未找到
- [`WorkerInitError`](exceptions.py:48): Worker 初始化失败
- [`TaskSubmissionError`](exceptions.py:71): 任务提交失败
- [`FeatureExtractionError`](exceptions.py:94): 特征提取失败
- [`WorkerCrashedError`](exceptions.py:121): Worker 进程崩溃
- [`InvalidImageError`](exceptions.py:142): 无效的图像文件

---

## 配置选项

所有配置常量定义在 [`config.py`](config.py:1) 中。

### Worker 配置

```python
from image_features_extractor.config import (
    DEFAULT_NUM_WORKERS,           # 1
    DEFAULT_MAX_QUEUE_SIZE,        # 100
    DEFAULT_CONTROL_QUEUE_SIZE,    # 10
)
```

### 超时配置

```python
from image_features_extractor.config import (
    DEFAULT_SYNC_TIMEOUT,          # 30.0 秒
    DEFAULT_SHUTDOWN_TIMEOUT,      # 5.0 秒
    DEFAULT_TASK_SUBMIT_TIMEOUT,   # 5.0 秒
)
```

### ONNX Runtime 配置

```python
from image_features_extractor.config import (
    ONNX_PROVIDERS,                # ['CPUExecutionProvider']
    ONNX_SESSION_OPTIONS,          # 会话选项字典
)
```

### 图像预处理配置

```python
from image_features_extractor.config import (
    IMAGE_SIZE,                    # (224, 224)
    NORMALIZE_MEAN,                # [0.485, 0.456, 0.406]
    NORMALIZE_STD,                 # [0.229, 0.224, 0.225]
)
```

---

## 错误处理

### 常见错误和解决方案

#### 1. 模型文件未找到

```python
try:
    extractor = ImageFeaturesExtractor(model_path="model.onnx")
    extractor.start()
except ModelNotFoundError as e:
    print(f"模型文件不存在: {e.model_path}")
    # 解决: 检查路径是否正确
```

#### 2. Worker 启动失败

```python
try:
    extractor.start()
except WorkerInitError as e:
    print(f"Worker {e.worker_id} 启动失败: {e.reason}")
    # 解决: 检查模型文件、依赖库是否完整
```

#### 3. 任务队列已满

```python
try:
    task_id = extractor.extract_features_async("image.jpg")
except TaskSubmissionError as e:
    print(f"任务提交失败: {e.reason}")
    # 解决: 等待队列空闲或增加 max_queue_size
```

#### 4. 提取超时

```python
try:
    features = extractor.extract_features_sync("image.jpg", timeout=10.0)
except TimeoutError:
    print("提取超时")
    # 解决: 增加超时时间或优化模型
```

### 错误处理最佳实践

```python
from image_features_extractor import (
    ImageFeaturesExtractor,
    ExtractorError,
    ModelNotFoundError,
    WorkerInitError,
)

try:
    with ImageFeaturesExtractor() as extractor:
        # 同步提取
        try:
            features = extractor.extract_features_sync("image.jpg")
        except TimeoutError:
            print("提取超时")
        except Exception as e:
            print(f"提取失败: {e}")
        
        # 异步提取
        def callback(result):
            if not result.is_success:
                print(f"异步提取失败: {result.error_message}")
        
        extractor.extract_features_async("image2.jpg", callback=callback)

except ModelNotFoundError as e:
    print(f"模型未找到: {e.model_path}")
except WorkerInitError as e:
    print(f"Worker 启动失败: {e.reason}")
except ExtractorError as e:
    print(f"提取器错误: {e}")
```

---

## 性能优化建议

### 1. Worker 数量选择

```python
import os

# 方案 A: CPU 密集型（推荐）
cpu_count = os.cpu_count() or 1
num_workers = max(1, cpu_count - 1)

# 方案 B: I/O 密集型
num_workers = cpu_count * 2

# 方案 C: 混合场景（需要测试）
num_workers = cpu_count
```

### 2. 队列大小调整

```python
# 小内存环境
extractor = ImageFeaturesExtractor(max_queue_size=50)

# 大内存环境，需要高吞吐量
extractor = ImageFeaturesExtractor(max_queue_size=500)
```

### 3. GPU 加速

如果有 NVIDIA GPU：

```bash
# 卸载 CPU 版本
pip uninstall onnxruntime

# 安装 GPU 版本
pip install onnxruntime-gpu>=1.16.0
```

然后：

```python
extractor = ImageFeaturesExtractor(
    model_path="model.onnx",
    num_workers=1,  # GPU 通常1个Worker就够了
    use_cuda=True
)
```

### 4. 批量处理优化

```python
from pathlib import Path

# 预先收集所有图像路径
image_paths = list(Path("images/").glob("*.jpg"))

# 批量提交任务
for path in image_paths:
    extractor.extract_features_async(str(path))

# 等待完成（使用信号或轮询）
```

### 5. 内存管理

```python
import gc

# 定期释放内存
for i, path in enumerate(image_paths):
    features = extractor.extract_features_sync(path)
    # 处理 features...
    
    if i % 100 == 0:
        gc.collect()  # 强制垃圾回收
```

---

## 故障排查

### 问题 1: 提取器启动后无响应

**症状**: 调用 `start()` 后，Worker 没有处理任务。

**可能原因**:
- 模型文件损坏
- ONNX Runtime 版本不兼容
- 系统资源不足

**诊断步骤**:

```python
import logging

# 启用详细日志
logging.basicConfig(level=logging.DEBUG)

extractor = ImageFeaturesExtractor(num_workers=1)
extractor.start()
```

### 问题 2: 内存持续增长

**症状**: 长时间运行后内存占用不断增加。

**可能原因**:
- 结果没有及时处理
- 队列积压

**解决方案**:

```python
# 监控队列大小
print(f"队列大小: {extractor.queue_size}")
print(f"待处理任务: {extractor.pending_tasks_count}")

# 限制并发任务数
max_concurrent = 10
while extractor.pending_tasks_count >= max_concurrent:
    time.sleep(0.1)
```

### 问题 3: 特征提取结果不一致

**症状**: 同一张图像多次提取的特征不同。

**可能原因**:
- 图像预处理问题
- 模型问题

**诊断**:

```python
# 多次提取同一图像
features_list = []
for _ in range(5):
    features = extractor.extract_features_sync("test.jpg")
    features_list.append(features)

# 检查一致性
import numpy as np
for i in range(1, len(features_list)):
    diff = np.abs(features_list[0] - features_list[i]).max()
    print(f"差异 {i}: {diff}")
```

### 问题 4: Worker 进程崩溃

**症状**: 日志显示 Worker 异常终止。

**诊断**:

```python
# 连接 Worker 停止信号
def on_worker_stopped(worker_id):
    print(f"Worker {worker_id} 已停止")

extractor.signals.worker_stopped.connect(on_worker_stopped)

# 检查进程状态
for i, process in enumerate(extractor._workers):
    print(f"Worker {i}: alive={process.is_alive()}")
```

---

## 完整示例

### 示例 1: 命令行批量处理工具

```python
#!/usr/bin/env python3
"""批量提取图像特征的命令行工具"""

import argparse
from pathlib import Path
import numpy as np
from image_features_extractor import ImageFeaturesExtractor

def main():
    parser = argparse.ArgumentParser(description='批量提取图像特征')
    parser.add_argument('input_dir', help='输入图像目录')
    parser.add_argument('output_file', help='输出特征文件(.npz)')
    parser.add_argument('--workers', type=int, default=2, help='Worker数量')
    parser.add_argument('--model', default='vit_b_16_features.onnx', help='模型路径')
    args = parser.parse_args()
    
    # 扫描图像
    image_dir = Path(args.input_dir)
    image_files = list(image_dir.glob('*.jpg')) + list(image_dir.glob('*.png'))
    print(f"找到 {len(image_files)} 张图像")
    
    # 创建提取器
    with ImageFeaturesExtractor(
        model_path=args.model,
        num_workers=args.workers
    ) as extractor:
        
        # 批量提取
        features_dict = {}
        for i, img_path in enumerate(image_files, 1):
            try:
                features = extractor.extract_features_sync(str(img_path))
                features_dict[img_path.name] = features
                print(f"[{i}/{len(image_files)}] {img_path.name}: {features.shape}")
            except Exception as e:
                print(f"[{i}/{len(image_files)}] {img_path.name}: 失败 - {e}")
        
        # 保存结果
        np.savez_compressed(args.output_file, **features_dict)
        print(f"\n特征已保存到: {args.output_file}")

if __name__ == '__main__':
    main()
```

### 示例 2: PyQt GUI 应用集成

```python
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, 
    QFileDialog, QProgressBar, QLabel, QVBoxLayout, QWidget
)
from PyQt6.QtCore import pyqtSlot
from image_features_extractor import ImageFeaturesExtractor

class FeatureExtractorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("图像特征提取器")
        self.setGeometry(100, 100, 500, 300)
        
        # 创建提取器
        self.extractor = ImageFeaturesExtractor(num_workers=2)
        
        # 连接信号
        self.extractor.signals.features_extracted.connect(self.on_features_ready)
        self.extractor.signals.extraction_failed.connect(self.on_failed)
        self.extractor.signals.all_workers_ready.connect(self.on_ready)
        
        # UI 组件
        layout = QVBoxLayout()
        
        self.status_label = QLabel("状态: 未就绪")
        layout.addWidget(self.status_label)
        
        self.select_btn = QPushButton("选择图像")
        self.select_btn.clicked.connect(self.select_image)
        self.select_btn.setEnabled(False)
        layout.addWidget(self.select_btn)
        
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)
        
        self.result_label = QLabel("")
        layout.addWidget(self.result_label)
        
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        
        # 启动提取器
        self.extractor.start()
        self.total_tasks = 0
        self.completed_tasks = 0
    
    @pyqtSlot()
    def on_ready(self):
        self.status_label.setText("状态: 就绪")
        self.select_btn.setEnabled(True)
    
    def select_image(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图像", "", "Images (*.jpg *.png)"
        )
        
        if files:
            self.total_tasks = len(files)
            self.completed_tasks = 0
            self.progress_bar.setMaximum(self.total_tasks)
            self.progress_bar.setValue(0)
            
            for file_path in files:
                self.extractor.extract_features_async(file_path)
    
    @pyqtSlot(str, str, object)
    def on_features_ready(self, task_id, image_path, features):
        self.completed_tasks += 1
        self.progress_bar.setValue(self.completed_tasks)
        self.result_label.setText(
            f"完成: {self.completed_tasks}/{self.total_tasks} - "
            f"最新: {Path(image_path).name} ({features.shape})"
        )
    
    @pyqtSlot(str, str, str)
    def on_failed(self, task_id, image_path, error_message):
        self.completed_tasks += 1
        self.progress_bar.setValue(self.completed_tasks)
        self.result_label.setText(f"失败: {Path(image_path).name} - {error_message}")
    
    def closeEvent(self, event):
        self.extractor.stop()
        event.accept()

if __name__ == '__main__':
    app = QApplication([])
    window = FeatureExtractorWindow()
    window.show()
    app.exec()
```

---

## 相关文档

- [DESIGN.md](DESIGN.md) - 详细的设计文档
- [USAGE_EXAMPLES.md](USAGE_EXAMPLES.md) - 更多使用示例
- [项目 README](../../README.md) - 项目总体说明

---

## 支持和反馈

如有问题或建议，请联系 StickerGenie 开发团队。

**版本**: 1.0.0  
**最后更新**: 2025-01-14