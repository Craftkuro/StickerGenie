"""批处理任务（Job 级）异常定义。

这里只放影响整个任务而不是单条数据的异常；单条数据失败通过
GeneralDataWrapper.hasException 表达，不会抛出异常中断任务。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import JobSummary


class JobError(RuntimeError):
    """批处理任务级失败的基类。"""


class WorkerInitializationError(JobError):
    """子进程初始化失败（setup_func 抛异常时触发）。"""


class WorkerCrashedError(JobError):
    """子进程在未发送正常结束消息前意外退出。"""


class JobCancelledError(JobError):
    """任务在正常完成前被取消。"""

    def __init__(
        self,
        message: str,
        *,
        summary: "JobSummary | None" = None,
    ) -> None:
        super().__init__(message)
        self.summary = summary


class JobTimeoutError(JobError):
    """任务超过配置的超时时间。"""
