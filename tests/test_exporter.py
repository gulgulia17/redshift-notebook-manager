"""Tests for NotebookExporter and helper functions."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.exporter import NotebookExporter, _extract_ipynb, _safe_name
from src.models import Folder, Notebook


class TestSafeName:
    def test_replaces_colon(self):
        assert ":" not in _safe_name("Revenue: Q1")

    def test_replaces_slash(self):
        assert "/" not in _safe_name("2024/Q1")

    def test_strips_leading_dot(self):
        assert not _safe_name("...hidden").startswith(".")

    def test_empty_falls_back_to_unnamed(self):
        assert _safe_name("") == "unnamed"
        assert _safe_name("   ") == "unnamed"

    def test_preserves_apostrophe(self):
        assert "John's Data" == _safe_name("John's Data")

    def test_no_change_for_clean_name(self):
        assert _safe_name("Revenue Validation") == "Revenue Validation"


class TestExtractIpynb:
    def test_dict_definition(self):
        resp = {"data": {"notebookDefinition": {"nbformat": 4, "cells": []}}}
        nb = _extract_ipynb(resp)
        assert nb["nbformat"] == 4

    def test_string_definition(self):
        inner = json.dumps({"nbformat": 4, "cells": [], "metadata": {}})
        resp = {"data": {"notebookDefinition": inner}}
        nb = _extract_ipynb(resp)
        assert isinstance(nb, dict)
        assert nb["nbformat"] == 4

    def test_invalid_json_string_returns_raw_cell(self):
        resp = {"data": {"notebookDefinition": "not-json-{"}}
        nb = _extract_ipynb(resp)
        assert nb["cells"][0]["cell_type"] == "raw"

    def test_missing_key_falls_back(self):
        resp = {"data": {"nbformat": 4, "cells": []}}
        nb = _extract_ipynb(resp)
        assert isinstance(nb, dict)


def _make_notebook(nb_id: str, name: str, folder_id: str = "folder-1") -> Notebook:
    return Notebook(id=nb_id, name=name, arn=f"arn:...:notebook/{nb_id}", folder_id=folder_id)


def _mock_client(ipynb: dict) -> MagicMock:
    client = MagicMock()
    client.export_notebook.return_value = {"data": {"notebookDefinition": ipynb}}
    return client


class TestNotebookExporterExportTree:
    def test_creates_output_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = os.path.join(tmpdir, "exports")
            client = _mock_client({"nbformat": 4, "cells": []})
            root = Folder(id="r", name="Root")
            NotebookExporter(client, output_dir=out).export_tree(root)
            assert os.path.isdir(out)

    def test_exports_notebook_at_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_content = {"nbformat": 4, "cells": [], "metadata": {}}
            client = _mock_client(nb_content)
            root = Folder(id="r", name="Root")
            root.notebooks = [_make_notebook("n1", "Revenue Check")]

            NotebookExporter(client, output_dir=tmpdir).export_tree(root)

            dest = Path(tmpdir) / "Revenue Check.ipynb"
            assert dest.exists()
            data = json.loads(dest.read_text())
            assert data["nbformat"] == 4

    def test_exports_notebook_in_subfolder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = _mock_client({"nbformat": 4, "cells": []})
            child = Folder(id="c", name="Finance")
            child.notebooks = [_make_notebook("n1", "Budget")]
            root = Folder(id="r", name="Root")
            root.children = [child]

            NotebookExporter(client, output_dir=tmpdir).export_tree(root)

            dest = Path(tmpdir) / "Finance" / "Budget.ipynb"
            assert dest.exists()

    def test_deduplicates_name_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = _mock_client({"nbformat": 4, "cells": []})
            root = Folder(id="r", name="Root")
            root.notebooks = [
                _make_notebook("aaa00001", "Report"),
                _make_notebook("bbb00002", "Report"),
            ]

            NotebookExporter(client, output_dir=tmpdir).export_tree(root)

            files = list(Path(tmpdir).glob("Report*.ipynb"))
            assert len(files) == 2

    def test_handles_export_error_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = MagicMock()
            client.export_notebook.side_effect = RuntimeError("API error")
            root = Folder(id="r", name="Root")
            root.notebooks = [_make_notebook("n1", "Broken")]
            # Should not raise — errors are logged and skipped
            NotebookExporter(client, output_dir=tmpdir).export_tree(root)
            assert not (Path(tmpdir) / "Broken.ipynb").exists()
