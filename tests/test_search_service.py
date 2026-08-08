import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import services.global_instances
import services.search as search_service


class FakeSettingsManager:
    def __init__(self, **values):
        self.values = {
            "recent_search_limit": 3,
            "tag_suggestion_limit": 10,
            **values,
        }

    def get(self, key):
        return self.values[key]


class FakeDatabase:
    def __init__(self, tags=()):
        self.tags = list(tags)
        self.tag_queries = []

    def search_tags(self, query, *, limit):
        self.tag_queries.append((query, limit))
        return self.tags[:limit]


class SearchServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_history_matches_substrings_and_moves_repeats_to_front(self):
        history = search_service.SearchHistory(["Alpha", "Beta", "Alphabet"])

        history.record("beta")

        self.assertEqual(["beta", "Alpha", "Alphabet"], history.values())
        self.assertEqual(("Alpha", "Alphabet"), history.matching("alp", 2))

    def test_text_mode_returns_only_recent_searches(self):
        database = FakeDatabase([SimpleNamespace(name="Alpha tag")])
        provider = search_service.SearchSuggestionsProvider(
            search_service.SearchHistory(["Alpha recent"]),
            FakeSettingsManager(),
            lambda: search_service.SearchType.TEXT,
            database_getter=lambda: database,
        )
        received = []
        provider.suggestions_ready.connect(
            lambda _request_id, _query, suggestions: received.extend(suggestions)
        )

        provider.request_suggestions(1, "Alpha")

        self.assertEqual(["Alpha recent"], [item.title for item in received])
        self.assertEqual([], database.tag_queries)

    def test_tag_mode_appends_ordered_tags_and_deduplicates_recent(self):
        database = FakeDatabase(
            [
                SimpleNamespace(name="Alpha recent"),
                SimpleNamespace(name="Alpha tag"),
            ]
        )
        provider = search_service.SearchSuggestionsProvider(
            search_service.SearchHistory(["Alpha recent"]),
            FakeSettingsManager(tag_suggestion_limit=2),
            lambda: search_service.SearchType.TAG,
            database_getter=lambda: database,
        )
        received = []
        provider.suggestions_ready.connect(
            lambda _request_id, _query, suggestions: received.extend(suggestions)
        )

        provider.request_suggestions(1, "Alpha")

        self.assertEqual(
            ["Alpha recent", "Alpha tag"],
            [item.title for item in received],
        )
        self.assertEqual(
            ["最近搜索", "标签"],
            [item.subtitle for item in received],
        )
        self.assertEqual([("Alpha", 2)], database.tag_queries)

    def test_zero_limits_disable_both_suggestion_groups(self):
        database = FakeDatabase([SimpleNamespace(name="Alpha tag")])
        provider = search_service.SearchSuggestionsProvider(
            search_service.SearchHistory(["Alpha recent"]),
            FakeSettingsManager(
                recent_search_limit=0,
                tag_suggestion_limit=0,
            ),
            lambda: search_service.SearchType.TAG,
            database_getter=lambda: database,
        )
        received = []
        provider.suggestions_ready.connect(
            lambda _request_id, _query, suggestions: received.extend(suggestions)
        )

        provider.request_suggestions(1, "Alpha")

        self.assertEqual([], received)
        self.assertEqual([], database.tag_queries)

    def test_open_search_results_routes_and_opens_result_tab(self):
        database = SimpleNamespace(
            search_stickers_by_tag=lambda query: [query],
            search_stickers_by_text=lambda query: [],
        )
        previous_database = services.global_instances.current_library_db
        services.global_instances.current_library_db = database
        try:
            with patch(
                "services.search.services.sticker_library_viewer_service."
                "open_sticker_results_tab"
            ) as open_tab:
                count = search_service.open_search_results("tag", "Happy")
        finally:
            services.global_instances.current_library_db = previous_database

        self.assertEqual(1, count)
        open_tab.assert_called_once_with(["Happy"], "标签搜索[Happy]")


if __name__ == "__main__":
    unittest.main()
