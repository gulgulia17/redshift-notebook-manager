"""Saved-query exporter for Query Editor V2.

Writes each saved query to a ``.sql`` file, preserving the folder hierarchy
that the :class:`~src.crawler.FolderCrawler` discovered.

Output layout::

    storage/exports/
    └── Shared/
        ├── Daily Summary.sql
        └── Revenue Check.sql

Example::

    from src.aws_cli import SqlWorkbenchClient
    from src.crawler import FolderCrawler
    from src.query_exporter import QueryExporter

    client   = SqlWorkbenchClient(region="ap-south-1")
    root     = FolderCrawler(client).crawl(resource_type="query")
    exporter = QueryExporter(output_dir="storage/exports")
    exporter.export_tree(root)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .models import Folder, Query

logger = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores.

    Args:
        name: Raw query or folder name.

    Returns:
        Sanitised string safe for use as a path component.
    """
    safe = _UNSAFE_CHARS.sub("_", name).strip(". ")
    return safe or "unnamed"


class QueryExporter:
    """Exports saved queries from a crawled folder tree to ``.sql`` files.

    Args:
        output_dir: Root directory for exported ``.sql`` files.
                    Created automatically if it does not exist.

    Example::

        exporter = QueryExporter(output_dir="storage/exports")
        exporter.export_tree(root_folder)
    """

    def __init__(self, output_dir: str = "storage/exports") -> None:
        self._output_dir = Path(output_dir)

    def export_tree(self, root: Folder) -> None:
        """Export all saved queries in the folder tree.

        Args:
            root: Root :class:`~src.models.Folder` from
                  :class:`~src.crawler.FolderCrawler`.
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Exporting queries to %s", self._output_dir.resolve())
        self._export_folder(root, self._output_dir, is_root=True)

    def _export_folder(self, folder: Folder, parent_path: Path, is_root: bool = False) -> None:
        current_path = parent_path if is_root else parent_path / _safe_name(folder.name)
        current_path.mkdir(parents=True, exist_ok=True)
        logger.info("Folder: %s  (%d queries)", current_path, len(folder.queries))

        for q in folder.queries:
            self._export_query(q, current_path)

        for child in folder.children:
            self._export_folder(child, current_path)

    def _export_query(self, query: Query, folder_path: Path) -> None:
        filename = _safe_name(query.name) + ".sql"
        dest = folder_path / filename

        # Guard against duplicate display names within the same folder.
        if dest.exists():
            filename = f"{_safe_name(query.name)}_{query.id[:8]}.sql"
            dest = folder_path / filename

        try:
            dest.write_text(query.sql, encoding="utf-8")
            logger.info("  ✓ %s", dest)
        except Exception as exc:
            logger.error("  ✗ %s — %s", dest, exc)
