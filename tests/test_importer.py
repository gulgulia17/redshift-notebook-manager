"""Tests for NotebookImporter and its helper functions."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from src.importer import NotebookImporter, _ensure_notebook_metadata, _notebook_title


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestNotebookTitle:
    def test_uses_metadata_title(self):
        ipynb = {"metadata": {"title": "Revenue Check"}}
        assert _notebook_title(ipynb, "ignored.ipynb") == "Revenue Check"

    def test_falls_back_to_filename_stem(self):
        assert _notebook_title({}, "Budget Analysis.ipynb") == "Budget Analysis"

    def test_prefers_metadata_over_filename(self):
        ipynb = {"metadata": {"title": "From Metadata"}}
        assert _notebook_title(ipynb, "From File.ipynb") == "From Metadata"

    def test_empty_metadata_title_falls_back(self):
        ipynb = {"metadata": {"title": ""}}
        assert _notebook_title(ipynb, "Fallback.ipynb") == "Fallback"


class TestEnsureNotebookMetadata:
    def test_injects_kernelspec_when_missing(self):
        nb = _ensure_notebook_metadata({}, "My Notebook")
        assert nb["metadata"]["kernelspec"]["name"] == "Redshift"

    def test_does_not_overwrite_existing_kernelspec(self):
        nb = _ensure_notebook_metadata(
            {"metadata": {"kernelspec": {"name": "Custom"}}},
            "Title",
        )
        assert nb["metadata"]["kernelspec"]["name"] == "Custom"

    def test_sets_title(self):
        nb = _ensure_notebook_metadata({}, "My Title")
        assert nb["metadata"]["title"] == "My Title"

    def test_sets_nbformat_defaults(self):
        nb = _ensure_notebook_metadata({}, "T")
        assert nb["nbformat"] == 4
        assert nb["nbformat_minor"] == 0

    def test_preserves_existing_cells(self):
        cells = [{"cell_type": "code", "source": "SELECT 1"}]
        nb = _ensure_notebook_metadata({"cells": cells}, "T")
        assert nb["cells"] == cells

    def test_sets_version(self):
        nb = _ensure_notebook_metadata({}, "T")
        assert nb["metadata"]["version"] == 1


# ---------------------------------------------------------------------------
# NotebookImporter integration tests (mocked client + temp filesystem)
# ---------------------------------------------------------------------------

def _mock_client(
    root_folder_id: str = "root-uuid",
    owner_id: str = "AIDA123",
) -> MagicMock:
    client = MagicMock()
    client.get_owner_user_id.return_value = owner_id
    client.get_user_info.return_value = {
        "data": {
            "id": owner_id,
            "rootFolders": {"notebook": root_folder_id},
        }
    }
    # list_folders returns empty by default (no existing folders)
    client.list_folders.return_value = {"data": {"items": [], "nextToken": None}}
    # create_folder returns a new UUID
    client.create_folder.return_value = {"data": {"id": "new-folder-uuid"}}
    # import_notebook returns a new ARN
    client.import_notebook.return_value = {
        "data": {"id": "arn:...:notebook/new-nb-uuid", "folderId": "new-folder-uuid"}
    }
    return client


def _write_notebook(path: Path, title: str = "Test Notebook") -> None:
    """Write a minimal exportedNotebook-wrapped .ipynb file to *path*."""
    payload = {
        "title": title,
        "exportedNotebook": {
            "nbformat": 4,
            "nbformat_minor": 0,
            "metadata": {},
            "cells": [],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestNotebookImporterImportTree:
    def test_creates_folder_and_imports_notebook(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = Path(tmpdir) / "Revenue Validation.ipynb"
            _write_notebook(nb_path, "Revenue Validation")

            client = _mock_client()
            importer = NotebookImporter(client)
            importer.import_tree(source_dir=tmpdir)

            client.create_folder.assert_called_once()
            client.import_notebook.assert_called_once()

    def test_imports_notebook_with_correct_title(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_notebook(Path(tmpdir) / "Budget.ipynb", "Budget Analysis")

            client = _mock_client()
            NotebookImporter(client).import_tree(source_dir=tmpdir)

            call_kwargs = client.import_notebook.call_args
            nb_def = call_kwargs[1]["notebook_definition"]
            assert nb_def["metadata"]["title"] == "Budget Analysis"

    def test_reuses_existing_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_notebook(Path(tmpdir) / "nb.ipynb")

            client = _mock_client()
            # list_folders returns a pre-existing folder with the same name
            dir_name = os.path.basename(tmpdir)
            client.list_folders.return_value = {
                "data": {
                    "items": [{"id": "existing-uuid", "title": dir_name}],
                    "nextToken": None,
                }
            }

            NotebookImporter(client).import_tree(source_dir=tmpdir)

            # create_folder must NOT be called — the existing folder is reused
            client.create_folder.assert_not_called()
            client.import_notebook.assert_called_once()

    def test_source_dir_not_found_raises(self):
        client = _mock_client()
        with pytest.raises(FileNotFoundError):
            NotebookImporter(client).import_tree(source_dir="/nonexistent/path")

    def test_base_dir_outside_source_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = _mock_client()
            with pytest.raises(ValueError, match="not inside"):
                NotebookImporter(client).import_tree(
                    source_dir=tmpdir,
                    base_dir="/some/other/path",
                )

    def test_import_base_creates_intermediate_folders(self):
        with tempfile.TemporaryDirectory() as base:
            # base/Analytics/Q1/notebook.ipynb
            leaf = Path(base) / "Analytics" / "Q1"
            leaf.mkdir(parents=True)
            _write_notebook(leaf / "Cost Analysis.ipynb", "Cost Analysis")

            client = _mock_client()
            # Every create_folder call returns a unique ID
            folder_ids = iter(["analytics-uuid", "q1-uuid", "q1-uuid"])
            client.create_folder.side_effect = lambda **kw: {"data": {"id": next(folder_ids)}}

            NotebookImporter(client).import_tree(
                source_dir=str(leaf),
                base_dir=base,
            )

            # Analytics + Q1 = 2 folders created
            assert client.create_folder.call_count == 2
            client.import_notebook.assert_called_once()

    def test_import_notebook_error_increments_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_notebook(Path(tmpdir) / "bad.ipynb")

            client = _mock_client()
            client.import_notebook.side_effect = RuntimeError("API error")

            importer = NotebookImporter(client)
            importer.import_tree(source_dir=tmpdir)

            assert importer._failed == 1
            assert importer._imported == 0

    def test_unwraps_exported_notebook_key(self):
        """Verify that exportedNotebook wrapper is stripped before import."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = Path(tmpdir) / "test.ipynb"
            _write_notebook(nb_path, "Wrapped Title")

            client = _mock_client()
            NotebookImporter(client).import_tree(source_dir=tmpdir)

            call_kwargs = client.import_notebook.call_args[1]
            nb_def = call_kwargs["notebook_definition"]
            # "exportedNotebook" key must not be present in what is sent to the API
            assert "exportedNotebook" not in nb_def
            assert nb_def["nbformat"] == 4
