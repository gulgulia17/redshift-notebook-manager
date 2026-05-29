"""
Redshift Query Editor V2 — Folder Hierarchy Crawler

Usage:
    python -m main --region ap-south-1
    python -m main --region ap-south-1 --type query
    python -m main --region ap-south-1 --type all --export ./export
    python -m main --region ap-south-1 --delete-query <arn-or-id>
    python -m main --region ap-south-1 --delete-all-queries
    python -m main --region ap-south-1 --delete-all-queries --folder <folder-id>
    python -m main --region ap-south-1 --import ./export
    python -m main --region ap-south-1 --import ./export --import-root-folder <folder-id>

Credentials from .env or standard boto3 chain.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from src.aws_cli import SqlWorkbenchClient
from src.crawler import FolderCrawler
from src.exporter import NotebookExporter
from src.importer import NotebookImporter
from src.query_exporter import QueryExporter
from src.models import Query
from src.renderer import export_json, render_stats, render_tree

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")


def _to_arn(region: str, id_or_arn: str) -> str:
    """Accept either a full ARN or a bare UUID and return the full ARN."""
    if id_or_arn.startswith("arn:"):
        return id_or_arn
    return f"arn:aws:sqlworkbench:{region}:{ACCOUNT_ID}:query/{id_or_arn}"


def _collect_all_queries(root) -> list[Query]:
    """Flatten all queries from the full tree."""
    results = list(root.queries)
    for child in root.children:
        results.extend(_collect_all_queries(child))
    return results


def cmd_delete_query(client: SqlWorkbenchClient, region: str, id_or_arn: str) -> None:
    """Delete a single query by ARN or bare UUID."""
    arn = _to_arn(region, id_or_arn)
    logger.info("Deleting query: %s", arn)
    client.delete_query(arn)
    logger.info("Deleted.")


def cmd_delete_all_queries(client: SqlWorkbenchClient, crawler: FolderCrawler, folder_id: str | None) -> None:
    """
    Crawl queries (optionally scoped to a folder), show what will be deleted,
    ask for confirmation, then delete all.
    """
    root = crawler.crawl(root_folder_id=folder_id, resource_type="query")
    all_queries = _collect_all_queries(root)

    if not all_queries:
        logger.info("No queries found — nothing to delete.")
        return

    print(f"\nFound {len(all_queries)} queries to delete:\n")
    for q in all_queries:
        print(f"  🔷 {q.name}  ({q.id})")

    print(f"\n⚠️  This will permanently delete all {len(all_queries)} queries.")
    confirm = input("Type 'yes' to confirm: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    deleted = 0
    failed = 0
    for q in all_queries:
        try:
            client.delete_query(q.arn)
            logger.info("Deleted: %s", q.name)
            deleted += 1
        except Exception as exc:
            logger.error("Failed to delete %s: %s", q.name, exc)
            failed += 1

    print(f"\nDone. Deleted: {deleted}  Failed: {failed}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawl, export, or delete Redshift Query Editor V2 resources",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"),
        help="AWS region (default: ap-south-1 or $AWS_DEFAULT_REGION)",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("AWS_PROFILE"),
        help="AWS credentials profile",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    # ---- crawl / export args ----
    parser.add_argument(
        "--type",
        choices=["notebook", "query", "all"],
        default="notebook",
        help="Resource type to crawl/export (default: notebook)",
    )
    parser.add_argument(
        "--output",
        default="storage/tree.json",
        help="JSON hierarchy export path (default: storage/tree.json)",
    )
    parser.add_argument(
        "--export",
        metavar="DIR",
        default="storage/exports",
        help="Export resources as files into this directory (default: storage/exports)",
    )
    parser.add_argument(
        "--root-folder",
        default=None,
        metavar="FOLDER_ID",
        help="Override root folder ID (bypass GET /user rootFolders discovery)",
    )

    # ---- import args ----
    parser.add_argument(
        "--import",
        metavar="DIR",
        dest="import_dir",
        default=None,
        help="Import notebooks from a local export directory into Query Editor V2",
    )
    parser.add_argument(
        "--import-root-folder",
        metavar="FOLDER_ID",
        default=None,
        help="Target root folder ID for --import (auto-discovered when omitted)",
    )
    parser.add_argument(
        "--import-base",
        metavar="DIR",
        default="storage/exports",
        help="Export root used to compute relative folder path (default: storage/exports)",
    )

    # ---- delete args ----
    delete_group = parser.add_mutually_exclusive_group()
    delete_group.add_argument(
        "--delete-query",
        metavar="ARN_OR_ID",
        help="Delete a single saved query by ARN or bare UUID",
    )
    delete_group.add_argument(
        "--delete-all-queries",
        action="store_true",
        help="Delete ALL saved queries (prompts for confirmation)",
    )
    parser.add_argument(
        "--folder",
        metavar="FOLDER_ID",
        default=None,
        help="Scope --delete-all-queries to a specific folder ID",
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    if args.profile:
        os.environ["AWS_PROFILE"] = args.profile

    logger.info("Region: %s", args.region)

    client = SqlWorkbenchClient(region=args.region)

    # ---- import command ----
    if args.import_dir:
        NotebookImporter(client).import_tree(
            source_dir=args.import_dir,
            root_folder_id=args.import_root_folder,
            base_dir=args.import_base,
        )
        return

    # ---- delete commands ----
    if args.delete_query:
        cmd_delete_query(client, args.region, args.delete_query)
        return

    if args.delete_all_queries:
        crawler = FolderCrawler(client)
        cmd_delete_all_queries(client, crawler, folder_id=args.folder)
        return

    # ---- crawl / export ----
    crawler = FolderCrawler(client)
    types_to_crawl = ["notebook", "query"] if args.type == "all" else [args.type]

    for resource_type in types_to_crawl:
        logger.info("Crawling %ss...", resource_type)
        try:
            root = crawler.crawl(root_folder_id=args.root_folder, resource_type=resource_type)
        except Exception as exc:
            logger.error("Crawl failed for %s: %s", resource_type, exc)
            sys.exit(1)

        stats = FolderCrawler.stats(root)
        print("\n" + render_tree(root))
        print(render_stats(stats))

        out_file = args.output.replace(".json", f"_{resource_type}.json") if args.type == "all" else args.output
        export_json(root, stats, out_file)
        logger.info("Hierarchy saved to %s", out_file)

        if args.export:
            export_dir = f"{args.export}/{resource_type}s" if args.type == "all" else args.export
            if resource_type == "notebook":
                NotebookExporter(client, output_dir=export_dir).export_tree(root)
                logger.info("Notebooks exported to %s/", export_dir)
            else:
                QueryExporter(output_dir=export_dir).export_tree(root)
                logger.info("Queries exported to %s/", export_dir)


if __name__ == "__main__":
    main()
