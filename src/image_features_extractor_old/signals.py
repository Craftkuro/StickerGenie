"""
Image Features Extractor PyQt6 信号定义

本模块定义了特征提取器使用的所有 PyQt6 信号,用于异步通知和事件处理。

信号列表:
- features_extracted: 特征提取完成
- extraction_failed: 特征提取失败
- task_submitted: 任务提交成功
- worker_started: Worker 启动完成
- worker_stopped: Worker 停止
- progress_updated: 进度更新
- all_workers_ready: 所有 Worker 已就绪

参考: DESIGN.md 第 3.3 节
"""

from PyQt6.QtCore import QObject, pyqtSignal


class ExtractorSignals(QObject):
    """
    特征提取器的信号集合
    
    所有信号都在主线程中触发,可以安全地用于更新 UI。
    
    使用示例:
        >>> extractor = ImageFeaturesExtractor()
        >>> extractor.signals.features_extracted.connect(on_features_ready)
        >>> extractor.signals.extraction_failed.connect(on_error)
        >>> extractor.start()
        
    注意:
        - 所有信号都应该在主线程中连接
        - 信号处理函数应该尽快返回,避免阻塞事件循环
        - 对于耗时操作,应该在信号处理函数中启动新的线程或异步任务
    """
    
    # ========================================================================
    # 任务相关信号
    # ========================================================================
    
    features_extracted = pyqtSignal(str, str, object)
    """
    特征提取完成信号
    
    参数:
        task_id (str): 任务唯一标识
        image_path (str): 图像文件路径
        features (np.ndarray): 提取的特征向量
        
    触发时机:
        Worker 成功完成特征提取后触发
        
    示例:
        >>> def on_features_ready(task_id, image_path, features):
        ...     print(f"任务 {task_id} 完成,特征维度: {features.shape}")
        >>> signals.features_extracted.connect(on_features_ready)
    """
    
    extraction_failed = pyqtSignal(str, str, str)
    """
    特征提取失败信号
    
    参数:
        task_id (str): 任务唯一标识
        image_path (str): 图像文件路径
        error_message (str): 错误信息描述
        
    触发时机:
        特征提取过程中发生错误时触发
        
    示例:
        >>> def on_error(task_id, image_path, error_message):
        ...     print(f"任务 {task_id} 失败: {error_message}")
        >>> signals.extraction_failed.connect(on_error)
    """
    
    task_submitted = pyqtSignal(str, str)
    """
    任务提交成功信号
    
    参数:
        task_id (str): 任务唯一标识
        image_path (str): 图像文件路径
        
    触发时机:
        任务成功提交到任务队列后立即触发
        
    示例:
        >>> def on_task_submitted(task_id, image_path):
        ...     print(f"任务 {task_id} 已提交")
        >>> signals.task_submitted.connect(on_task_submitted)
    """
    
    # ========================================================================
    # Worker 生命周期信号
    # ========================================================================
    
    worker_started = pyqtSignal(int)
    """
    Worker 启动完成信号
    
    参数:
        worker_id (int): Worker 进程标识
        
    触发时机:
        Worker 进程成功启动并完成初始化后触发
        
    示例:
        >>> def on_worker_started(worker_id):
        ...     print(f"Worker {worker_id} 已启动")
        >>> signals.worker_started.connect(on_worker_started)
    """
    
    worker_stopped = pyqtSignal(int)
    """
    Worker 停止信号
    
    参数:
        worker_id (int): Worker 进程标识
        
    触发时机:
        Worker 进程正常退出或被强制终止后触发
        
    示例:
        >>> def on_worker_stopped(worker_id):
        ...     print(f"Worker {worker_id} 已停止")
        >>> signals.worker_stopped.connect(on_worker_stopped)
    """
    
    all_workers_ready = pyqtSignal()
    """
    所有 Worker 已就绪信号
    
    参数:
        无
        
    触发时机:
        所有 Worker 进程都启动完成并准备好接收任务时触发
        
    示例:
        >>> def on_all_ready():
        ...     print("所有 Worker 已就绪,可以开始提交任务")
        >>> signals.all_workers_ready.connect(on_all_ready)
    """
    
    # ========================================================================
    # 进度和状态信号
    # ========================================================================
    
    progress_updated = pyqtSignal(int, int)
    """
    进度更新信号
    
    参数:
        completed (int): 已完成的任务数量
        total (int): 总任务数量
        
    触发时机:
        批量处理时,每完成一个任务后触发
        
    示例:
        >>> def on_progress(completed, total):
        ...     progress = (completed / total) * 100
        ...     print(f"进度: {progress:.1f}% ({completed}/{total})")
        >>> signals.progress_updated.connect(on_progress)
    """
    
    # ========================================================================
    # 未来扩展信号(暂未实现)
    # ========================================================================
    
    # worker_crashed = pyqtSignal(int, str)
    # """
    # Worker 崩溃信号
    # 
    # 参数:
    #     worker_id (int): 崩溃的 Worker ID
    #     reason (str): 崩溃原因
    # """
    
    # queue_full_warning = pyqtSignal(int, int)
    # """
    # 队列即将满警告
    # 
    # 参数:
    #     current_size (int): 当前队列大小
    #     max_size (int): 最大队列大小
    # """
    
    # performance_stats = pyqtSignal(dict)
    # """
    # 性能统计信息
    # 
    # 参数:
    #     stats (dict): 包含各种性能指标的字典
    # """


# ============================================================================
# 辅助函数
# ============================================================================

def create_signals() -> ExtractorSignals:
    """
    创建信号对象的工厂函数
    
    返回:
        ExtractorSignals 实例
        
    注意:
        此函数必须在 QApplication 创建之后调用
        
    示例:
        >>> from PyQt6.QtWidgets import QApplication
        >>> app = QApplication([])
        >>> signals = create_signals()
    """
    return ExtractorSignals()


def connect_all_signals(signals: ExtractorSignals,
                       on_extracted=None,
                       on_failed=None,
                       on_submitted=None,
                       on_worker_started=None,
                       on_worker_stopped=None,
                       on_progress=None,
                       on_all_ready=None):
    """
    批量连接信号的辅助函数
    
    参数:
        signals: ExtractorSignals 实例
        on_extracted: features_extracted 信号的处理函数
        on_failed: extraction_failed 信号的处理函数
        on_submitted: task_submitted 信号的处理函数
        on_worker_started: worker_started 信号的处理函数
        on_worker_stopped: worker_stopped 信号的处理函数
        on_progress: progress_updated 信号的处理函数
        on_all_ready: all_workers_ready 信号的处理函数
        
    示例:
        >>> def on_done(task_id, path, features):
        ...     print("完成!")
        >>> connect_all_signals(
        ...     signals,
        ...     on_extracted=on_done,
        ...     on_failed=lambda tid, path, err: print(f"失败: {err}")
        ... )
    """
    if on_extracted is not None:
        signals.features_extracted.connect(on_extracted)
    if on_failed is not None:
        signals.extraction_failed.connect(on_failed)
    if on_submitted is not None:
        signals.task_submitted.connect(on_submitted)
    if on_worker_started is not None:
        signals.worker_started.connect(on_worker_started)
    if on_worker_stopped is not None:
        signals.worker_stopped.connect(on_worker_stopped)
    if on_progress is not None:
        signals.progress_updated.connect(on_progress)
    if on_all_ready is not None:
        signals.all_workers_ready.connect(on_all_ready)


def disconnect_all_signals(signals: ExtractorSignals):
    """
    断开所有信号连接的辅助函数
    
    参数:
        signals: ExtractorSignals 实例
        
    用途:
        在清理资源或重置时使用
        
    示例:
        >>> disconnect_all_signals(extractor.signals)
    """
    signals.features_extracted.disconnect()
    signals.extraction_failed.disconnect()
    signals.task_submitted.disconnect()
    signals.worker_started.disconnect()
    signals.worker_stopped.disconnect()
    signals.progress_updated.disconnect()
    signals.all_workers_ready.disconnect()