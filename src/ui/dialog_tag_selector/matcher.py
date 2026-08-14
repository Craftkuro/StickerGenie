"""标签搜索匹配器。

搜索算法与组件解耦：组件只依赖 TagSearchMatcher 的 filter_tags 接口，
后续可以替换为其他算法（如模糊匹配）而无需改动 UI 代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Sequence

from commons.dto import Tag


class TagSearchMatcher(ABC):
    """从标签序列中筛选出与查询匹配的标签，保持输入顺序。"""

    @abstractmethod
    def filter_tags(self, tags: Sequence[Tag], query: str) -> List[Tag]:
        raise NotImplementedError


class SubstringTagSearchMatcher(TagSearchMatcher):
    """基于标签名称的不区分大小写子串匹配。"""

    def filter_tags(self, tags: Sequence[Tag], query: str) -> List[Tag]:
        normalized = query.strip().casefold()
        if not normalized:
            return list(tags)
        return [tag for tag in tags if normalized in tag.name.casefold()]
