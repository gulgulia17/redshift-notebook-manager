"""SigV4-authenticated HTTP client for the ``sqlworkbench`` service.

Query Editor V2 is backed by a private REST API at::

    https://api.sqlworkbench.<region>.amazonaws.com

The API is not exposed through the public boto3 SDK, so requests are signed
manually using ``requests-aws4auth``.  Credentials are sourced from the
standard boto3 chain — environment variables, ``~/.aws/credentials``, instance
profile, etc.  When working with browser-session credentials, load them from
a ``.env`` file before constructing the client (see :mod:`main`).

Example::

    from src.aws_cli import SqlWorkbenchClient

    client = SqlWorkbenchClient(region="ap-south-1")
    user  = client.get_user_info()
    print(user["data"]["rootFolders"]["notebook"])
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import boto3
import requests
from requests_aws4auth import AWS4Auth

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_RETRY_BACKOFF_BASE = 1.5
# 403 is intentionally excluded — it indicates an auth/permission problem,
# not a transient server error, and retrying would just delay the failure.
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class SqlWorkbenchClient:
    """Authenticated HTTP client for the ``sqlworkbench`` REST API.

    All requests are signed with AWS Signature Version 4 using the
    ``sqlworkbench`` service name.  Transient server errors (5xx, 429) are
    retried with exponential back-off; authentication errors (4xx) are raised
    immediately.

    Args:
        region: AWS region, e.g. ``"ap-south-1"``.

    Example::

        client = SqlWorkbenchClient(region="ap-south-1")

        # Discover the root notebook folder
        user_info  = client.get_user_info()
        root_id    = user_info["data"]["rootFolders"]["notebook"]

        # List child folders
        folders = client.list_folders(root_id)

        # Export a notebook to .ipynb format
        nb = client.export_notebook("arn:aws:sqlworkbench:ap-south-1:123:notebook/uuid")
    """

    SERVICE = "sqlworkbench"

    def __init__(self, region: str) -> None:
        self.region = region
        self.base_url = f"https://api.sqlworkbench.{region}.amazonaws.com"
        self._session = requests.Session()
        self._auth = self._build_auth()
        self._owner_user_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_auth(self) -> AWS4Auth:
        """Build SigV4 auth from the current boto3 credential chain."""
        boto_session = boto3.Session(region_name=self.region)
        creds = boto_session.get_credentials().get_frozen_credentials()
        logger.debug("Using access key: %s...", creds.access_key[:8])
        return AWS4Auth(
            creds.access_key,
            creds.secret_key,
            self.region,
            self.SERVICE,
            session_token=creds.token,
        )

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, str]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a signed HTTP request with retry logic.

        Args:
            method: HTTP verb (``"GET"``, ``"POST"``, ``"PUT"``, ``"DELETE"``).
            path:   Path relative to the service base URL, e.g. ``"/user"``.
            params: URL query parameters.
            body:   JSON request body.  Omit for requests that have no body;
                    sending ``Content-Type: application/json`` on a GET with no
                    body causes a SigV4 mismatch.

        Returns:
            Parsed JSON response as a ``dict``.

        Raises:
            requests.HTTPError: On non-retryable HTTP errors (4xx).
            RuntimeError:       When all retry attempts are exhausted.
        """
        url = self.base_url + path
        headers = {"Content-Type": "application/json"} if body is not None else {}

        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=body,
                    headers=headers,
                    auth=self._auth,
                    timeout=30,
                )

                if resp.status_code in _RETRY_STATUS_CODES:
                    wait = _RETRY_BACKOFF_BASE ** attempt
                    logger.warning(
                        "HTTP %s for %s %s — retrying in %.1fs (attempt %d/%d)",
                        resp.status_code, method, path, wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue

                if not resp.ok:
                    logger.debug("HTTP %s response body: %s", resp.status_code, resp.text[:500])

                resp.raise_for_status()
                return resp.json()

            except requests.exceptions.RequestException as exc:
                if attempt == _MAX_RETRIES - 1:
                    logger.error("Request failed after %d attempts: %s", _MAX_RETRIES, exc)
                    raise
                wait = _RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "Request error for %s %s: %s — retrying in %.1fs",
                    method, path, exc, wait,
                )
                time.sleep(wait)

        raise RuntimeError(f"Exhausted retries for {method} {path}")

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def get_user_info(self) -> Dict[str, Any]:
        """Fetch the current user's identity and root folder IDs.

        Endpoint: ``GET /user``

        The response includes ``data.rootFolders.notebook`` (the root notebook
        folder UUID) and ``data.id`` (the IAM user ID used as a tag filter
        when listing resources).

        Returns:
            Full API response, e.g.::

                {
                    "data": {
                        "id": "AIDA...",
                        "rootFolders": {
                            "notebook": "<folder-uuid>",
                            "query": "..."
                        }
                    }
                }
        """
        logger.debug("GET /user")
        return self._request("GET", "/user")

    def get_owner_user_id(self) -> Optional[str]:
        """Return the IAM user ID for the current credentials.

        The value is fetched once from ``GET /user → data.id`` and cached.
        It is used as a tag filter in ``POST /tagged-resource`` to scope
        results to the calling identity.

        Returns:
            IAM user ID string, or ``None`` if it cannot be determined.
        """
        if self._owner_user_id is None:
            try:
                resp = self.get_user_info()
                self._owner_user_id = resp.get("data", {}).get("id") or ""
                if self._owner_user_id:
                    logger.info("Owner user ID: %s", self._owner_user_id)
                else:
                    logger.warning("GET /user returned no 'id' — owner filter will be omitted")
            except Exception as exc:
                logger.warning("Could not determine ownerUserId: %s", exc)
                self._owner_user_id = ""
        return self._owner_user_id or None

    # ------------------------------------------------------------------
    # Folder listing
    # ------------------------------------------------------------------

    def list_folders(
        self,
        folder_id: str,
        resource_type: str = "notebook",
        next_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List immediate child folders of a given folder.

        Endpoint: ``GET /v2/file?actionName=folders-only&targetId=<id>&type=<type>``

        Args:
            folder_id:     UUID of the parent folder.
            resource_type: ``"notebook"`` or ``"query"``.
            next_token:    Pagination token from a previous response.

        Returns:
            Response containing ``data.items`` (list of folder objects) and
            an optional ``data.nextToken`` for subsequent pages.
        """
        params: Dict[str, str] = {
            "actionName": "folders-only",
            "targetId": folder_id,
            "type": resource_type,
        }
        if next_token:
            params["nextToken"] = next_token
        logger.debug("list_folders targetId=%s type=%s token=%s", folder_id, resource_type, next_token)
        return self._request("GET", "/v2/file", params=params)

    # ------------------------------------------------------------------
    # Resource listing
    # ------------------------------------------------------------------

    def list_resources(
        self,
        folder_id: str,
        resource_type: str = "notebook",
        next_cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List notebooks or queries in a folder via tag-based lookup.

        Endpoint: ``POST /tagged-resource``

        Filters by ``aws:sqlworkbench:resource-folder`` (folder ID) and,
        when available, by ``sqlworkbench-resource-owner`` (IAM user ID).

        Args:
            folder_id:     UUID of the folder to query.
            resource_type: ``"notebook"`` or ``"query"``.
            next_cursor:   Pagination cursor from a previous response.

        Returns:
            Response containing ``data.resources`` (list of resource objects
            with ``resourceArn`` and ``tags``) and ``data.nextCursor``.
        """
        owner = self.get_owner_user_id()
        tag_filters: List[Dict[str, Any]] = []
        if owner:
            tag_filters.append({"key": "sqlworkbench-resource-owner", "values": [owner]})
        tag_filters.append({"key": "aws:sqlworkbench:resource-folder", "values": [folder_id]})

        body: Dict[str, Any] = {
            "resourceTypeFilters": [f"sqlworkbench:{resource_type}"],
            "tagFilters": tag_filters,
            "limit": 50,
        }
        if next_cursor:
            body["nextCursor"] = next_cursor

        logger.debug("list_resources folder=%s type=%s cursor=%s", folder_id, resource_type, next_cursor)
        return self._request("POST", "/tagged-resource", body=body)

    def list_notebooks(self, folder_id: str, next_cursor: Optional[str] = None) -> Dict[str, Any]:
        """Convenience wrapper around :meth:`list_resources` for notebooks."""
        return self.list_resources(folder_id, resource_type="notebook", next_cursor=next_cursor)

    def list_queries(self, folder_id: str, next_cursor: Optional[str] = None) -> Dict[str, Any]:
        """Convenience wrapper around :meth:`list_resources` for saved queries."""
        return self.list_resources(folder_id, resource_type="query", next_cursor=next_cursor)

    # ------------------------------------------------------------------
    # Notebook operations
    # ------------------------------------------------------------------

    def get_notebook(self, notebook_arn: str) -> Dict[str, Any]:
        """Fetch notebook metadata (title, folderId, timestamps).

        Endpoint: ``GET /notebook/<url-encoded-arn>``

        Args:
            notebook_arn: Full notebook ARN.

        Returns:
            Response containing ``data.name``, ``data.folderId``, etc.
        """
        encoded = quote(notebook_arn, safe="")
        logger.debug("get_notebook arn=%s", notebook_arn)
        return self._request("GET", f"/notebook/{encoded}")

    def export_notebook(self, notebook_arn: str) -> Dict[str, Any]:
        """Export a notebook as an ``.ipynb``-compatible payload.

        Endpoint: ``GET /notebook/<url-encoded-arn>/export``

        The response wraps the notebook definition under
        ``data.notebookDefinition`` (either as a JSON string or a dict —
        :func:`src.exporter._extract_ipynb` normalises both).

        Args:
            notebook_arn: Full notebook ARN.

        Returns:
            Raw export response from the API.
        """
        encoded = quote(notebook_arn, safe="")
        logger.debug("export_notebook arn=%s", notebook_arn)
        return self._request("GET", f"/notebook/{encoded}/export")

    def import_notebook(
        self,
        notebook_definition: Dict[str, Any],
        folder_id: str,
        owner_user_id: str,
    ) -> Dict[str, Any]:
        """Import a notebook from an ``.ipynb``-compatible definition.

        Endpoint: ``POST /notebook/import/v1``

        Args:
            notebook_definition: Full ``.ipynb`` dict (must include ``metadata``,
                                 ``cells``, ``nbformat``).
            folder_id:           UUID of the destination folder.
            owner_user_id:       IAM user ID to set as the resource owner tag.
                                 Obtain via :meth:`get_owner_user_id`.

        Returns:
            Response containing ``data.id`` (new notebook ARN) and
            ``data.folderId``.

        Example::

            arn = client.import_notebook(
                notebook_definition=nb_dict,
                folder_id="folder-uuid",
                owner_user_id="AIDA...",
            )["data"]["id"]
        """
        body: Dict[str, Any] = {
            "notebookDefinition": notebook_definition,
            "folderId": folder_id,
            "tags": {"sqlworkbench-resource-owner": owner_user_id},
        }
        logger.debug(
            "import_notebook folder=%s title=%s",
            folder_id,
            notebook_definition.get("metadata", {}).get("title"),
        )
        return self._request("POST", "/notebook/import/v1", body=body)

    # ------------------------------------------------------------------
    # Folder operations
    # ------------------------------------------------------------------

    def create_folder(
        self,
        name: str,
        parent_folder_id: Optional[str] = None,
        folder_type: str = "2",
    ) -> Dict[str, Any]:
        """Create a new folder.

        Endpoint: ``PUT /folder``

        When ``parent_folder_id`` is ``None``, the ``parentFolderId`` field is
        omitted from the request body entirely.  Passing the QEV2 virtual root
        folder ID as the parent causes a 403; omitting it places the folder at
        the account root level.

        Args:
            name:             Display name for the new folder.
            parent_folder_id: UUID of the parent folder.  Pass ``None`` to
                              create a top-level folder.
            folder_type:      Folder type string (``"2"`` for standard folders).

        Returns:
            Response containing ``data.id`` (new folder UUID) and
            ``data.parentFolderId``.
        """
        body: Dict[str, Any] = {"name": name, "type": folder_type}
        if parent_folder_id:
            body["parentFolderId"] = parent_folder_id
        logger.debug("create_folder name=%s parentFolderId=%s", name, parent_folder_id)
        return self._request("PUT", "/folder", body=body)

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    def get_query(self, query_arn: str) -> Dict[str, Any]:
        """Fetch a saved query's metadata and SQL text.

        Endpoint: ``GET /query-saved/<url-encoded-arn>``

        Args:
            query_arn: Full query ARN.

        Returns:
            Response containing ``data.title``, ``data.query`` (SQL text), etc.
        """
        encoded = quote(query_arn, safe="")
        logger.debug("get_query arn=%s", query_arn)
        return self._request("GET", f"/query-saved/{encoded}")

    def delete_query(self, query_arn: str) -> None:
        """Permanently delete a saved query.

        Endpoint: ``DELETE /query-saved/<url-encoded-arn>``

        Args:
            query_arn: Full query ARN or bare UUID.

        Raises:
            requests.HTTPError: If the query does not exist or access is denied.
        """
        encoded = quote(query_arn, safe="")
        logger.debug("delete_query arn=%s", query_arn)
        self._request("DELETE", f"/query-saved/{encoded}")
