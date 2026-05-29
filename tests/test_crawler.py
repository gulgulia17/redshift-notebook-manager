"""Tests for the FolderCrawler and its response-parsing helpers."""

from unittest.mock import MagicMock, patch

import pytest

from src.crawler import (
    FolderCrawler,
    _build_folder,
    _build_notebook_stub,
    _extract_root_folder,
    _parse_folders,
    _parse_next_cursor,
    _parse_next_token,
    _parse_resources,
)
from src.models import Folder, Notebook


# ---------------------------------------------------------------------------
# Response parser unit tests (no network)
# ---------------------------------------------------------------------------

class TestExtractRootFolder:
    def test_standard_shape(self):
        resp = {"data": {"rootFolders": {"notebook": "folder-uuid"}}}
        assert _extract_root_folder(resp) == "folder-uuid"

    def test_nested_user_info(self):
        resp = {"data": {"userInfo": {"rootFolders": {"notebook": "nested-uuid"}}}}
        assert _extract_root_folder(resp) == "nested-uuid"

    def test_missing_key_returns_none(self):
        assert _extract_root_folder({}) is None

    def test_query_type(self):
        resp = {"data": {"rootFolders": {"query": "query-root-uuid"}}}
        assert _extract_root_folder(resp, resource_type="query") == "query-root-uuid"

    def test_empty_value_returns_none(self):
        resp = {"data": {"rootFolders": {"notebook": ""}}}
        assert _extract_root_folder(resp) is None


class TestParseFolders:
    def test_standard_shape(self):
        resp = {"data": {"items": [{"id": "a"}, {"id": "b"}]}}
        items = _parse_folders(resp)
        assert len(items) == 2
        assert items[0]["id"] == "a"

    def test_empty_items(self):
        assert _parse_folders({"data": {"items": []}}) == []

    def test_fallback_shape(self):
        resp = {"data": {"folders": [{"id": "x"}]}}
        assert len(_parse_folders(resp)) == 1

    def test_unknown_shape_returns_empty(self):
        assert _parse_folders({"unexpected": "shape"}) == []


class TestParseResources:
    def test_standard_shape(self):
        resp = {"data": {"resources": [{"resourceArn": "arn:1"}, {"resourceArn": "arn:2"}]}}
        assert len(_parse_resources(resp)) == 2

    def test_empty(self):
        assert _parse_resources({"data": {"resources": []}}) == []

    def test_missing_key(self):
        assert _parse_resources({}) == []


class TestParseNextToken:
    def test_present(self):
        resp = {"data": {"nextToken": "tok123"}}
        assert _parse_next_token(resp) == "tok123"

    def test_absent(self):
        assert _parse_next_token({"data": {}}) is None

    def test_empty_string_returns_none(self):
        assert _parse_next_token({"data": {"nextToken": ""}}) is None


class TestParseNextCursor:
    def test_present(self):
        resp = {"data": {"nextCursor": "cursor-abc"}}
        assert _parse_next_cursor(resp) == "cursor-abc"

    def test_absent(self):
        assert _parse_next_cursor({"data": {}}) is None

    def test_empty_string_returns_none(self):
        assert _parse_next_cursor({"data": {"nextCursor": ""}}) is None


class TestBuildFolder:
    def test_standard_item(self):
        raw = {
            "id": "folder-1",
            "title": "Finance",
            "entityDetailObject": {"parentFolderId": "root"},
        }
        folder = _build_folder(raw)
        assert folder.id == "folder-1"
        assert folder.name == "Finance"
        assert folder.parent_folder_id == "root"

    def test_missing_title_falls_back_to_name(self):
        raw = {"id": "x", "name": "Fallback"}
        folder = _build_folder(raw)
        assert folder.name == "Fallback"

    def test_no_parent(self):
        raw = {"id": "y", "title": "Root"}
        folder = _build_folder(raw)
        assert folder.parent_folder_id is None


class TestBuildNotebookStub:
    def test_extracts_arn_and_folder(self):
        resource = {
            "resourceArn": "arn:aws:sqlworkbench:ap-south-1:123:notebook/nb-uuid",
            "tags": [
                {"key": "aws:sqlworkbench:resource-folder", "value": "folder-uuid"},
            ],
        }
        nb = _build_notebook_stub(resource)
        assert nb.id == "nb-uuid"
        assert nb.arn == "arn:aws:sqlworkbench:ap-south-1:123:notebook/nb-uuid"
        assert nb.folder_id == "folder-uuid"
        assert nb.name == ""

    def test_no_tags(self):
        resource = {"resourceArn": "arn:...:notebook/x", "tags": []}
        nb = _build_notebook_stub(resource)
        assert nb.folder_id == ""


# ---------------------------------------------------------------------------
# FolderCrawler integration tests (mocked client)
# ---------------------------------------------------------------------------

def _mock_client(root_folder_id: str = "root-uuid", owner_id: str = "AIDA123") -> MagicMock:
    client = MagicMock()
    client.get_user_info.return_value = {
        "data": {
            "id": owner_id,
            "rootFolders": {"notebook": root_folder_id},
        }
    }
    client.get_owner_user_id.return_value = owner_id
    # Return empty folders and notebooks by default
    client.list_folders.return_value = {"data": {"items": [], "nextToken": None}}
    client.list_notebooks.return_value = {"data": {"resources": [], "nextCursor": ""}}
    client.list_queries.return_value = {"data": {"resources": [], "nextCursor": ""}}
    return client


class TestFolderCrawlerCrawl:
    def test_auto_discovers_root_folder(self):
        client = _mock_client(root_folder_id="discovered-root")
        root = FolderCrawler(client).crawl()
        assert root.id == "discovered-root"
        client.get_user_info.assert_called_once()

    def test_skips_discovery_when_root_provided(self):
        client = _mock_client()
        root = FolderCrawler(client).crawl(root_folder_id="explicit-root")
        assert root.id == "explicit-root"
        client.get_user_info.assert_not_called()

    def test_empty_tree_returns_root_with_no_children(self):
        client = _mock_client()
        root = FolderCrawler(client).crawl()
        assert root.children == []
        assert root.notebooks == []

    def test_raises_on_missing_root_folder(self):
        client = _mock_client()
        client.get_user_info.return_value = {"data": {"id": "x", "rootFolders": {}}}
        with pytest.raises(ValueError, match="Credential mismatch"):
            FolderCrawler(client).crawl()

    def test_single_child_folder(self):
        client = _mock_client()
        client.list_folders.side_effect = [
            # First call (root): one child folder
            {"data": {"items": [{"id": "child-1", "title": "Finance"}], "nextToken": None}},
            # Second call (child): no grandchildren
            {"data": {"items": [], "nextToken": None}},
        ]
        root = FolderCrawler(client).crawl()
        assert len(root.children) == 1
        assert root.children[0].name == "Finance"


class TestFolderCrawlerStats:
    def test_stats_from_populated_tree(self):
        root = Folder(id="r", name="Root")
        child = Folder(id="c", name="Child")
        child.notebooks = [Notebook(id="n", name="N", arn="arn:...")]
        root.children = [child]

        stats = FolderCrawler.stats(root)
        assert stats.total_folders == 1
        assert stats.total_notebooks == 1
        assert stats.max_depth == 1

    def test_stats_empty_tree(self):
        stats = FolderCrawler.stats(Folder(id="r", name="Root"))
        assert stats.total_folders == 0
        assert stats.total_notebooks == 0
        assert stats.max_depth == 0
