"""
GPU 检测和配置工具

本模块提供GPU检测功能，用于确定系统是否支持CUDA以及可用的VRAM大小。
这些信息可用于优化workers数量配置，特别是在启用GPU加速时。

主要功能:
- 检测CUDA是否可用
- 获取GPU VRAM大小
- 基于VRAM建议合适的workers数量

注意: 此模块独立于主提取器类，可在系统设置中单独使用。
"""

import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GPUInfo:
    """GPU 信息数据类"""
    
    cuda_available: bool
    """是否支持CUDA"""
    
    device_count: int
    """GPU设备数量"""
    
    device_name: Optional[str] = None
    """GPU设备名称"""
    
    vram_total_mb: Optional[float] = None
    """总VRAM大小(MB)"""
    
    vram_free_mb: Optional[float] = None
    """可用VRAM大小(MB)"""
    
    recommended_workers: int = 1
    """推荐的workers数量"""
    
    error_message: Optional[str] = None
    """错误信息(如果检测失败)"""
    
    def __str__(self) -> str:
        """返回友好的字符串表示"""
        if not self.cuda_available:
            return f"CUDA不可用 (原因: {self.error_message or '未知'})"
        
        info_parts = [
            f"CUDA可用",
            f"设备数量: {self.device_count}",
        ]
        
        if self.device_name:
            info_parts.append(f"设备名称: {self.device_name}")
        
        if self.vram_total_mb is not None:
            info_parts.append(f"总VRAM: {self.vram_total_mb:.0f} MB")
        
        if self.vram_free_mb is not None:
            info_parts.append(f"可用VRAM: {self.vram_free_mb:.0f} MB")
        
        info_parts.append(f"推荐workers: {self.recommended_workers}")
        
        return " | ".join(info_parts)


def detect_gpu_info() -> GPUInfo:
    """
    检测GPU信息
    
    尝试使用多种方法检测GPU信息:
    1. 优先使用 onnxruntime 的GPU检测
    2. 如果失败，尝试使用 pynvml
    3. 如果都失败，返回CPU模式信息
    
    返回:
        GPUInfo 对象，包含GPU检测结果和建议配置
    
    示例:
        >>> gpu_info = detect_gpu_info()
        >>> print(gpu_info)
        CUDA可用 | 设备数量: 1 | 设备名称: NVIDIA GeForce RTX 3080 | 总VRAM: 10240 MB | 可用VRAM: 8192 MB | 推荐workers: 1
        
        >>> if gpu_info.cuda_available:
        ...     print(f"使用GPU模式，建议workers数量: {gpu_info.recommended_workers}")
        ... else:
        ...     print(f"使用CPU模式: {gpu_info.error_message}")
    """
    # 方法1: 尝试使用 onnxruntime 检测
    gpu_info = _detect_via_onnxruntime()
    if gpu_info.cuda_available:
        return gpu_info
    
    # 方法2: 尝试使用 pynvml 检测
    gpu_info = _detect_via_pynvml()
    if gpu_info.cuda_available:
        return gpu_info
    
    # 都失败了，返回CPU模式
    logger.info("未检测到可用的GPU，将使用CPU模式")
    return gpu_info


def _detect_via_onnxruntime() -> GPUInfo:
    """
    使用 onnxruntime 检测GPU信息
    
    返回:
        GPUInfo 对象
    """
    try:
        import onnxruntime as ort
        
        # 检查可用的执行提供者
        available_providers = ort.get_available_providers()
        
        if 'CUDAExecutionProvider' not in available_providers:
            return GPUInfo(
                cuda_available=False,
                device_count=0,
                error_message="ONNX Runtime未安装GPU支持(onnxruntime-gpu)"
            )
        
        logger.info("检测到 ONNX Runtime CUDA 支持")
        
        # ONNX Runtime 没有直接的VRAM查询API，
        # 所以我们只能确认CUDA可用，但无法获取详细的VRAM信息
        # 对于详细信息，需要依赖torch或其他库
        
        return GPUInfo(
            cuda_available=True,
            device_count=1,  # ONNX Runtime不提供设备计数
            device_name="CUDA Device (via ONNX Runtime)",
            vram_total_mb=None,  # 无法从ONNX Runtime获取
            vram_free_mb=None,
            recommended_workers=1,  # 保守估计
            error_message=None
        )
        
    except ImportError:
        return GPUInfo(
            cuda_available=False,
            device_count=0,
            error_message="未安装 onnxruntime"
        )
    except Exception as e:
        logger.warning(f"使用 onnxruntime 检测GPU失败: {e}")
        return GPUInfo(
            cuda_available=False,
            device_count=0,
            error_message=f"ONNX Runtime检测失败: {str(e)}"
        )


def _detect_via_pynvml() -> GPUInfo:
    """
    使用 pynvml 检测GPU信息
    
    pynvml 是 NVIDIA 官方库的 Python 绑定,提供详细的GPU信息,包括VRAM大小。
    相比 PyTorch,pynvml 是轻量级库,不需要深度学习框架依赖。
    
    返回:
        GPUInfo 对象
    """
    try:
        import pynvml
        
        # 初始化 NVML
        pynvml.nvmlInit()
        
        # 获取GPU数量
        device_count = pynvml.nvmlDeviceGetCount()
        
        if device_count == 0:
            pynvml.nvmlShutdown()
            return GPUInfo(
                cuda_available=False,
                device_count=0,
                error_message="未检测到GPU设备"
            )
        
        # 获取第一个GPU的详细信息(大多数情况下只使用一个GPU)
        device_id = 0
        handle = pynvml.nvmlDeviceGetHandleByIndex(device_id)
        device_name = pynvml.nvmlDeviceGetName(handle)
        
        # 获取VRAM信息(字节转MB)
        memory_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        vram_total_mb = memory_info.total / (1024 * 1024)
        vram_free_mb = memory_info.free / (1024 * 1024)
        
        # 基于VRAM大小建议workers数量
        recommended_workers = _calculate_recommended_workers(vram_total_mb)
        
        logger.info(
            f"检测到GPU: {device_name}, "
            f"VRAM: {vram_total_mb:.0f} MB, "
            f"可用: {vram_free_mb:.0f} MB, "
            f"推荐workers: {recommended_workers}"
        )
        
        # 清理 NVML
        pynvml.nvmlShutdown()
        
        return GPUInfo(
            cuda_available=True,
            device_count=device_count,
            device_name=device_name,
            vram_total_mb=vram_total_mb,
            vram_free_mb=vram_free_mb,
            recommended_workers=recommended_workers,
            error_message=None
        )
        
    except ImportError:
        return GPUInfo(
            cuda_available=False,
            device_count=0,
            error_message="未安装 pynvml 库"
        )
    except Exception as e:
        logger.warning(f"使用 pynvml 检测GPU失败: {e}")
        return GPUInfo(
            cuda_available=False,
            device_count=0,
            error_message=f"pynvml检测失败: {str(e)}"
        )


def _calculate_recommended_workers(vram_mb: float) -> int:
    """
    基于VRAM大小计算推荐的workers数量
    
    注意: 由于架构简化,现在始终返回1个worker。
    保留此函数是为了与GPU检测逻辑兼容。
    
    参数:
        vram_mb: GPU总VRAM大小(MB)
    
    返回:
        推荐的workers数量(始终为1)
    """
    # 简化架构:始终使用单个worker
    # Pillow 自带多线程处理能力,单个worker进程足够
    return 1


def get_gpu_provider_config(
    gpu_info: Optional[GPUInfo] = None,
    device_id: int = 0,
    memory_limit_gb: Optional[float] = None
) -> Tuple[list, Optional[list]]:
    """
    获取ONNX Runtime的GPU提供者配置
    
    根据GPU检测结果生成适合的提供者列表和配置选项。
    
    参数:
        gpu_info: GPU信息对象，如果为None则自动检测
        device_id: GPU设备ID (默认0)
        memory_limit_gb: GPU内存限制(GB)，如果为None则自动计算
    
    返回:
        (providers, provider_options) 元组:
        - providers: 提供者列表 ['CUDAExecutionProvider', 'CPUExecutionProvider']
        - provider_options: 提供者选项列表或None (每个提供者对应一个选项字典)
    
    示例:
        >>> providers, options = get_gpu_provider_config()
        >>> # 在创建ONNX会话时使用
        >>> session = ort.InferenceSession(
        ...     model_path,
        ...     providers=providers,
        ...     provider_options=options
        ... )
    """
    # 如果未提供GPU信息，自动检测
    if gpu_info is None:
        gpu_info = detect_gpu_info()
    
    # 如果CUDA不可用，只使用CPU
    if not gpu_info.cuda_available:
        logger.info("GPU不可用，使用CPU执行提供者")
        return ['CPUExecutionProvider'], None
    
    # 计算内存限制
    if memory_limit_gb is None and gpu_info.vram_free_mb is not None:
        # 使用80%的可用VRAM
        memory_limit_gb = (gpu_info.vram_free_mb * 0.8) / 1024
    
    # 构建CUDA提供者选项
    cuda_options = {
        'device_id': device_id,
        'arena_extend_strategy': 'kNextPowerOfTwo',
        'cudnn_conv_algo_search': 'EXHAUSTIVE',
    }
    
    if memory_limit_gb is not None:
        cuda_options['gpu_mem_limit'] = int(memory_limit_gb * 1024 * 1024 * 1024)
        logger.info(f"设置GPU内存限制: {memory_limit_gb:.2f} GB")
    
    # 提供者列表(CUDA优先，CPU作为后备)
    providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
    
    # 提供者选项(只配置CUDA，CPU使用默认)
    provider_options = [cuda_options, {}]
    
    logger.info(f"配置GPU提供者: 设备ID={device_id}")
    
    return providers, provider_options


# ============================================================================
# 便捷函数
# ============================================================================

def print_gpu_info() -> None:
    """
    打印GPU信息到控制台
    
    用于快速检查系统GPU配置的便捷函数。
    
    示例:
        >>> from image_features_extractor.gpu_utils import print_gpu_info
        >>> print_gpu_info()
        
        === GPU 信息 ===
        CUDA可用 | 设备数量: 1 | 设备名称: NVIDIA GeForce RTX 3080 | 总VRAM: 10240 MB | 可用VRAM: 8192 MB | 推荐workers: 2
        
        推荐配置:
        - 使用GPU加速: 是
        - Workers数量: 2
        - ONNX Runtime: onnxruntime-gpu
    """
    gpu_info = detect_gpu_info()
    
    print("\n" + "=" * 50)
    print("GPU 信息检测结果")
    print("=" * 50)
    print(f"\n{gpu_info}\n")
    
    if gpu_info.cuda_available:
        print("推荐配置:")
        print(f"  - 使用GPU加速: 是")
        print(f"  - Workers数量: {gpu_info.recommended_workers}")
        print(f"  - ONNX Runtime: onnxruntime-gpu")
        
        if gpu_info.vram_total_mb and gpu_info.vram_total_mb < 4000:
            print("\n注意: VRAM较小，建议使用较少的workers或降低batch size")
    else:
        print("推荐配置:")
        print(f"  - 使用GPU加速: 否")
        print(f"  - 原因: {gpu_info.error_message}")
        print(f"  - 建议: 使用CPU模式或安装GPU支持")
    
    print("=" * 50 + "\n")


if __name__ == "__main__":
    # 模块直接运行时，执行GPU检测并打印信息
    logging.basicConfig(
        level=logging.INFO,
        format='[%(levelname)s] %(message)s'
    )
    
    print_gpu_info()