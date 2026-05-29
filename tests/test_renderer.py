"""Tests for the tree renderer and JSON exporter."""

import json
import os
import tempfile

import pytest

from src.models import Folder, HierarchyStats, Notebook, Query
from src.renderer import export_json, render_stats, render_tree, to_dict


def _simple_tree() -> Folder:
    root = Folder(id="root", name="Query Editor V2 — Notebooks")
    finance = Folder(id="fin", name="Finance", parent_folder_id="root")
    finance.notebooks = [
        Notebook(id="n1", name="Budget Analysis", arn="arn:...:n1"),
    ]
    root.children = [finance]
    root.notebooks = [
        Notebook(id="n0", name="Root Notebook", arn="arn:...:n0"),
    ]
    return root


class TestRenderTree:
    def test_root_label(self):
        root = _simple_tree()
        output = render_tree(root)
        assert output.startswith("📁 Query Editor V2 — Notebooks")

    def test_child_folder_present(self):
        output = render_tree(_simple_tree())
        assert "Finance" in output

    def test_notebook_icon(self):
        output = render_tree(_simple_tree())
        assert "📄 Budget Analysis" in output

    def test_query_icon(self):
        root = Folder(id="r", name="Root")
        root.queries = [Query(id="q1", name="My Query", arn="arn:...", sql="")]
        output = render_tree(root)
        assert "🔷 My Query" in output

    def test_empty_tree(self):
        root = Folder(id="r", name="Empty Root")
        output = render_tree(root)
        assert output == "📁 Empty Root"

    def test_last_item_uses_bend(self):
        root = Folder(id="r", name="Root")
        root.notebooks = [Notebook(id="n", name="Only", arn="arn:...")]
        output = render_tree(root)
        assert "└──" in output


class TestRenderStats:
    def test_contains_counts(self):
        stats = HierarchyStats(total_folders=3, total_notebooks=7, total_queries=2, max_depth=2)
        output = render_stats(stats)
        assert "3" in output
        assert "7" in output
        assert "2" in output

    def test_contains_labels(self):
        stats = HierarchyStats(total_folders=0, total_notebooks=0, total_queries=0, max_depth=0)
        output = render_stats(stats)
        assert "Total folders" in output
        assert "Total notebooks" in output
        assert "Maximum depth" in output


class TestToDict:
    def test_structure(self):
        root = _simple_tree()
        d = to_dict(root)
        assert d["id"] == "root"
        assert d["type"] == "folder"
        assert len(d["children"]) == 1
        assert d["children"][0]["name"] == "Finance"

    def test_notebooks_serialised(self):
        root = _simple_tree()
        d = to_dict(root)
        nbs = d["notebooks"]
        assert len(nbs) == 1
        assert nbs[0]["name"] == "Root Notebook"
        assert nbs[0]["type"] == "notebook"

    def test_empty_tree(self):
        root = Folder(id="r", name="Root")
        d = to_dict(root)
        assert d["children"] == []
        assert d["notebooks"] == []


class TestExportJson:
    def test_creates_file(self):
        root = _simple_tree()
        stats = HierarchyStats(total_folders=1, total_notebooks=2, total_queries=0, max_depth=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tree.json")
            export_json(root, stats, path)
            assert os.path.exists(path)

    def test_file_content(self):
        root = _simple_tree()
        stats = HierarchyStats(total_folders=1, total_notebooks=2, total_queries=0, max_depth=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "tree.json")
            export_json(root, stats, path)
            with open(path) as fh:
                data = json.load(fh)
            assert data["hierarchy"]["name"] == "Query Editor V2 — Notebooks"
            assert data["stats"]["total_folders"] == 1
            assert data["stats"]["total_notebooks"] == 2

    def test_creates_parent_directories(self):
        root = Folder(id="r", name="Root")
        stats = HierarchyStats(total_folders=0, total_notebooks=0, total_queries=0, max_depth=0)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nested", "dir", "tree.json")
            export_json(root, stats, path)
            assert os.path.exists(path)
