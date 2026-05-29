"""Notebook importer for Query Editor V2.

Recreates a local export directory as a folder hierarchy in Query Editor V2
and imports each ``.ipynb`` file as a notebook resource.

Exported files produced by :class:`~src.exporter.NotebookExporter` are wrapped
under an ``"exportedNotebook"`` key.  The importer unwraps this automatically
before calling the import API.

Import flow::

    storage/exports/
        Analytics/
            Q1/
                Revenue Validation.ipynb
                Cost Analysis.ipynb
                ↓
    PUT /folder  Analytics  (root-level, parentFolderId omitted)
    PUT /folder  Q1         (under Analytics)
    POST /notebook/import/v1  Revenue Validation  (into Q1)
    POST /notebook/import/v1  Cost Analysis       (into Q1)

Existing folders are reused rather than duplicated — the importer checks
``GET /v2/file?actionName=folders-only`` before each ``PUT /folder``.

Example::

    from src.aws_cli import SqlWorkbenchClient
    from src.importer import NotebookImporter

    client   = SqlWorkbenchClient(region="ap-south-1")
    importer = NotebookImporter(client)

    # Import a subtree, rebuilding the full path from the export root
    importer.import_tree(
        source_dir="storage/exports/Analytics/Q1",
        base_dir="storage/exports",
    )

    # Import everything
    importer.import_tree(source_dir="storage/exports")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

from .aws_cli import SqlWorkbenchClient

logger = logging.getLogger(__name__)


def _notebook_title(ipynb: dict, filename: str) -> str:
    """Derive a display title from the notebook definition or filename.

    Checks ``metadata.title`` first, then falls back to the stem of
    *filename* (i.e. the name without the ``.ipynb`` extension).

    Args:
        ipynb:    Parsed ``.ipynb`` dict.
        filename: Source file name, used as a fallback.

    Returns:
        Non-empty title string.
    """
    title = ipynb.get("metadata", {}).get("title", "")
    return title or Path(filename).stem


def _ensure_notebook_metadata(ipynb: dict, title: str) -> dict:
    """Inject the metadata fields required by ``POST /notebook/import/v1``.

    Query Editor V2 requires a specific ``kernelspec`` and ``language_info``
    shape.  This function adds the missing fields without overwriting existing
    values.

    Args:
        ipynb: Parsed ``.ipynb`` dict.
        title: Display title to set in ``metadata.title``.

    Returns:
        A new dict with the required metadata fields filled in.
    """
    meta = dict(ipynb.get("metadata", {}))

    if "kernelspec" not in meta:
        meta["kernelspec"] = {
            "display_name": "Redshift",
            "language": "postgresql",
            "name": "Redshift",
        }
    if "language_info" not in meta:
        meta["language_info"] = {
            "file_extension": ".sql",
            "name": "Redshift",
        }
    if "version" not in meta:
        meta["version"] = 1

    meta["title"] = title

    result = dict(ipynb)
    result["metadata"] = meta
    result.setdefault("nbformat", 4)
    result.setdefault("nbformat_minor", 0)
    result.setdefault("cells", [])
    return result


class NotebookImporter:
    """Imports a local export directory into Redshift Query Editor V2.

    The importer recreates the folder hierarchy on the remote account and
    uploads each ``.ipynb`` file as a notebook resource.  Re-running the
    importer on the same directory is safe — existing folders are reused
    and no duplicates are created.

    Args:
        client: Authenticated :class:`~src.aws_cli.SqlWorkbenchClient`.

    Example::

        importer = NotebookImporter(SqlWorkbenchClient(region="ap-south-1"))

        importer.import_tree(
            source_dir="storage/exports/Analytics/Q1",
            base_dir="storage/exports",
        )
    """

    def __init__(self, client: SqlWorkbenchClient) -> None:
        self._client = client
        self._imported = 0
        self._failed = 0
        self._folders_created = 0
        self._root_folder_id: Optional[str] = None

    def import_tree(
        self,
        source_dir: str,
        root_folder_id: Optional[str] = None,
        base_dir: Optional[str] = None,
    ) -> None:
        """Import all notebooks from *source_dir* into Query Editor V2.

        Args:
            source_dir:     Local directory to import.  May be the export root
                            or any subdirectory within it.
            root_folder_id: Target root folder UUID.  When ``None``, the value
                            is auto-discovered via ``GET /user``.
            base_dir:       Export root directory.  When provided, all
                            intermediate folders between *base_dir* and
                            *source_dir* are recreated on the remote account
                            before importing the leaf directory.

                            Example — ``base_dir="storage/exports"``,
                            ``source_dir="storage/exports/Analytics/Q1"``
                            creates *Analytics* at the account root, then *Q1*
                            inside it.

        Raises:
            FileNotFoundError: If *source_dir* does not exist.
            ValueError:        If *source_dir* is not inside *base_dir*, or if
                               the root folder cannot be auto-discovered.
            RuntimeError:      If the caller's identity cannot be determined.
        """
        source = Path(source_dir).resolve()
        if not source.exists():
            raise FileNotFoundError(f"Source directory not found: {source}")

        owner_user_id = self._client.get_owner_user_id()
        if not owner_user_id:
            raise RuntimeError(
                "Cannot determine owner user ID — GET /user returned no 'data.id'.\n"
                "Ensure your credentials match the target Query Editor V2 account."
            )

        if root_folder_id is None:
            user_info = self._client.get_user_info()
            root_folder_id = user_info.get("data", {}).get("rootFolders", {}).get("notebook")
            if not root_folder_id:
                raise ValueError(
                    "Could not discover root folder from GET /user.\n"
                    "Pass --import-root-folder <folder-id> to specify it explicitly."
                )
            logger.info("Target root folder (auto-discovered): %s", root_folder_id)
        else:
            logger.info("Target root folder (provided): %s", root_folder_id)

        self._imported = 0
        self._failed = 0
        self._folders_created = 0
        self._root_folder_id = root_folder_id

        if base_dir:
            base = Path(base_dir).resolve()
            try:
                rel = source.relative_to(base)
            except ValueError:
                raise ValueError(
                    f"--import path '{source}' is not inside --import-base '{base}'"
                )
            # Recreate every folder between base and source, then import the leaf.
            intermediate_parts = list(rel.parts[:-1])
            current_folder_id: Optional[str] = None
            for part in intermediate_parts:
                current_folder_id = self._ensure_folder(part, current_folder_id)
                if current_folder_id is None:
                    return
            self._import_directory(source, current_folder_id, owner_user_id)
        else:
            self._import_directory(source, root_folder_id, owner_user_id)

        logger.info(
            "Import complete — folders created: %d, notebooks imported: %d, failed: %d",
            self._folders_created, self._imported, self._failed,
        )
        print(
            f"\nImport complete.\n"
            f"  Folders created   : {self._folders_created}\n"
            f"  Notebooks imported: {self._imported}\n"
            f"  Failed            : {self._failed}\n"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_existing_folder(self, name: str, list_parent_id: str) -> Optional[str]:
        """Return the folder ID if *name* already exists under *list_parent_id*.

        Pages through ``GET /v2/file?actionName=folders-only`` until a match
        is found or all pages are exhausted.

        Args:
            name:           Folder display name to search for.
            list_parent_id: Parent folder UUID to search within.

        Returns:
            Existing folder UUID, or ``None`` if not found.
        """
        try:
            next_token: Optional[str] = None
            while True:
                resp = self._client.list_folders(list_parent_id, resource_type="notebook", next_token=next_token)
                for item in resp.get("data", {}).get("items", []):
                    if (item.get("title") or item.get("name", "")) == name:
                        return item.get("id")
                next_token = resp.get("data", {}).get("nextToken")
                if not next_token:
                    break
        except Exception as exc:
            logger.debug("Could not list folders under %s: %s", list_parent_id, exc)
        return None

    def _ensure_folder(self, name: str, parent_folder_id: Optional[str] = None) -> Optional[str]:
        """Return the UUID of *name*, creating it if it does not yet exist.

        Args:
            name:             Folder display name.
            parent_folder_id: Parent folder UUID.  Pass ``None`` for root-level
                              folders (omits ``parentFolderId`` from the request).

        Returns:
            Folder UUID, or ``None`` if creation failed.
        """
        list_parent_id = parent_folder_id or self._root_folder_id
        existing = self._find_existing_folder(name, list_parent_id)
        if existing:
            logger.info("  ↩ Folder already exists: %s (%s)", name, existing)
            return existing

        logger.info("Creating folder '%s' under %s", name, parent_folder_id or "root")
        try:
            resp = self._client.create_folder(name=name, parent_folder_id=parent_folder_id)
            folder_id = resp.get("data", {}).get("id")
            if not folder_id:
                raise ValueError(f"No folder ID in response: {resp}")
            self._folders_created += 1
            logger.info("  ✓ Folder created: %s (%s)", name, folder_id)
            return folder_id
        except Exception as exc:
            logger.error("  ✗ Failed to create folder '%s': %s", name, exc)
            return None

    def _import_directory(
        self,
        directory: Path,
        parent_folder_id: Optional[str],
        owner_user_id: str,
    ) -> None:
        """Create a folder for *directory* and import all notebooks inside it.

        Recurses into sub-directories after processing notebooks in the current
        directory.

        Args:
            directory:        Local directory to process.
            parent_folder_id: UUID of the parent folder on the remote account.
            owner_user_id:    IAM user ID to attach as the resource owner tag.
        """
        folder_id = self._ensure_folder(directory.name, parent_folder_id)
        if folder_id is None:
            return

        for nb_file in sorted(directory.glob("*.ipynb")):
            self._import_notebook_file(nb_file, folder_id, owner_user_id)

        for sub in sorted(p for p in directory.iterdir() if p.is_dir()):
            self._import_directory(sub, folder_id, owner_user_id)

    def _import_notebook_file(
        self,
        nb_file: Path,
        folder_id: str,
        owner_user_id: str,
    ) -> None:
        """Read *nb_file* from disk and upload it via ``POST /notebook/import/v1``.

        The exporter wraps notebook content under ``"exportedNotebook"``.  This
        method unwraps it before sending to the import API.

        Args:
            nb_file:       Path to the local ``.ipynb`` file.
            folder_id:     Destination folder UUID.
            owner_user_id: IAM user ID for the resource owner tag.
        """
        try:
            raw: Dict = json.loads(nb_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("  ✗ Cannot read %s: %s", nb_file.name, exc)
            self._failed += 1
            return

        # The exporter wraps the notebook body under "exportedNotebook".
        # Shape: {"title": "...", "exportedNotebook": { <actual ipynb> }}
        if "exportedNotebook" in raw:
            nb_body = raw["exportedNotebook"]
            title = raw.get("title") or _notebook_title(nb_body, nb_file.name)
        else:
            nb_body = raw
            title = _notebook_title(raw, nb_file.name)

        nb_def = _ensure_notebook_metadata(nb_body, title)

        logger.info("Importing '%s' into folder %s", title, folder_id)
        try:
            resp = self._client.import_notebook(
                notebook_definition=nb_def,
                folder_id=folder_id,
                owner_user_id=owner_user_id,
            )
            created_arn = resp.get("data", {}).get("id", "")
            self._imported += 1
            logger.info("  ✓ %s → %s", nb_file.name, created_arn)
        except Exception as exc:
            logger.error("  ✗ Failed to import '%s': %s", nb_file.name, exc)
            self._failed += 1
