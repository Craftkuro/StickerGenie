"""
Image Features Extractor 任务和结果数据类

本模块定义了特征提取过程中使用的数据结构:
- TaskStatus: 任务状态枚举
- ExtractionTask: 特征提取任务数据类
- ExtractionResult: 特征提取结果数据类
- WorkerCommand: Worker 控制命令枚举

参考: DESIGN.md 第 3.4 节
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Any
import numpy as np
import time


class TaskStatus(Enum):
    """
    任务状态枚举
    
    定义任务在生命周期中的各个状态。
    """
    PENDING = "pending"
    """任务已创建,等待处理"""
    
    PROCESSING = "processing"
    """任务正在处理中"""
    
    COMPLETED = "completed"
    """任务成功完成"""
    
    FAILED = "failed"
    """任务处理失败"""


@dataclass
class ExtractionTask:
    """
    特征提取任务数据类
    
    封装单个特征提取任务的所有信息,用于在进程间传递。
    
    属性:
        task_id: 任务唯一标识符,用于追踪和匹配结果
        image_path: 要处理的图像文件路径(绝对路径或相对路径)
        timestamp: 任务提交时间戳(Unix时间戳,秒)
        callback: 可选的回调函数,任务完成时调用(注意:不能跨进程传递)
        metadata: 可选的元数据字典,用于存储额外信息
        
    示例:
        >>> task = ExtractionTask(
        ...     task_id="task_001",
        ...     image_path="/path/to/image.jpg",
        ...     timestamp=time.time()
        ... )
        
    注意:
        - task_id 应该是全局唯一的
        - callback 函数由于不能序列化,在多进程环境中需要特殊处理
        - timestamp 用于计算任务等待时间和处理时间
    """
    task_id: str
    image_path: str
    timestamp: float
    callback: Optional[Callable[[Any], None]] = None
    metadata: Optional[dict] = None
    
    def __post_init__(self):
        """数据验证"""
        if not self.task_id:
            raise ValueError("task_id 不能为空")
        if not self.image_path:
            raise ValueError("image_path 不能为空")
        if self.timestamp <= 0:
            raise ValueError("timestamp 必须为正数")


@dataclass
class ExtractionResult:
    """
    特征提取结果数据类
    
    封装特征提取的结果,包括成功和失败的情况。
    
    属性:
        task_id: 对应的任务 ID,用于匹配原始任务
        image_path: 处理的图像文件路径
        features: 提取的特征向量(NumPy数组),失败时为 None
        error_message: 错误信息,成功时为 None
        worker_id: 处理该任务的 Worker 进程 ID
        processing_time: 实际处理耗时(秒),不包括队列等待时间
        timestamp: 结果生成时间戳(Unix时间戳,秒)
        
    示例:
        成功结果:
        >>> result = ExtractionResult(
        ...     task_id="task_001",
        ...     image_path="/path/to/image.jpg",
        ...     features=np.array([0.1, 0.2, ...]),
        ...     error_message=None,
        ...     worker_id=0,
        ...     processing_time=0.15,
        ...     timestamp=time.time()
        ... )
        
        失败结果:
        >>> result = ExtractionResult(
        ...     task_id="task_002",
        ...     image_path="/path/to/corrupted.jpg",
        ...     features=None,
        ...     error_message="图像文件已损坏",
        ...     worker_id=1,
        ...     processing_time=0.02,
        ...     timestamp=time.time()
        ... )
        
    注意:
        - features 和 error_message 互斥:成功时 features 非空,失败时 error_message 非空
        - processing_time 仅包括实际处理时间,不包括在队列中的等待时间
    """
    task_id: str
    image_path: str
    features: Optional[np.ndarray]
    error_message: Optional[str]
    worker_id: int
    processing_time: float
    timestamp: Optional[float] = None
    
    def __post_init__(self):
        """数据验证和默认值设置"""
        if self.timestamp is None:
            self.timestamp = time.time()
            
        if not self.task_id:
            raise ValueError("task_id 不能为空")
        if not self.image_path:
            raise ValueError("image_path 不能为空")
        if self.processing_time < 0:
            raise ValueError("processing_time 不能为负数")
            
        # 验证结果的一致性
        if self.features is not None and self.error_message is not None:
            raise ValueError("features 和 error_message 不能同时非空")
        if self.features is None and self.error_message is None:
            raise ValueError("features 和 error_message 不能同时为空")
    
    @property
    def is_success(self) -> bool:
        """
        判断任务是否成功
        
        返回:
            True 如果特征提取成功,False 如果失败
        """
        return self.features is not None
    
    @property
    def feature_dimension(self) -> Optional[int]:
        """
        获取特征向量维度
        
        返回:
            特征向量的维度,失败时返回 None
        """
        if self.features is not None:
            return self.features.shape[0] if len(self.features.shape) == 1 else self.features.size
        return None


class WorkerCommand(Enum):
    """
    Worker 控制命令枚举
    
    用于向 Worker 进程发送控制命令。
    
    命令说明:
        STOP: 请求 Worker 优雅地停止(完成当前任务后退出)
        HEALTH_CHECK: 健康检查,Worker 应响应确认存活
        PAUSE: 暂停处理新任务(保留用于未来扩展)
        RESUME: 恢复处理任务(保留用于未来扩展)
    """
    STOP = "stop"
    """停止 Worker 进程"""
    
    HEALTH_CHECK = "health_check"
    """健康检查命令"""
    
    # 未来扩展
    # PAUSE = "pause"
    # """暂停任务处理"""
    # 
    # RESUME = "resume"
    # """恢复任务处理"""


# ============================================================================
# 辅助函数
# ============================================================================

def create_task(image_path: str, 
                task_id: Optional[str] = None,
                callback: Optional[Callable] = None) -> ExtractionTask:
    """
    创建特征提取任务的辅助函数
    
    参数:
        image_path: 图像文件路径
        task_id: 任务 ID,如果未提供则自动生成
        callback: 可选的回调函数
        
    返回:
        ExtractionTask 实例
        
    示例:
        >>> task = create_task("/path/to/image.jpg")
        >>> task = create_task("/path/to/image.jpg", task_id="custom_id")
    """
    import uuid
    
    if task_id is None:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
    
    return ExtractionTask(
        task_id=task_id,
        image_path=image_path,
        timestamp=time.time(),
        callback=callback
    )


def create_success_result(task: ExtractionTask,
                         features: np.ndarray,
                         worker_id: int,
                         processing_time: float) -> ExtractionResult:
    """
    创建成功结果的辅助函数
    
    参数:
        task: 原始任务
        features: 提取的特征向量
        worker_id: Worker ID
        processing_time: 处理耗时
        
    返回:
        ExtractionResult 实例
    """
    return ExtractionResult(
        task_id=task.task_id,
        image_path=task.image_path,
        features=features,
        error_message=None,
        worker_id=worker_id,
        processing_time=processing_time
    )


def create_error_result(task: ExtractionTask,
                       error_message: str,
                       worker_id: int,
                       processing_time: float) -> ExtractionResult:
    """
    创建错误结果的辅助函数
    
    参数:
        task: 原始任务
        error_message: 错误信息
        worker_id: Worker ID
        processing_time: 处理耗时
        
    返回:
        ExtractionResult 实例
    """
    return ExtractionResult(
        task_id=task.task_id,
        image_path=task.image_path,
        features=None,
        error_message=error_message,
        worker_id=worker_id,
        processing_time=processing_time
    )