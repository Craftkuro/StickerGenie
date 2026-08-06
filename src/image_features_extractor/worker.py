"""
Image Features Extractor Worker 进程实现

本模块实现了在独立进程中运行的 Worker,负责实际的特征提取工作。
Worker 从任务队列获取任务,加载图像,执行 ONNX 推理,并将结果返回。

核心组件:
- FeatureExtractionWorker: Worker 进程类
- worker_process_entry: 进程入口点函数

参考: DESIGN.md 第 3.1 节
"""

import multiprocessing
import logging
import time
import traceback
from pathlib import Path
from typing import Optional, Any
from queue import Empty

import numpy as np
import onnxruntime as ort
from PIL import Image
from torchvision.models import ViT_B_16_Weights

from .tasks import ExtractionTask, ExtractionResult, WorkerCommand, create_error_result, create_success_result
from .config import (
    ONNX_PROVIDERS,
    ONNX_SESSION_OPTIONS,
    DEFAULT_RESULT_GET_TIMEOUT,
)
from .exceptions import (
    ModelNotFoundError,
    InvalidImageError,
    FeatureExtractionError,
)


# 配置日志
logger = logging.getLogger(__name__)


class FeatureExtractionWorker:
    """
    Worker 进程类,负责实际的特征提取工作
    
    该类在独立进程中运行,避免 Python GIL 的影响。每个 Worker 维护自己的
    ONNX Runtime 会话和预处理 transforms,从任务队列获取任务并处理。
    
    属性:
        worker_id (int): Worker 唯一标识符
        model_path (str): ONNX 模型文件路径
        task_queue (multiprocessing.Queue): 接收任务的队列
        result_queue (multiprocessing.Queue): 发送结果的队列
        control_queue (multiprocessing.Queue): 接收控制命令的队列
        ort_session (ort.InferenceSession): ONNX Runtime 推理会话
        transform: 图像预处理转换
        input_name (str): ONNX 模型输入节点名称
        
    参考: DESIGN.md 第 3.1 节
    """
    
    def __init__(
        self,
        worker_id: int,
        model_path: str,
        task_queue: 'multiprocessing.Queue[ExtractionTask]',
        result_queue: 'multiprocessing.Queue[ExtractionResult]',
        control_queue: 'multiprocessing.Queue[WorkerCommand]',
    ):
        """
        初始化 Worker 进程
        
        参数:
            worker_id: Worker 唯一标识符,用于日志和调试
            model_path: ONNX 模型文件路径(绝对路径或相对路径)
            task_queue: 接收 ExtractionTask 的队列(多个 Worker 共享)
            result_queue: 发送 ExtractionResult 的队列(所有 Worker 共享)
            control_queue: 接收 WorkerCommand 的队列(每个 Worker 独立)
            
        注意:
            - 此方法在主进程中调用,不应执行耗时操作
            - 模型加载在 _init_model() 中进行,该方法在子进程中调用
        """
        self.worker_id = worker_id
        self.model_path = model_path
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.control_queue = control_queue
        
        # 这些属性将在子进程中初始化
        self.ort_session: Optional['ort.InferenceSession'] = None
        self.transform: Optional[Any] = None
        self.input_name: Optional[str] = None
        
        logger.info(f"Worker {worker_id} 已创建")
    
    def _init_model(self) -> None:
        """
        加载 ONNX 模型和预处理 transforms
        
        此方法在子进程中调用,执行耗时的模型加载操作。
        
        抛出:
            ModelNotFoundError: 模型文件不存在
            RuntimeError: ONNX Runtime 初始化失败
            
        注意:
            - 使用配置文件中的 ONNX_PROVIDERS 和 ONNX_SESSION_OPTIONS
            - 优先使用 GPU (CUDA),不可用时回退到 CPU
        """
        logger.info(f"Worker {self.worker_id} 正在加载模型: {self.model_path}")
        
        # 验证模型文件存在
        model_file = Path(self.model_path)
        if not model_file.exists():
            raise ModelNotFoundError(self.model_path)
        
        try:
            # 创建 ONNX Runtime 会话选项
            sess_options = ort.SessionOptions()
            
            # 应用配置的会话选项
            if 'intra_op_num_threads' in ONNX_SESSION_OPTIONS:
                sess_options.intra_op_num_threads = ONNX_SESSION_OPTIONS['intra_op_num_threads']
            if 'inter_op_num_threads' in ONNX_SESSION_OPTIONS:
                sess_options.inter_op_num_threads = ONNX_SESSION_OPTIONS['inter_op_num_threads']
            
            # 设置图优化级别
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            
            # 创建推理会话
            self.ort_session = ort.InferenceSession(
                str(model_file),
                sess_options=sess_options,
                providers=ONNX_PROVIDERS
            )
            
            # 获取输入节点名称
            self.input_name = self.ort_session.get_inputs()[0].name
            
            # 记录使用的执行提供者
            providers = self.ort_session.get_providers()
            logger.info(f"Worker {self.worker_id} 使用执行提供者: {providers}")
            
        except Exception as e:
            logger.error(f"Worker {self.worker_id} 模型加载失败: {e}")
            raise RuntimeError(f"ONNX Runtime 初始化失败: {e}") from e
        
        # 初始化图像预处理 transforms
        try:
            weights = ViT_B_16_Weights.DEFAULT
            self.transform = weights.transforms()
            logger.info(f"Worker {self.worker_id} 预处理器初始化完成")
        except Exception as e:
            logger.error(f"Worker {self.worker_id} 预处理器初始化失败: {e}")
            raise RuntimeError(f"预处理器初始化失败: {e}") from e
        
        logger.info(f"Worker {self.worker_id} 模型加载完成")
    
    def _extract_features(self, image_path: str) -> np.ndarray:
        """
        从图像文件提取特征向量
        
        执行步骤:
        1. 加载图像文件
        2. 转换为 RGB 格式
        3. 应用预处理转换
        4. 执行 ONNX 推理
        5. 返回特征向量
        
        参数:
            image_path: 图像文件路径
            
        返回:
            特征向量 (numpy.ndarray),一维数组
            
        抛出:
            InvalidImageError: 图像文件无效或无法加载
            FeatureExtractionError: 特征提取过程中发生错误
            
        参考: image_similarity.py 的 _extract_features 方法
        """
        try:
            # 1. 加载图像
            image = Image.open(image_path).convert('RGB')
            
        except FileNotFoundError:
            raise InvalidImageError(image_path, "文件不存在")
        except Exception as e:
            raise InvalidImageError(image_path, f"无法加载图像: {e}")
        
        try:
            # 2. 图像预处理
            image_tensor = self.transform(image).unsqueeze(0)
            
            # 3. 转换为 NumPy 数组
            ort_inputs = {self.input_name: image_tensor.cpu().numpy()}
            
            # 4. 执行 ONNX 推理
            ort_outs = self.ort_session.run(None, ort_inputs)
            
            # 5. 获取输出并展平为一维向量
            features = ort_outs[0].flatten()
            
            return features
            
        except Exception as e:
            logger.error(f"Worker {self.worker_id} 特征提取失败: {image_path} - {e}")
            raise FeatureExtractionError(
                image_path,
                f"ONNX 推理失败: {e}",
                worker_id=self.worker_id
            ) from e
    
    def _process_task(self, task: ExtractionTask) -> ExtractionResult:
        """
        处理单个特征提取任务
        
        参数:
            task: 要处理的任务
            
        返回:
            ExtractionResult: 包含特征或错误信息的结果对象
            
        注意:
            - 此方法捕获所有异常,确保 Worker 不会因单个任务失败而崩溃
            - 处理时间仅包括特征提取,不包括队列等待
        """
        start_time = time.time()
        
        try:
            logger.debug(f"Worker {self.worker_id} 开始处理任务: {task.task_id}")
            
            # 提取特征
            features = self._extract_features(task.image_path)
            
            # 计算处理时间
            processing_time = time.time() - start_time
            
            # 创建成功结果
            result = create_success_result(
                task=task,
                features=features,
                worker_id=self.worker_id,
                processing_time=processing_time
            )
            
            logger.debug(
                f"Worker {self.worker_id} 完成任务 {task.task_id}, "
                f"耗时 {processing_time:.3f}s, 特征维度: {features.shape[0]}"
            )
            
            return result
            
        except (InvalidImageError, FeatureExtractionError) as e:
            # 已知的特征提取错误
            processing_time = time.time() - start_time
            logger.warning(f"Worker {self.worker_id} 任务失败: {task.task_id} - {e}")
            
            return create_error_result(
                task=task,
                error_message=str(e),
                worker_id=self.worker_id,
                processing_time=processing_time
            )
            
        except Exception as e:
            # 未预期的错误
            processing_time = time.time() - start_time
            error_msg = f"未预期的错误: {type(e).__name__}: {e}"
            logger.error(
                f"Worker {self.worker_id} 任务异常: {task.task_id}\n{traceback.format_exc()}"
            )
            
            return create_error_result(
                task=task,
                error_message=error_msg,
                worker_id=self.worker_id,
                processing_time=processing_time
            )
    
    def _handle_control_command(self, command: WorkerCommand) -> bool:
        """
        处理控制命令
        
        参数:
            command: 控制命令
            
        返回:
            bool: True 表示应该停止 Worker,False 表示继续运行
        """
        if command == WorkerCommand.STOP:
            logger.info(f"Worker {self.worker_id} 收到停止命令")
            return True
            
        elif command == WorkerCommand.HEALTH_CHECK:
            logger.debug(f"Worker {self.worker_id} 收到健康检查命令")
            # 可以在此处添加健康状态报告逻辑
            return False
            
        else:
            logger.warning(f"Worker {self.worker_id} 收到未知命令: {command}")
            return False
    
    def run(self) -> None:
        """
        Worker 主循环
        
        执行流程:
        1. 初始化模型
        2. 循环从任务队列获取任务
        3. 检查控制命令队列
        4. 处理任务并将结果放入结果队列
        5. 收到 STOP 命令时优雅退出
        
        注意:
            - 此方法在子进程中运行
            - 模型初始化失败会导致 Worker 立即退出
            - 使用非阻塞方式检查控制队列,避免影响任务处理
        """
        try:
            # 初始化模型(在子进程中)
            self._init_model()
            logger.info(f"Worker {self.worker_id} 已启动")
            
        except Exception as e:
            logger.error(f"Worker {self.worker_id} 初始化失败: {e}")
            return
        
        # 主循环
        while True:
            try:
                # 非阻塞检查控制命令
                try:
                    command = self.control_queue.get_nowait()
                    should_stop = self._handle_control_command(command)
                    if should_stop:
                        break
                except Empty:
                    pass
                
                # 阻塞等待任务(超时以便定期检查控制命令)
                try:
                    task = self.task_queue.get(timeout=0.5)
                except Empty:
                    continue
                
                # 处理任务
                result = self._process_task(task)
                
                # 将结果放入结果队列
                try:
                    self.result_queue.put(result, timeout=5.0)
                except Exception as e:
                    logger.error(
                        f"Worker {self.worker_id} 无法放入结果队列: {e}"
                    )
                
            except KeyboardInterrupt:
                logger.info(f"Worker {self.worker_id} 收到中断信号")
                break
                
            except Exception as e:
                logger.error(
                    f"Worker {self.worker_id} 主循环异常:\n{traceback.format_exc()}"
                )
                # 继续运行,除非是致命错误
                time.sleep(0.1)
        
        logger.info(f"Worker {self.worker_id} 已停止")


def worker_process_entry(
    worker_id: int,
    model_path: str,
    task_queue: 'multiprocessing.Queue[ExtractionTask]',
    result_queue: 'multiprocessing.Queue[ExtractionResult]',
    control_queue: 'multiprocessing.Queue[WorkerCommand]',
) -> None:
    """
    Worker 进程入口点函数
    
    此函数用作 multiprocessing.Process 的 target 参数。
    它创建 Worker 实例并启动主循环。
    
    参数:
        worker_id: Worker 唯一标识符
        model_path: ONNX 模型文件路径
        task_queue: 任务队列
        result_queue: 结果队列
        control_queue: 控制队列
        
    示例:
        >>> process = multiprocessing.Process(
        ...     target=worker_process_entry,
        ...     args=(0, "model.onnx", task_q, result_q, control_q)
        ... )
        >>> process.start()
        
    注意:
        - 此函数在独立进程中运行
        - 异常会导致进程退出,但不会影响其他 Worker
    """
    try:
        # 创建 Worker 实例
        worker = FeatureExtractionWorker(
            worker_id=worker_id,
            model_path=model_path,
            task_queue=task_queue,
            result_queue=result_queue,
            control_queue=control_queue,
        )
        
        # 启动主循环
        worker.run()
        
    except Exception as e:
        logger.error(
            f"Worker {worker_id} 进程入口点异常:\n{traceback.format_exc()}"
        )
        raise