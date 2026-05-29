"""Tests for data models — Folder, Notebook, Query, HierarchyStats."""

import pytest

from src.models import Folder, HierarchyStats, Notebook, Query


class TestFolder:
    def _make_tree(self) -> Folder:
        """Build a three-level folder tree for reuse across tests.

        Structure::

            root
            ├── finance (notebooks: budget, revenue)
            │   └── archive (notebooks: legacy)
            └── engineering (no notebooks)
        """
        archive = Folder(id="arch", name="Archive", parent_folder_id="fin")
        archive.notebooks = [Notebook(id="n3", name="Legacy", arn="arn:...:n3")]

        finance = Folder(id="fin", name="Finance", parent_folder_id="root")
        finance.notebooks = [
            Notebook(id="n1", name="Budget", arn="arn:...:n1"),
            Notebook(id="n2", name="Revenue", arn="arn:...:n2"),
        ]
        finance.children = [archive]

        engineering = Folder(id="eng", name="Engineering", parent_folder_id="root")

        root = Folder(id="root", name="Root")
        root.children = [finance, engineering]
        return root

    def test_total_folders(self):
        root = self._make_tree()
        # finance + archive + engineering = 3
        assert root.total_folders == 3

    def test_total_notebooks(self):
        root = self._make_tree()
        # finance(2) + archive(1) + engineering(0) = 3
        assert root.total_notebooks == 3

    def test_total_notebooks_empty_tree(self):
        root = Folder(id="r", name="Root")
        assert root.total_notebooks == 0

    def test_max_depth(self):
        root = self._make_tree()
        # root → finance → archive = depth 2
        assert root.max_depth == 2

    def test_max_depth_flat(self):
        root = Folder(id="r", name="Root")
        root.children = [Folder(id="c", name="Child")]
        assert root.max_depth == 1

    def test_max_depth_no_children(self):
        assert Folder(id="r", name="Root").max_depth == 0

    def test_total_queries(self):
        root = Folder(id="r", name="Root")
        root.queries = [
            Query(id="q1", name="Q1", arn="arn:...:q1", sql="SELECT 1"),
            Query(id="q2", name="Q2", arn="arn:...:q2", sql="SELECT 2"),
        ]
        child = Folder(id="c", name="Child")
        child.queries = [Query(id="q3", name="Q3", arn="arn:...:q3", sql="SELECT 3")]
        root.children = [child]
        assert root.total_queries == 3

    def test_default_fields(self):
        f = Folder(id="x", name="X")
        assert f.parent_folder_id is None
        assert f.children == []
        assert f.notebooks == []
        assert f.queries == []


class TestNotebook:
    def test_defaults(self):
        nb = Notebook(id="1", name="Test", arn="arn:...:1")
        assert nb.folder_id is None
        assert nb.created_at is None
        assert nb.updated_at is None

    def test_fields(self):
        nb = Notebook(
            id="abc",
            name="Revenue Check",
            arn="arn:aws:sqlworkbench:ap-south-1:123:notebook/abc",
            folder_id="folder-1",
            created_at="2024-01-01T00:00:00Z",
        )
        assert nb.id == "abc"
        assert nb.name == "Revenue Check"
        assert nb.folder_id == "folder-1"


class TestQuery:
    def test_fields(self):
        q = Query(
            id="q1",
            name="Revenue Check",
            arn="arn:aws:sqlworkbench:ap-south-1:123:query/q1",
            sql="SELECT * FROM revenue;",
            folder_id="folder-1",
        )
        assert q.id == "q1"
        assert q.sql == "SELECT * FROM revenue;"
        assert q.folder_id == "folder-1"

    def test_default_folder_id(self):
        q = Query(id="q", name="Q", arn="arn:...", sql="")
        assert q.folder_id is None


class TestHierarchyStats:
    def test_fields(self):
        stats = HierarchyStats(
            total_folders=5,
            total_notebooks=12,
            total_queries=3,
            max_depth=4,
        )
        assert stats.total_folders == 5
        assert stats.total_notebooks == 12
        assert stats.total_queries == 3
        assert stats.max_depth == 4
