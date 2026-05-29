"""Notebook exporter for Query Editor V2.

Exports each notebook to an ``.ipynb`` file using ``GET /notebook/<arn>/export``,
preserving the original folder hierarchy on disk.

Output layout::

    storage/exports/
    └── Analytics/
        ├── Q1/
        │   ├── Revenue Validation.ipynb
        │   └── Cost Analysis.ipynb
        └── Archive/
            └── Legacy Report.ipynb

Example::

    from src.aws_cli import SqlWorkbenchClient
    from src.crawler import FolderCrawler
    from src.exporter import NotebookExporter

    client   = SqlWorkbenchClient(region="ap-south-1")
    root     = FolderCrawler(client).crawl()
    exporter = NotebookExporter(client, output_dir="storage/exports")
    exporter.export_tree(root)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .aws_cli import SqlWorkbenchClient
from .models import Folder, Notebook

logger = logging.getLogger(__name__)

# Characters that are not valid in file/directory names on common operating systems
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores.

    Args:
        name: Raw folder or notebook name.

    Returns:
        Sanitised string safe for use as a path component.

    Example::

        _safe_name('Revenue: Q1/2024')  # 'Revenue_ Q1_2024'
    """
    safe = _UNSAFE_CHARS.sub("_", name).strip(". ")
    return safe or "unnamed"


def _extract_ipynb(export_response: dict) -> dict:
    """Extract the notebook dict from an export API response.

    The export endpoint can return the notebook definition in two shapes:

    - ``{"data": {"notebookDefinition": "<json-string>"}}`` — JSON-encoded string
    - ``{"data": {"notebookDefinition": {...}}}`` — already a dict

    Args:
        export_response: Raw response from ``GET /notebook/<arn>/export``.

    Returns:
        Parsed ``.ipynb`` dict.
    """
    payload = export_response.get("data", export_response)
    nb_def = payload.get("notebookDefinition", payload)

    if isinstance(nb_def, str):
        try:
            return json.loads(nb_def)
        except json.JSONDecodeError:
            return {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [{"cell_type": "raw", "metadata": {}, "source": nb_def}],
            }

    if isinstance(nb_def, dict):
        return nb_def

    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [], "_raw": str(nb_def)}


class NotebookExporter:
    """Exports a folder tree of notebooks to ``.ipynb`` files on disk.

    Args:
        client:     Authenticated :class:`~src.aws_cli.SqlWorkbenchClient`.
        output_dir: Root directory for exported files.
                    Created automatically if it does not exist.

    Example::

        exporter = NotebookExporter(client, output_dir="storage/exports")
        exporter.export_tree(root_folder)
    """

    def __init__(self, client: SqlWorkbenchClient, output_dir: str = "storage/exports") -> None:
        self._client = client
        self._output_dir = Path(output_dir)

    def export_tree(self, root: Folder) -> None:
        """Export the full folder tree rooted at *root*.

        Args:
            root: Root :class:`~src.models.Folder` from :class:`~src.crawler.FolderCrawler`.
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Exporting notebooks to %s", self._output_dir.resolve())
        self._export_folder(root, self._output_dir, is_root=True)

    def _export_folder(self, folder: Folder, parent_path: Path, is_root: bool = False) -> None:
        current_path = parent_path if is_root else parent_path / _safe_name(folder.name)
        current_path.mkdir(parents=True, exist_ok=True)
        logger.info("Folder: %s  (%d notebooks)", current_path, len(folder.notebooks))

        for nb in folder.notebooks:
            self._export_notebook(nb, current_path)

        for child in folder.children:
            self._export_folder(child, current_path)

    def _export_notebook(self, nb: Notebook, folder_path: Path) -> None:
        filename = _safe_name(nb.name) + ".ipynb"
        dest = folder_path / filename

        # If a name collision occurs (same display name, different notebook),
        # append the short ID to distinguish the files.
        if dest.exists():
            filename = f"{_safe_name(nb.name)}_{nb.id[:8]}.ipynb"
            dest = folder_path / filename

        try:
            response = self._client.export_notebook(nb.arn)
            ipynb = _extract_ipynb(response)
            dest.write_text(json.dumps(ipynb, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("  ✓ %s", dest)
        except Exception as exc:
            logger.error("  ✗ %s — %s", dest, exc)
