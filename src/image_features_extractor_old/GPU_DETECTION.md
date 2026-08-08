# GPU 检测功能文档

## 概述

`gpu_utils.py` 模块提供了独立的GPU检测功能，可以在系统设置阶段使用，无需预先初始化ONNX会话。该模块能够：

- 检测系统是否支持CUDA
- 获取GPU的VRAM大小
- 基于VRAM大小推荐合适的workers数量
- 生成适合的ONNX Runtime配置

## 主要组件

### 1. GPUInfo 数据类

存储GPU检测结果的数据类：

```python
@dataclass
class GPUInfo:
    cuda_available: bool          # 是否支持CUDA
    device_count: int             # GPU设备数量
    device_name: Optional[str]    # GPU设备名称
    vram_total_mb: Optional[float]  # 总VRAM(MB)
    vram_free_mb: Optional[float]   # 可用VRAM(MB)
    recommended_workers: int      # 推荐的workers数量
    error_message: Optional[str]  # 错误信息
```

### 2. detect_gpu_info()

主要的GPU检测函数：

```python
def detect_gpu_info() -> GPUInfo:
    """检测GPU信息并返回GPUInfo对象"""
```

检测策略：
1. 优先使用 `onnxruntime` 的GPU检测
2. 如果失败，尝试使用 `torch.cuda`
3. 如果都失败，返回CPU模式信息

### 3. get_gpu_provider_config()

生成ONNX Runtime的提供者配置：

```python
def get_gpu_provider_config(
    gpu_info: Optional[GPUInfo] = None,
    device_id: int = 0,
    memory_limit_gb: Optional[float] = None
) -> Tuple[list, Optional[list]]:
    """返回(providers, provider_options)元组"""
```

### 4. print_gpu_info()

便捷的打印函数，用于快速检查GPU配置：

```python
def print_gpu_info() -> None:
    """打印GPU信息到控制台"""
```

## 使用示例

### 示例1: 基本GPU检测

```python
from src.image_features_extractor import detect_gpu_info

# 检测GPU信息
gpu_info = detect_gpu_info()

print(f"CUDA可用: {gpu_info.cuda_available}")
print(f"推荐workers: {gpu_info.recommended_workers}")

if gpu_info.cuda_available:
    print(f"GPU: {gpu_info.device_name}")
    print(f"VRAM: {gpu_info.vram_total_mb:.0f} MB")
else:
    print(f"原因: {gpu_info.error_message}")
```

### 示例2: 在系统设置中使用

```python
from src.image_features_extractor import detect_gpu_info, ImageFeaturesExtractor

# 在应用启动时检测GPU
gpu_info = detect_gpu_info()

# 根据检测结果配置提取器
if gpu_info.cuda_available:
    extractor = ImageFeaturesExtractor(
        num_workers=gpu_info.recommended_workers,
        use_cuda=True
    )
    print(f"使用GPU模式，{gpu_info.recommended_workers} 个workers")
else:
    extractor = ImageFeaturesExtractor(
        num_workers=2,  # CPU模式使用较少workers
        use_cuda=False
    )
    print(f"使用CPU模式: {gpu_info.error_message}")
```

### 示例3: 获取ONNX Runtime配置

```python
from src.image_features_extractor import detect_gpu_info, get_gpu_provider_config
import onnxruntime as ort

# 检测GPU并获取配置
gpu_info = detect_gpu_info()
providers, provider_options = get_gpu_provider_config(gpu_info)

# 创建ONNX会话
session = ort.InferenceSession(
    "model.onnx",
    providers=providers,
    provider_options=provider_options
)

print(f"使用提供者: {session.get_providers()}")
```

### 示例4: 使用便捷打印函数

```python
from src.image_features_extractor import print_gpu_info

# 快速检查GPU配置
print_gpu_info()
```

输出示例：
```
==================================================
GPU 信息检测结果
==================================================

CUDA可用 | 设备数量: 1 | 设备名称: NVIDIA GeForce RTX 3080 | 总VRAM: 10240 MB | 可用VRAM: 8192 MB | 推荐workers: 2

推荐配置:
  - 使用GPU加速: 是
  - Workers数量: 2
  - ONNX Runtime: onnxruntime-gpu
==================================================
```

### 示例5: 直接运行模块

```bash
# 直接运行gpu_utils.py查看GPU信息
python -m src.image_features_extractor.gpu_utils
```

或者：

```bash
cd src/image_features_extractor
python gpu_utils.py
```

## Workers数量推荐策略

模块会根据VRAM大小自动推荐合适的workers数量：

| VRAM大小 | 推荐Workers | 说明 |
|---------|-----------|------|
| < 2GB   | 1         | 保守配置 |
| 2-4GB   | 1-2       | 小型GPU |
| 4-8GB   | 2-3       | 中型GPU |
| > 8GB   | 3-4       | 大型GPU |

计算策略：
- 每个worker约需800MB VRAM（针对ViT-B/16模型）
- 保留20%的VRAM给系统
- 最多4个workers（避免过度并行）

## 错误处理

如果GPU检测失败，函数会优雅地降级到CPU模式：

```python
gpu_info = detect_gpu_info()

if not gpu_info.cuda_available:
    print(f"GPU不可用: {gpu_info.error_message}")
    # 可能的错误信息：
    # - "未安装 onnxruntime"
    # - "ONNX Runtime未安装GPU支持(onnxruntime-gpu)"
    # - "PyTorch检测到CUDA不可用"
    # - "未检测到GPU设备"
```

## 依赖要求

### 基本检测（使用ONNX Runtime）
```bash
pip install onnxruntime-gpu
```

### 详细检测（使用PyTorch）
```bash
pip install torch torchvision
```

注意：
- 如果只安装了 `onnxruntime`（CPU版本），模块会检测到CUDA不可用
- 如果安装了 `onnxruntime-gpu` 但系统无GPU，模块会检测到但无法获取详细VRAM信息
- 如果安装了 `torch`，模块可以获取更详细的GPU信息（VRAM、设备名称等）

## 测试

运行测试脚本：

```bash
python test_gpu_detection.py
```

这将执行完整的GPU检测测试并显示详细信息。

## 与主模块的集成

GPU检测功能已集成到模块的公共API中：

```python
from src.image_features_extractor import (
    GPUInfo,
    detect_gpu_info,
    get_gpu_provider_config,
    print_gpu_info,
)
```

## 注意事项

1. **独立性**：GPU检测不需要预先初始化ONNX会话，可以在系统设置阶段使用
2. **非侵入性**：不影响现有的ImageFeaturesExtractor类的功能
3. **错误容忍**：即使检测失败，也会优雅地降级到CPU模式
4. **性能考虑**：检测过程很快（通常<100ms），可以在应用启动时执行

## 未来改进

可能的改进方向：
- 支持多GPU配置
- 动态调整workers数量
- 添加GPU使用率监控
- 支持更多GPU后端（如ROCm、OpenCL等）