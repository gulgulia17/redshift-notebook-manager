"""Data models for the Query Editor V2 folder hierarchy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Query:
    """A saved SQL query resource.

    Attributes:
        id:        Bare UUID extracted from the resource ARN.
        name:      Display name as shown in Query Editor V2.
        arn:       Full resource ARN, e.g.
                   ``arn:aws:sqlworkbench:ap-south-1:123456789012:query/<uuid>``.
        sql:       Raw SQL text.
        folder_id: UUID of the containing folder, or ``None`` for root-level resources.

    Example::

        q = Query(
            id="abc123",
            name="Revenue Check",
            arn="arn:aws:sqlworkbench:ap-south-1:123456789012:query/abc123",
            sql="SELECT * FROM revenue WHERE date = CURRENT_DATE;",
        )
    """

    id: str
    name: str
    arn: str
    sql: str
    folder_id: Optional[str] = None


@dataclass
class Notebook:
    """A Query Editor V2 notebook resource.

    Attributes:
        id:         Bare UUID extracted from the resource ARN.
        name:       Display name as shown in Query Editor V2.
        arn:        Full resource ARN, e.g.
                    ``arn:aws:sqlworkbench:ap-south-1:123456789012:notebook/<uuid>``.
        folder_id:  UUID of the containing folder, or ``None`` for root-level.
        created_at: ISO 8601 timestamp from the API, if available.
        updated_at: ISO 8601 timestamp from the API, if available.

    Example::

        nb = Notebook(
            id="def456",
            name="Revenue Validation",
            arn="arn:aws:sqlworkbench:ap-south-1:123456789012:notebook/def456",
        )
    """

    id: str
    name: str
    arn: str
    folder_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Folder:
    """A folder in the Query Editor V2 hierarchy.

    Folders form a tree: every folder knows its ``parent_folder_id`` and holds
    lists of child :class:`Folder`, :class:`Notebook`, and :class:`Query` objects.

    Attributes:
        id:               Folder UUID.
        name:             Display name.
        parent_folder_id: UUID of the parent folder, or ``None`` for root.
        children:         Immediate child folders.
        notebooks:        Notebooks directly inside this folder.
        queries:          Saved queries directly inside this folder.

    Example::

        root = Folder(id="root-uuid", name="Query Editor V2")
        child = Folder(id="child-uuid", name="Finance", parent_folder_id="root-uuid")
        root.children.append(child)
        print(root.total_notebooks)  # 0
    """

    id: str
    name: str
    parent_folder_id: Optional[str] = None
    children: List["Folder"] = field(default_factory=list)
    notebooks: List[Notebook] = field(default_factory=list)
    queries: List[Query] = field(default_factory=list)

    @property
    def total_folders(self) -> int:
        """Total number of descendant folders (not counting self)."""
        return sum(1 + c.total_folders for c in self.children)

    @property
    def total_notebooks(self) -> int:
        """Total number of notebooks across this folder and all descendants."""
        return len(self.notebooks) + sum(c.total_notebooks for c in self.children)

    @property
    def total_queries(self) -> int:
        """Total number of saved queries across this folder and all descendants."""
        return len(self.queries) + sum(c.total_queries for c in self.children)

    @property
    def max_depth(self) -> int:
        """Maximum nesting depth of child folders below this node."""
        if not self.children:
            return 0
        return 1 + max(c.max_depth for c in self.children)


@dataclass
class HierarchyStats:
    """Aggregate statistics for a crawled folder tree.

    Example::

        stats = HierarchyStats(
            total_folders=5,
            total_notebooks=12,
            total_queries=3,
            max_depth=3,
        )
    """

    total_folders: int
    total_notebooks: int
    total_queries: int
    max_depth: int
