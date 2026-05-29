"""Recursive folder and resource crawler for Query Editor V2.

Traversal strategy:

1. ``GET /user`` → ``data.rootFolders.<type>`` to locate the root folder.
2. ``GET /v2/file?actionName=folders-only`` to page through child folders.
3. ``POST /tagged-resource`` to list notebooks or saved queries per folder.
4. ``GET /notebook/<arn>`` / ``GET /query-saved/<arn>`` to resolve titles.
5. Recurse into child folders until the tree is exhausted.

Example::

    from src.aws_cli import SqlWorkbenchClient
    from src.crawler import FolderCrawler

    client  = SqlWorkbenchClient(region="ap-south-1")
    crawler = FolderCrawler(client)

    root  = crawler.crawl()                        # notebooks
    root  = crawler.crawl(resource_type="query")   # saved queries
    stats = FolderCrawler.stats(root)
    print(stats.total_notebooks)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .aws_cli import SqlWorkbenchClient
from .models import Folder, HierarchyStats, Notebook, Query

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def _extract_root_folder(response: Dict[str, Any], resource_type: str = "notebook") -> Optional[str]:
    """Return ``data.rootFolders.<resource_type>`` from a ``GET /user`` response."""
    for path in [
        ["data", "rootFolders", resource_type],
        ["data", "userInfo", "rootFolders", resource_type],
        ["rootFolders", resource_type],
    ]:
        node: Any = response
        try:
            for key in path:
                node = node[key]
            if node:
                return str(node)
        except (KeyError, TypeError):
            continue
    return None


def _parse_folders(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalise the folder list from ``GET /v2/file?actionName=folders-only``.

    Returns an empty list rather than raising if the shape is unexpected.
    """
    try:
        items = response["data"]["items"]
        if isinstance(items, list):
            return items
    except (KeyError, TypeError):
        pass

    for path in [["data", "files"], ["data", "folders"], ["files"], ["folders"], ["items"]]:
        node: Any = response
        try:
            for key in path:
                node = node[key]
            if isinstance(node, list):
                return node
        except (KeyError, TypeError):
            continue

    logger.warning("Unexpected folder list shape — keys: %s", list(response.keys()))
    return []


def _parse_resources(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the resource list from a ``POST /tagged-resource`` response."""
    try:
        resources = response["data"]["resources"]
        if isinstance(resources, list):
            return resources
    except (KeyError, TypeError):
        pass
    return []


def _parse_next_token(response: Dict[str, Any]) -> Optional[str]:
    """Extract pagination ``nextToken`` from a folder listing response."""
    for path in [["data", "nextToken"], ["nextToken"]]:
        node: Any = response
        try:
            for key in path:
                node = node[key]
            if isinstance(node, str) and node:
                return node
        except (KeyError, TypeError):
            continue
    return None


def _parse_next_cursor(response: Dict[str, Any]) -> Optional[str]:
    """Extract pagination ``nextCursor`` from a tagged-resource response."""
    try:
        cursor = response["data"]["nextCursor"]
        if isinstance(cursor, str) and cursor:
            return cursor
    except (KeyError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Object builders
# ---------------------------------------------------------------------------

def _build_folder(raw: Dict[str, Any]) -> Folder:
    """Construct a :class:`~src.models.Folder` from a ``/v2/file`` item."""
    name = raw.get("title") or raw.get("name") or "Unnamed Folder"
    parent: Optional[str] = None
    try:
        parent = raw["entityDetailObject"]["parentFolderId"]
    except (KeyError, TypeError):
        parent = raw.get("parentFolderId")
    return Folder(id=raw.get("id", ""), name=name, parent_folder_id=parent)


def _build_notebook_stub(resource: Dict[str, Any]) -> Notebook:
    """Construct a title-less :class:`~src.models.Notebook` from a tagged-resource entry.

    The caller is responsible for fetching the full title via ``GET /notebook/<arn>``.
    """
    arn = resource.get("resourceArn", "")
    nb_id = arn.split("/")[-1] if "/" in arn else arn
    folder_id = ""
    for tag in resource.get("tags", []):
        if tag.get("key") == "aws:sqlworkbench:resource-folder":
            folder_id = tag.get("value", "")
            break
    return Notebook(id=nb_id, name="", arn=arn, folder_id=folder_id)


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

class FolderCrawler:
    """Crawls the complete folder and resource tree for a Query Editor V2 account.

    The crawler builds an in-memory :class:`~src.models.Folder` tree that can
    be rendered, exported to JSON, or passed to the notebook exporters.

    Args:
        client: An authenticated :class:`~src.aws_cli.SqlWorkbenchClient`.

    Example::

        client  = SqlWorkbenchClient(region="ap-south-1")
        crawler = FolderCrawler(client)

        root  = crawler.crawl()
        stats = FolderCrawler.stats(root)
        print(f"{stats.total_folders} folders, {stats.total_notebooks} notebooks")
    """

    def __init__(self, client: SqlWorkbenchClient) -> None:
        self._client = client

    def crawl(
        self,
        root_folder_id: Optional[str] = None,
        resource_type: str = "notebook",
    ) -> Folder:
        """Discover the root folder and traverse the full hierarchy.

        Args:
            root_folder_id: Override the auto-discovered root folder.  Useful
                            when the calling credentials differ from the browser
                            session that owns the notebooks.
            resource_type:  ``"notebook"`` or ``"query"``.

        Returns:
            A fully populated :class:`~src.models.Folder` tree.

        Raises:
            ValueError: When root folder discovery fails (credential mismatch).
        """
        if root_folder_id:
            logger.info("Using provided root folder: %s", root_folder_id)
        else:
            logger.info("Discovering root folder via GET /user (type=%s)", resource_type)
            user_info = self._client.get_user_info()
            root_folder_id = _extract_root_folder(user_info, resource_type)

            if not root_folder_id:
                user_id = user_info.get("data", {}).get("id", "unknown")
                raise ValueError(
                    f"\n\nCredential mismatch detected.\n"
                    f"  GET /user returned empty rootFolders for id={user_id}.\n"
                    f"  This IAM identity has no Query Editor V2 root folder initialized.\n\n"
                    f"Options:\n"
                    f"  1. Export browser-session credentials to .env and retry.\n"
                    f"  2. Pass the root folder ID explicitly:\n"
                    f"       python -m main --region ap-south-1 --root-folder <folder-id>\n"
                )

        logger.info("Root folder: %s", root_folder_id)
        label = "Notebooks" if resource_type == "notebook" else "Queries"
        root = Folder(id=root_folder_id, name=f"Query Editor V2 — {label}")
        self._traverse(root, resource_type=resource_type)
        return root

    def _traverse(self, folder: Folder, resource_type: str = "notebook", depth: int = 0) -> None:
        indent = "  " * depth
        logger.info("%sCrawling '%s' (%s)", indent, folder.name, folder.id)

        folder.children = self._fetch_all_folders(folder.id, resource_type)

        if resource_type == "notebook":
            folder.notebooks = self._fetch_all_notebooks(folder.id)
            count, label = len(folder.notebooks), "notebooks"
        else:
            folder.queries = self._fetch_all_queries(folder.id)
            count, label = len(folder.queries), "queries"

        logger.info("%s  → %d sub-folders, %d %s", indent, len(folder.children), count, label)

        for child in folder.children:
            self._traverse(child, resource_type, depth + 1)

    def _fetch_all_folders(self, folder_id: str, resource_type: str = "notebook") -> List[Folder]:
        """Page through all child folders for the given folder ID."""
        results: List[Folder] = []
        next_token: Optional[str] = None

        while True:
            response = self._client.list_folders(folder_id, resource_type=resource_type, next_token=next_token)
            for raw in _parse_folders(response):
                results.append(_build_folder(raw))
            next_token = _parse_next_token(response)
            if not next_token:
                break

        return results

    def _fetch_all_notebooks(self, folder_id: str) -> List[Notebook]:
        """Page through notebooks in a folder and resolve each title."""
        results: List[Notebook] = []
        next_cursor: Optional[str] = None

        while True:
            response = self._client.list_notebooks(folder_id, next_cursor=next_cursor)
            for resource in _parse_resources(response):
                nb = _build_notebook_stub(resource)
                try:
                    detail = self._client.get_notebook(nb.arn)
                    title = (
                        detail.get("data", {}).get("name")
                        or detail.get("data", {}).get("title")
                        or "Untitled"
                    )
                    nb = Notebook(id=nb.id, name=title, arn=nb.arn, folder_id=nb.folder_id)
                except Exception as exc:
                    logger.warning("Could not fetch title for %s: %s", nb.arn, exc)
                    nb = Notebook(id=nb.id, name="Untitled", arn=nb.arn, folder_id=nb.folder_id)
                results.append(nb)

            next_cursor = _parse_next_cursor(response)
            if not next_cursor:
                break

        return results

    def _fetch_all_queries(self, folder_id: str) -> List[Query]:
        """Page through saved queries in a folder and resolve each title and SQL."""
        results: List[Query] = []
        next_cursor: Optional[str] = None

        while True:
            response = self._client.list_queries(folder_id, next_cursor=next_cursor)
            for resource in _parse_resources(response):
                arn = resource.get("resourceArn", "")
                qid = arn.split("/")[-1] if "/" in arn else arn
                try:
                    detail = self._client.get_query(arn)
                    d = detail.get("data", {})
                    results.append(Query(
                        id=qid,
                        name=d.get("title") or "Untitled",
                        arn=arn,
                        sql=d.get("query") or "",
                        folder_id=folder_id,
                    ))
                except Exception as exc:
                    logger.warning("Could not fetch query %s: %s", arn, exc)
                    results.append(Query(id=qid, name="Untitled", arn=arn, sql="", folder_id=folder_id))

            next_cursor = _parse_next_cursor(response)
            if not next_cursor:
                break

        return results

    @staticmethod
    def stats(root: Folder) -> HierarchyStats:
        """Compute aggregate statistics for a crawled folder tree.

        Args:
            root: The root :class:`~src.models.Folder` returned by :meth:`crawl`.

        Returns:
            A :class:`~src.models.HierarchyStats` instance.
        """
        return HierarchyStats(
            total_folders=root.total_folders,
            total_notebooks=root.total_notebooks,
            total_queries=root.total_queries,
            max_depth=root.max_depth,
        )
