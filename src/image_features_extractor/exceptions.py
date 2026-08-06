"""
Image Features Extractor 异常类定义

本模块定义了特征提取过程中可能出现的所有自定义异常。

异常层次结构:
    ExtractorError (基类)
    ├── ModelNotFoundError (模型文件未找到)
    ├── WorkerInitError (Worker 初始化失败)
    ├── TaskSubmissionError (任务提交失败)
    ├── FeatureExtractionError (特征提取失败)
    ├── WorkerCrashedError (Worker 进程崩溃)
    └── InvalidImageError (无效的图像文件)

参考: DESIGN.md 第 8.2 节
"""

from typing import Optional


class ExtractorError(Exception):
    """
    特征提取器基础异常类
    
    所有模块特定的异常都应该继承自此类,便于统一捕获和处理。
    """
    pass


class ModelNotFoundError(ExtractorError):
    """
    模型文件未找到异常
    
    当指定的 ONNX 模型文件路径不存在或无法访问时抛出。
    
    属性:
        model_path (str): 尝试加载的模型文件路径
        
    示例:
        >>> raise ModelNotFoundError("vit_model.onnx")
    """
    
    def __init__(self, model_path: str):
        self.model_path = model_path
        super().__init__(f"模型文件未找到: {model_path}")


class WorkerInitError(ExtractorError):
    """
    Worker 进程初始化失败异常
    
    当 Worker 进程无法正常启动或初始化时抛出,可能的原因包括:
    - 模型加载失败
    - 依赖库缺失
    - 资源不足
    
    属性:
        worker_id (int): 失败的 Worker 标识
        reason (str): 失败原因描述
        
    示例:
        >>> raise WorkerInitError(0, "ONNX Runtime 初始化失败")
    """
    
    def __init__(self, worker_id: int, reason: str):
        self.worker_id = worker_id
        self.reason = reason
        super().__init__(f"Worker {worker_id} 初始化失败: {reason}")


class TaskSubmissionError(ExtractorError):
    """
    任务提交失败异常
    
    当无法将任务成功提交到任务队列时抛出,可能的原因包括:
    - 队列已满
    - Worker 未启动
    - 系统资源不足
    
    属性:
        task_id (str): 任务标识
        reason (str): 失败原因
        
    示例:
        >>> raise TaskSubmissionError("task_001", "任务队列已满")
    """
    
    def __init__(self, task_id: str, reason: str):
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"任务 {task_id} 提交失败: {reason}")


class FeatureExtractionError(ExtractorError):
    """
    特征提取失败异常
    
    当图像特征提取过程中发生错误时抛出,可能的原因包括:
    - 图像加载失败
    - 预处理错误
    - ONNX 推理失败
    - 内存不足
    
    属性:
        image_path (str): 处理失败的图像路径
        reason (str): 失败原因
        worker_id (int, optional): 处理该任务的 Worker ID
        
    示例:
        >>> raise FeatureExtractionError("image.jpg", "图像格式不支持", worker_id=0)
    """
    
    def __init__(self, image_path: str, reason: str, worker_id: Optional[int] = None):
        self.image_path = image_path
        self.reason = reason
        self.worker_id = worker_id
        worker_info = f" (Worker {worker_id})" if worker_id is not None else ""
        super().__init__(f"特征提取失败{worker_info}: {image_path} - {reason}")


class WorkerCrashedError(ExtractorError):
    """
    Worker 进程崩溃异常
    
    当检测到 Worker 进程异常终止时抛出。
    
    属性:
        worker_id (int): 崩溃的 Worker 标识
        exit_code (int, optional): 进程退出码
        
    示例:
        >>> raise WorkerCrashedError(1, exit_code=-1)
    """
    
    def __init__(self, worker_id: int, exit_code: Optional[int] = None):
        self.worker_id = worker_id
        self.exit_code = exit_code
        exit_info = f" (退出码: {exit_code})" if exit_code is not None else ""
        super().__init__(f"Worker {worker_id} 进程崩溃{exit_info}")


class InvalidImageError(ExtractorError):
    """
    无效图像文件异常
    
    当图像文件无效、损坏或不支持时抛出。
    
    属性:
        image_path (str): 无效的图像文件路径
        reason (str): 详细原因
        
    示例:
        >>> raise InvalidImageError("corrupted.jpg", "文件已损坏")
    """
    
    def __init__(self, image_path: str, reason: str):
        self.image_path = image_path
        self.reason = reason
        super().__init__(f"无效的图像文件: {image_path} - {reason}")