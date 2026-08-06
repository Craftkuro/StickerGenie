"""
Image Features Extractor 进程管理器和主接口类

本模块实现了特征提取器的主要管理类 ImageFeaturesExtractor,负责:
- 创建和管理 Worker 进程池
- 提供同步/异步任务提交接口
- 从结果队列收集结果并触发信号
- 管理进程生命周期

核心组件:
- ImageFeaturesExtractor: 主管理类,对外暴露的 API

参考: DESIGN.md 第 3.2 和第 6 节
"""

import multiprocessing as mp
import threading
import logging
import time
import uuid
from pathlib import Path
from typing import Optional, Callable, Dict, List, Any
from queue import Empty

import numpy as np
from PyQt6.QtCore import QObject

from .worker import worker_process_entry
from .signals import ExtractorSignals
from .tasks import (
    ExtractionTask,
    ExtractionResult,
    WorkerCommand,
    create_task,
)
from .config import (
    DEFAULT_NUM_WORKERS,
    DEFAULT_MAX_QUEUE_SIZE,
    DEFAULT_CONTROL_QUEUE_SIZE,
    DEFAULT_MODEL_PATH,
    DEFAULT_SYNC_TIMEOUT,
    DEFAULT_SHUTDOWN_TIMEOUT,
    DEFAULT_TASK_SUBMIT_TIMEOUT,
    DEFAULT_RESULT_GET_TIMEOUT,
)
from .exceptions import (
    WorkerInitError,
    TaskSubmissionError,
)


# 配置日志
logger = logging.getLogger(__name__)


class ImageFeaturesExtractor(QObject):
    """
    图像特征提取管理器 - 主接口类
    
    该类协调多个 Worker 进程,提供高效的并发特征提取能力。
    使用多进程架构绕过 Python GIL,支持同步和异步两种提取方式,
    并通过 PyQt6 信号提供异步通知能力。
    
    主要特性:
    - 多进程并行处理,充分利用多核 CPU
    - 同步/异步两种 API,灵活适应不同场景
    - PyQt6 信号系统,无缝集成 GUI 应用
    - 完善的错误处理和资源管理
    - 支持上下文管理器(with 语句)
    
    属性:
        signals (ExtractorSignals): PyQt6 信号对象,用于异步通知
        num_workers (int): Worker 进程数量
        model_path (str): ONNX 模型文件路径
        
    示例:
        基本使用:
        >>> extractor = ImageFeaturesExtractor(num_workers=2)
        >>> extractor.start()
        >>> features = extractor.extract_features_sync("image.jpg")
        >>> extractor.stop()
        
        上下文管理器:
        >>> with ImageFeaturesExtractor() as extractor:
        ...     features = extractor.extract_features_sync("image.jpg")
        
        异步提取:
        >>> def on_done(task_id, path, features):
        ...     print(f"完成: {features.shape}")
        >>> extractor.signals.features_extracted.connect(on_done)
        >>> task_id = extractor.extract_features_async("image.jpg")
        
    参考: DESIGN.md 第 3.2 和第 6 节
    """
    
    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_PATH,
        num_workers: int = DEFAULT_NUM_WORKERS,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        use_cuda: bool = False,
        **kwargs
    ):
        """
        初始化特征提取管理器
        
        参数:
            model_path: ONNX 模型文件路径(绝对路径或相对路径)
            num_workers: Worker 进程数量,建议设为 CPU 核心数 - 1
            max_queue_size: 任务队列最大长度,防止内存溢出
            use_cuda: 是否使用 CUDA 加速(需要 onnxruntime-gpu)
            **kwargs: 其他配置参数(保留用于未来扩展)
            
        注意:
            - 此方法仅创建对象,不会启动 Worker 进程
            - 需要显式调用 start() 方法启动 Worker
            - model_path 会在 Worker 进程中验证和加载
        """
        super().__init__()
        
        self.model_path = model_path
        self.num_workers = num_workers
        self.max_queue_size = max_queue_size
        self.use_cuda = use_cuda
        
        # 创建信号对象
        self.signals = ExtractorSignals()
        
        # 创建进程间通信队列
        self._task_queue: mp.Queue = mp.Queue(maxsize=max_queue_size)
        self._result_queue: mp.Queue = mp.Queue()  # 无大小限制
        
        # Worker 进程列表和控制队列
        self._workers: List[mp.Process] = []
        self._control_queues: List[mp.Queue] = []
        
        # 结果收集线程
        self._result_thread: Optional[threading.Thread] = None
        self._stop_result_thread = threading.Event()
        
        # 待处理任务字典: task_id -> (callback, event)
        # event 用于同步等待
        self._pending_tasks: Dict[str, tuple[Optional[Callable], Optional[threading.Event]]] = {}
        self._pending_results: Dict[str, ExtractionResult] = {}
        
        # 状态管理
        self._running = False
        self._lock = threading.Lock()
        
        logger.info(
            f"ImageFeaturesExtractor 初始化完成: "
            f"workers={num_workers}, model={model_path}, "
            f"queue_size={max_queue_size}"
        )
    
    def start(self) -> None:
        """
        启动所有 Worker 进程
        
        执行步骤:
        1. 验证状态(确保未启动)
        2. 创建控制队列
        3. 启动 Worker 进程
        4. 启动结果收集线程
        5. 发射信号通知
        
        抛出:
            WorkerInitError: Worker 启动失败
            RuntimeError: 管理器已在运行中
            
        注意:
            - 此方法会阻塞直到所有 Worker 启动完成
            - Worker 进程会在后台持续运行
            - 必须在程序退出前调用 stop() 清理资源
        """
        with self._lock:
            if self._running:
                raise RuntimeError("提取器已在运行中")
            
            logger.info(f"正在启动 {self.num_workers} 个 Worker 进程...")
            
            try:
                # 创建控制队列(每个 Worker 一个)
                self._control_queues = [
                    mp.Queue(maxsize=DEFAULT_CONTROL_QUEUE_SIZE)
                    for _ in range(self.num_workers)
                ]
                
                # 启动 Worker 进程
                for worker_id in range(self.num_workers):
                    process = mp.Process(
                        target=worker_process_entry,
                        args=(
                            worker_id,
                            self.model_path,
                            self._task_queue,
                            self._result_queue,
                            self._control_queues[worker_id],
                        ),
                        daemon=True,  # 守护进程,主进程退出时自动终止
                        name=f"FeatureWorker-{worker_id}"
                    )
                    process.start()
                    self._workers.append(process)
                    
                    logger.info(f"Worker {worker_id} 进程已启动 (PID: {process.pid})")
                    self.signals.worker_started.emit(worker_id)
                
                # 启动结果收集线程
                self._stop_result_thread.clear()
                self._result_thread = threading.Thread(
                    target=self._result_collector_thread,
                    daemon=True,
                    name="ResultCollector"
                )
                self._result_thread.start()
                logger.info("结果收集线程已启动")
                
                self._running = True
                
                # 发射所有 Worker 就绪信号
                self.signals.all_workers_ready.emit()
                logger.info("所有 Worker 已就绪")
                
            except Exception as e:
                # 启动失败,清理已创建的资源
                logger.error(f"Worker 启动失败: {e}")
                self._cleanup()
                raise WorkerInitError(-1, str(e)) from e
    
    def stop(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT) -> None:
        """
        优雅地停止所有 Worker 进程
        
        执行步骤:
        1. 发送 STOP 命令到所有 Worker
        2. 等待 Worker 进程退出(超时机制)
        3. 强制终止未响应的 Worker
        4. 停止结果收集线程
        5. 清理资源和发射信号
        
        参数:
            timeout: 等待 Worker 退出的超时时间(秒)
            
        注意:
            - 此方法会等待所有正在处理的任务完成
            - 超时后会强制终止 Worker 进程
            - 队列中未处理的任务将被丢弃
        """
        with self._lock:
            if not self._running:
                logger.warning("提取器未运行,无需停止")
                return
            
            logger.info(f"正在停止 {len(self._workers)} 个 Worker 进程...")
            
            # 1. 发送停止命令到所有 Worker
            for worker_id, control_queue in enumerate(self._control_queues):
                try:
                    control_queue.put(WorkerCommand.STOP, timeout=1.0)
                    logger.debug(f"已向 Worker {worker_id} 发送停止命令")
                except Exception as e:
                    logger.warning(f"无法向 Worker {worker_id} 发送停止命令: {e}")
            
            # 2. 等待 Worker 进程退出
            start_time = time.time()
            for worker_id, process in enumerate(self._workers):
                remaining_time = timeout - (time.time() - start_time)
                if remaining_time > 0:
                    process.join(timeout=remaining_time)
                
                if process.is_alive():
                    logger.warning(f"Worker {worker_id} 未在超时内退出,强制终止")
                    process.terminate()
                    process.join(timeout=1.0)
                    
                    if process.is_alive():
                        logger.error(f"Worker {worker_id} 无法终止,强制杀死")
                        process.kill()
                
                logger.info(f"Worker {worker_id} 已停止")
                self.signals.worker_stopped.emit(worker_id)
            
            # 3. 停止结果收集线程
            self._stop_result_thread.set()
            if self._result_thread and self._result_thread.is_alive():
                self._result_thread.join(timeout=2.0)
                logger.info("结果收集线程已停止")
            
            # 4. 清理资源
            self._cleanup()
            self._running = False
            
            logger.info("所有 Worker 已停止")
    
    def _cleanup(self) -> None:
        """
        清理资源
        
        内部方法,用于清理队列、进程和线程资源。
        """
        # 清空队列
        self._clear_queue(self._task_queue)
        self._clear_queue(self._result_queue)
        for control_queue in self._control_queues:
            self._clear_queue(control_queue)
        
        # 清空待处理任务
        with self._lock:
            self._pending_tasks.clear()
            self._pending_results.clear()
        
        # 重置列表
        self._workers.clear()
        self._control_queues.clear()
    
    @staticmethod
    def _clear_queue(queue: mp.Queue) -> None:
        """清空队列中的所有元素"""
        try:
            while not queue.empty():
                queue.get_nowait()
        except Empty:
            pass
        except Exception as e:
            logger.debug(f"清空队列时出错: {e}")
    
    def extract_features_async(
        self,
        image_path: str,
        callback: Optional[Callable[[ExtractionResult], None]] = None,
        metadata: Optional[dict] = None
    ) -> str:
        """
        异步提交特征提取任务
        
        任务会被放入队列,由 Worker 进程异步处理。
        完成后通过回调函数或信号通知。
        
        参数:
            image_path: 图像文件路径(绝对路径或相对路径)
            callback: 可选的回调函数,签名为 callback(result: ExtractionResult)
            metadata: 可选的元数据字典,会在结果中返回
            
        返回:
            任务唯一标识符(task_id)
            
        抛出:
            RuntimeError: 提取器未启动
            TaskSubmissionError: 任务提交失败(队列满等)
            
        示例:
            >>> def on_complete(result):
            ...     if result.is_success:
            ...         print(f"完成: {result.features.shape}")
            >>> task_id = extractor.extract_features_async(
            ...     "image.jpg",
            ...     callback=on_complete
            ... )
            
        注意:
            - 此方法立即返回,不会阻塞
            - 回调函数在结果收集线程中执行,应避免耗时操作
            - 也可以通过连接 signals.features_extracted 信号来接收通知
        """
        if not self._running:
            raise RuntimeError("提取器未启动,请先调用 start()")
        
        # 创建任务
        task = create_task(
            image_path=image_path,
            callback=callback
        )
        task.metadata = metadata
        
        # 记录待处理任务
        with self._lock:
            self._pending_tasks[task.task_id] = (callback, None)
        
        # 提交任务到队列
        try:
            self._task_queue.put(task, timeout=DEFAULT_TASK_SUBMIT_TIMEOUT)
            logger.debug(f"任务 {task.task_id} 已提交: {image_path}")
            
            # 发射任务提交信号
            self.signals.task_submitted.emit(task.task_id, image_path)
            
            return task.task_id
            
        except Exception as e:
            # 提交失败,移除待处理记录
            with self._lock:
                self._pending_tasks.pop(task.task_id, None)
            
            error_msg = f"任务队列已满或提交失败: {e}"
            logger.error(f"任务 {task.task_id} 提交失败: {error_msg}")
            raise TaskSubmissionError(task.task_id, error_msg) from e
    
    def extract_features_sync(
        self,
        image_path: str,
        timeout: float = DEFAULT_SYNC_TIMEOUT
    ) -> np.ndarray:
        """
        同步提取图像特征(阻塞方法)
        
        此方法会阻塞等待,直到特征提取完成或超时。
        
        参数:
            image_path: 图像文件路径
            timeout: 超时时间(秒),默认 30 秒
            
        返回:
            提取的特征向量(numpy.ndarray)
            
        抛出:
            RuntimeError: 提取器未启动
            TaskSubmissionError: 任务提交失败
            TimeoutError: 等待超时
            Exception: 特征提取失败(包含错误信息)
            
        示例:
            >>> try:
            ...     features = extractor.extract_features_sync("image.jpg", timeout=10.0)
            ...     print(f"特征维度: {features.shape}")
            ... except TimeoutError:
            ...     print("提取超时")
            ... except Exception as e:
            ...     print(f"提取失败: {e}")
            
        注意:
            - 此方法会阻塞调用线程,不适合在 UI 线程中使用
            - 内部使用 threading.Event 实现阻塞等待
            - 超时后任务仍会在后台继续处理
        """
        if not self._running:
            raise RuntimeError("提取器未启动,请先调用 start()")
        
        # 创建同步事件
        done_event = threading.Event()
        task_id = None
        
        # 内部回调,设置事件
        def sync_callback(result: ExtractionResult):
            with self._lock:
                self._pending_results[result.task_id] = result
            done_event.set()
        
        # 提交异步任务
        task_id = self.extract_features_async(
            image_path=image_path,
            callback=sync_callback
        )
        
        # 更新待处理任务,添加事件
        with self._lock:
            if task_id in self._pending_tasks:
                callback, _ = self._pending_tasks[task_id]
                self._pending_tasks[task_id] = (callback, done_event)
        
        # 等待完成
        if not done_event.wait(timeout=timeout):
            # 超时,清理
            with self._lock:
                self._pending_tasks.pop(task_id, None)
            raise TimeoutError(f"特征提取超时 ({timeout}秒): {image_path}")
        
        # 获取结果
        with self._lock:
            result = self._pending_results.pop(task_id, None)
            self._pending_tasks.pop(task_id, None)
        
        if result is None:
            raise RuntimeError("无法获取提取结果")
        
        # 检查结果
        if result.is_success:
            return result.features
        else:
            raise Exception(f"特征提取失败: {result.error_message}")
    
    def _result_collector_thread(self) -> None:
        """
        结果收集线程
        
        持续从结果队列获取结果,触发回调函数和信号。
        
        内部方法,由 start() 方法启动。
        
        执行流程:
        1. 从结果队列获取结果(非阻塞)
        2. 查找对应的待处理任务
        3. 调用回调函数(如果有)
        4. 发射 PyQt6 信号
        5. 清理待处理任务记录
        
        注意:
            - 此方法在独立线程中运行
            - 回调函数在此线程中执行,应避免耗时操作
            - 信号会在主线程中触发(PyQt6 机制)
        """
        logger.info("结果收集线程开始运行")
        
        while not self._stop_result_thread.is_set():
            try:
                # 非阻塞获取结果
                try:
                    result = self._result_queue.get(timeout=DEFAULT_RESULT_GET_TIMEOUT)
                except Empty:
                    continue
                
                logger.debug(
                    f"收到结果: task_id={result.task_id}, "
                    f"success={result.is_success}, "
                    f"time={result.processing_time:.3f}s"
                )
                
                # 查找待处理任务
                with self._lock:
                    task_info = self._pending_tasks.get(result.task_id)
                
                # 调用回调函数
                if task_info:
                    callback, event = task_info
                    if callback:
                        try:
                            callback(result)
                        except Exception as e:
                            logger.error(
                                f"任务 {result.task_id} 回调函数执行失败: {e}"
                            )
                
                # 发射信号
                if result.is_success:
                    self.signals.features_extracted.emit(
                        result.task_id,
                        result.image_path,
                        result.features
                    )
                else:
                    self.signals.extraction_failed.emit(
                        result.task_id,
                        result.image_path,
                        result.error_message
                    )
                
            except Exception as e:
                logger.error(f"结果收集线程异常: {e}", exc_info=True)
        
        logger.info("结果收集线程已退出")
    
    def is_running(self) -> bool:
        """
        检查提取器是否正在运行
        
        返回:
            True 如果提取器已启动且正在运行,否则 False
        """
        return self._running
    
    @property
    def queue_size(self) -> int:
        """
        获取当前任务队列中的任务数量
        
        返回:
            队列中的任务数量(近似值)
            
        注意:
            由于多进程环境,此值可能不完全准确
        """
        return self._task_queue.qsize()
    
    @property
    def pending_tasks_count(self) -> int:
        """
        获取待处理任务数量
        
        返回:
            待处理(已提交但未完成)的任务数量
        """
        with self._lock:
            return len(self._pending_tasks)
    
    # ========================================================================
    # 上下文管理器支持
    # ========================================================================
    
    def __enter__(self) -> 'ImageFeaturesExtractor':
        """
        进入上下文管理器,自动启动提取器
        
        返回:
            self
            
        示例:
            >>> with ImageFeaturesExtractor() as extractor:
            ...     features = extractor.extract_features_sync("image.jpg")
        """
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """
        退出上下文管理器,自动停止提取器
        
        参数:
            exc_type: 异常类型
            exc_val: 异常值
            exc_tb: 异常追踪
            
        返回:
            False(不抑制异常)
        """
        self.stop()
        return False
    
    def __del__(self):
        """析构函数,确保资源被释放"""
        if self._running:
            logger.warning("提取器未正常停止,在析构时强制停止")
            self.stop(timeout=1.0)