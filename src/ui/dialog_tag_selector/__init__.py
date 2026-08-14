"""标签选择组件。

提供可搜索的双列表 TagSelectorWidget（左侧可选、右侧已选）、可替换的
搜索匹配器，以及与组件集成的新建标签对话框 NewTagDialog。
"""

from .dialog_new_tag import NewTagDialog
from .matcher import SubstringTagSearchMatcher, TagSearchMatcher
from .widget import (
    TAG_ACCENT_COLOR_ROLE,
    TAG_DATA_ROLE,
    TAG_ID_ROLE,
    TagSelectorWidget,
)

__all__ = [
    "SubstringTagSearchMatcher",
    "TagSearchMatcher",
    "TagSelectorWidget",
    "NewTagDialog",
    "TAG_ACCENT_COLOR_ROLE",
    "TAG_DATA_ROLE",
    "TAG_ID_ROLE",
]
