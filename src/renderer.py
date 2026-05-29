"""Terminal tree renderer and JSON exporter for the folder hierarchy.

Example::

    from src.renderer import render_tree, render_stats, export_json

    print(render_tree(root))
    print(render_stats(stats))
    export_json(root, stats, "storage/tree.json")
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .models import Folder, HierarchyStats, Notebook, Query

_TEE   = "├── "
_BEND  = "└── "
_PIPE  = "│   "
_SPACE = "    "


def render_tree(root: Folder) -> str:
    """Return a Unicode box-drawing tree for the folder hierarchy.

    Args:
        root: Root :class:`~src.models.Folder` of the hierarchy.

    Returns:
        Multi-line string suitable for printing to a terminal.

    Example::

        print(render_tree(root))
        # 📁 Query Editor V2 — Notebooks
        # ├── 📁 Finance
        # │   └── 📄 Budget Analysis
        # └── 📁 Archive
    """
    lines: List[str] = [f"📁 {root.name}"]
    _render_node(root, prefix="", lines=lines)
    return "\n".join(lines)


def _render_node(folder: Folder, prefix: str, lines: List[str]) -> None:
    items: List[Any] = [*folder.children, *folder.notebooks, *folder.queries]

    for idx, item in enumerate(items):
        is_last = idx == len(items) - 1
        connector = _BEND if is_last else _TEE
        extension = _SPACE if is_last else _PIPE

        if isinstance(item, Folder):
            lines.append(f"{prefix}{connector}📁 {item.name}")
            _render_node(item, prefix + extension, lines)
        elif isinstance(item, Query):
            lines.append(f"{prefix}{connector}🔷 {item.name}")
        else:
            lines.append(f"{prefix}{connector}📄 {item.name}")


def render_stats(stats: HierarchyStats) -> str:
    """Format hierarchy statistics as a terminal table.

    Args:
        stats: :class:`~src.models.HierarchyStats` from
               :meth:`~src.crawler.FolderCrawler.stats`.

    Returns:
        Multi-line string with counts and max depth.
    """
    sep = "─" * 40
    return "\n".join([
        "",
        sep,
        "Statistics",
        sep,
        f"  Total folders   : {stats.total_folders}",
        f"  Total notebooks : {stats.total_notebooks}",
        f"  Total queries   : {stats.total_queries}",
        f"  Maximum depth   : {stats.max_depth}",
        sep,
    ])


def to_dict(folder: Folder) -> Dict[str, Any]:
    """Serialise a folder tree to a JSON-compatible dict.

    Args:
        folder: Any :class:`~src.models.Folder` node (typically the root).

    Returns:
        Nested dict suitable for ``json.dump``.
    """
    return {
        "id": folder.id,
        "name": folder.name,
        "type": "folder",
        "parentFolderId": folder.parent_folder_id,
        "children": [to_dict(c) for c in folder.children],
        "notebooks": [
            {
                "id": nb.id,
                "name": nb.name,
                "type": "notebook",
                "arn": nb.arn,
                "folderId": nb.folder_id,
                "createdAt": nb.created_at,
                "updatedAt": nb.updated_at,
            }
            for nb in folder.notebooks
        ],
    }


def export_json(root: Folder, stats: HierarchyStats, path: str) -> None:
    """Write the folder hierarchy and statistics to a JSON file.

    Creates parent directories if they do not exist.

    Args:
        root:  Root :class:`~src.models.Folder` of the hierarchy.
        stats: Aggregate :class:`~src.models.HierarchyStats`.
        path:  Destination file path, e.g. ``"storage/tree.json"``.
    """
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    payload: Dict[str, Any] = {
        "hierarchy": to_dict(root),
        "stats": {
            "total_folders": stats.total_folders,
            "total_notebooks": stats.total_notebooks,
            "total_queries": stats.total_queries,
            "max_depth": stats.max_depth,
        },
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
