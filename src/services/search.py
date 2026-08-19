# coding=utf-8
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

import services.global_instances
import services.sticker_library_viewer_service
from ui.widgets.custom_search_box import SearchSuggestion

logger = logging.getLogger(__name__)


class SearchType(str, Enum):
    TAG = "tag"
    TEXT = "text"
    FILENAME = "filename"
    ADVANCED = "advanced"


class SearchHistory:
    """按最近使用顺序维护无需可靠存储的搜索文本。"""

    MAX_STORED_SEARCHES = 100

    def __init__(self, searches: Sequence[str] = ()):
        self._searches: list[str] = []
        for search in searches:
            self._append_existing(search)

    def _append_existing(self, search: str) -> None:
        search = search.strip()
        if not search:
            return
        normalized = search.casefold()
        if any(existing.casefold() == normalized for existing in self._searches):
            return
        self._searches.append(search)
        del self._searches[self.MAX_STORED_SEARCHES :]

    def record(self, search: str) -> None:
        search = search.strip()
        if not search:
            return
        normalized = search.casefold()
        self._searches = [
            existing
            for existing in self._searches
            if existing.casefold() != normalized
        ]
        self._searches.insert(0, search)
        del self._searches[self.MAX_STORED_SEARCHES :]

    def matching(self, query: str, limit: int) -> tuple[str, ...]:
        if limit <= 0:
            return ()
        normalized_query = query.strip().casefold()
        matches = (
            search
            for search in self._searches
            if normalized_query in search.casefold()
        )
        return tuple(list(matches)[:limit])

    def values(self) -> list[str]:
        return list(self._searches)


class SearchSuggestionsProvider(QObject):
    """根据当前搜索类型提供最近搜索和数据库标签候选。"""

    suggestions_ready = pyqtSignal(int, str, object)

    def __init__(
        self,
        history: SearchHistory,
        settings_manager,
        search_type_getter: Callable[[], SearchType | str],
        parent: QObject | None = None,
        *,
        database_getter: Callable[[], object | None] | None = None,
    ):
        super().__init__(parent)
        self._history = history
        self._settings_manager = settings_manager
        self._search_type_getter = search_type_getter
        self._database_getter = database_getter or (
            lambda: services.global_instances.current_library_db
        )

    @pyqtSlot(int, str)
    def request_suggestions(self, request_id: int, query: str) -> None:
        normalized_query = query.strip()
        recent_limit = max(
            0,
            int(self._settings_manager.get("recent_search_limit")),
        )
        recent_searches = self._history.matching(
            normalized_query,
            recent_limit,
        )
        suggestions = [
            SearchSuggestion(search, "最近搜索")
            for search in recent_searches
        ]

        search_type = SearchType(self._search_type_getter())
        if search_type is SearchType.TAG and normalized_query:
            self._append_tag_suggestions(
                suggestions,
                normalized_query,
            )

        self.suggestions_ready.emit(request_id, query, tuple(suggestions))

    def _append_tag_suggestions(
        self,
        suggestions: list[SearchSuggestion],
        query: str,
    ) -> None:
        tag_limit = max(
            0,
            int(self._settings_manager.get("tag_suggestion_limit")),
        )
        database = self._database_getter()
        if tag_limit == 0 or database is None:
            return

        existing_titles = {
            suggestion.title.casefold()
            for suggestion in suggestions
        }
        try:
            tags = database.search_tags(query, limit=tag_limit)
        except Exception:
            logger.exception("加载标签搜索候选失败")
            return

        suggestions.extend(
            SearchSuggestion(tag.name, "标签")
            for tag in tags
            if tag.name.casefold() not in existing_titles
        )


def open_search_results(search_type: SearchType | str, query: str) -> int:
    """执行数据库搜索并在主窗口中打开结果标签页。"""
    database = services.global_instances.current_library_db
    if database is None:
        raise RuntimeError("图库数据库尚未初始化。")

    search_type = SearchType(search_type)
    if search_type is SearchType.TAG:
        images = database.search_stickers_by_tag(query)
        title = f"标签搜索[{query}]"
    elif search_type is SearchType.ADVANCED:
        images = database.search_stickers_by_tag_expression(query)
        services.sticker_library_viewer_service.open_advanced_search_results_tab(
            query,
            images,
        )
        return len(images)
    elif search_type is SearchType.FILENAME:
        images = database.search_stickers_by_file_name(query)
        title = f"文件名搜索[{query}]"
    else:
        images = database.search_stickers_by_text(query)
        title = f"文本搜索[{query}]"

    services.sticker_library_viewer_service.open_search_results_tab(
        images,
        title,
    )
    return len(images)
