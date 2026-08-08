"""
Image Features Extractor 配置参数

本模块定义了特征提取器的默认配置参数,包括:
- Worker 进程配置
- ONNX Runtime 配置
- 图像预处理配置
- 超时和队列配置

参考: DESIGN.md 第 7 节
"""

from typing import List, Dict, Any

# ============================================================================
# Worker 进程配置
# ============================================================================

DEFAULT_NUM_WORKERS: int = 1
"""默认 Worker 进程数量。建议设置为 CPU 核心数 - 1"""

DEFAULT_MAX_QUEUE_SIZE: int = 100
"""默认任务队列最大长度,防止内存溢出"""

DEFAULT_CONTROL_QUEUE_SIZE: int = 10
"""控制队列大小,用于发送停止等控制命令"""


# ============================================================================
# 模型配置
# ============================================================================

DEFAULT_MODEL_PATH: str = "vit_b_16_features.onnx"
"""默认 ONNX 模型文件路径(相对于工作目录)"""


# ============================================================================
# 超时配置
# ============================================================================

DEFAULT_SYNC_TIMEOUT: float = 30.0
"""同步提取的默认超时时间(秒)"""

DEFAULT_SHUTDOWN_TIMEOUT: float = 5.0
"""关闭 Worker 进程的超时时间(秒)"""

DEFAULT_TASK_SUBMIT_TIMEOUT: float = 5.0
"""任务提交到队列的超时时间(秒)"""

DEFAULT_RESULT_GET_TIMEOUT: float = 0.1
"""从结果队列获取结果的超时时间(秒),用于非阻塞检查"""


# ============================================================================
# ONNX Runtime 配置
# ============================================================================

ONNX_PROVIDERS: List[str] = ['CPUExecutionProvider']
"""
ONNX Runtime 执行提供者列表

可选值:
- 'CUDAExecutionProvider': GPU 加速(需要 onnxruntime-gpu)
- 'CPUExecutionProvider': CPU 执行
- 'TensorrtExecutionProvider': TensorRT 加速

注意: 列表顺序决定优先级,第一个可用的提供者将被使用
"""

ONNX_SESSION_OPTIONS: Dict[str, Any] = {
    'intra_op_num_threads': 0,
    'inter_op_num_threads': 0,
}
"""
ONNX Runtime 会话选项

配置说明:
- intra_op_num_threads: 算子内部线程数(根据文档, 设为0则使用所有处理器)
- inter_op_num_threads: 算子间并行线程数(根据文档, 设为0则使用所有处理器)
- graph_optimization_level: 图优化级别(需要导入 onnxruntime 后设置)

由于pillow和onnxruntime都具备并行处理能力, 因此不必再自己造多进程处理的功能。
"""


# ============================================================================
# 图像预处理配置
# ============================================================================

IMAGE_SIZE: tuple = (224, 224)
"""
输入图像尺寸 (宽, 高)

根据使用的模型调整:
- ViT-B/16: (224, 224)
- ResNet-50: (224, 224)
- EfficientNet: 可能需要其他尺寸
"""

NORMALIZE_MEAN: List[float] = [0.485, 0.456, 0.406]
"""
图像归一化均值 (RGB 通道)

ImageNet 标准值:
- R: 0.485
- G: 0.456
- B: 0.406
"""

NORMALIZE_STD: List[float] = [0.229, 0.224, 0.225]
"""
图像归一化标准差 (RGB 通道)

ImageNet 标准值:
- R: 0.229
- G: 0.224
- B: 0.225
"""


# ============================================================================
# 性能优化建议
# ============================================================================

def get_recommended_num_workers() -> int:
    """
    获取推荐的 Worker 数量
    
    返回:
        推荐的 Worker 进程数
        
    策略:
        - CPU 密集型任务: CPU 核心数 - 1
        - 保留一个核心给主进程和系统
        - 最少 1 个 Worker
    """
    import os
    cpu_count = os.cpu_count() or 1
    return max(1, cpu_count - 1)


# ============================================================================
# GPU 配置建议
# ============================================================================

# 如果使用 GPU,可以取消以下注释并根据实际情况调整:
#
# ONNX_PROVIDERS = [
#     'CUDAExecutionProvider',
#     'CPUExecutionProvider',  # 回退选项
# ]
#
# CUDA_PROVIDER_OPTIONS = {
#     'device_id': 0,  # GPU 设备 ID
#     'arena_extend_strategy': 'kNextPowerOfTwo',
#     'gpu_mem_limit': 2 * 1024 * 1024 * 1024,  # 2GB
#     'cudnn_conv_algo_search': 'EXHAUSTIVE',
# }


# ============================================================================
# 日志配置
# ============================================================================

LOG_LEVEL: str = "INFO"
"""日志级别: DEBUG, INFO, WARNING, ERROR, CRITICAL"""

LOG_FORMAT: str = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
"""日志格式"""

LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
"""日志时间格式"""