"""Main entry point for argocd-validate-action v3."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .appset import expand_application_set
from .diff import DiffSummary, compare_manifests, write_github_outputs
from .github import commit_state, post_pr_comment, write_step_summary
from .parser import discover_argo_manifests, parse_application
from .renderer import render_application
from .repos import RepoResolver
from .validator import (
    ValidationSummary,
    validate_argo_manifests_dir,
    validate_k8s_manifests,
)

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Supports both direct CLI usage and GitHub Actions input via env vars.
    """
    parser = argparse.ArgumentParser(
        description="Build, validate, and compare ArgoCD manifests",
    )
    parser.add_argument(
        "--manifests-dir",
        default=os.environ.get(
            "INPUT_MANIFESTS_DIR", os.environ.get("INPUT_MANIFESTS-DIR", "manifests")
        ),
        help="Directory to output generated manifests",
    )
    parser.add_argument(
        "--apps-dir",
        default=os.environ.get("INPUT_APPS_DIR", os.environ.get("INPUT_APPS-DIR", "")),
        help="Directory containing ArgoCD application manifests",
    )
    parser.add_argument(
        "--skip-files",
        default=os.environ.get(
            "INPUT_SKIP_FILES", os.environ.get("INPUT_SKIP-FILES", "")
        ),
        help="Comma-separated list of files to skip",
    )
    parser.add_argument(
        "--skip-resources",
        default=os.environ.get(
            "INPUT_SKIP_RESOURCES",
            os.environ.get("INPUT_SKIP-RESOURCES", "CustomResourceDefinition"),
        ),
        help="Comma-separated list of K8s resources to skip during validation",
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get(
            "INPUT_STATE_DIR", os.environ.get("INPUT_STATE-DIR", "")
        ),
        help="Directory for storing manifest state for diff",
    )
    parser.add_argument(
        "--comment-on-pr",
        default=os.environ.get(
            "INPUT_COMMENT_ON_PR", os.environ.get("INPUT_COMMENT-ON-PR", "false")
        ),
        help="Whether to post diff as a PR comment",
    )
    parser.add_argument(
        "--github-token",
        default=os.environ.get(
            "INPUT_GITHUB_TOKEN", os.environ.get("INPUT_GITHUB-TOKEN", "")
        ),
        help="GitHub token for PR comments",
    )
    parser.add_argument(
        "--commit-state",
        default=os.environ.get(
            "INPUT_COMMIT_STATE", os.environ.get("INPUT_COMMIT-STATE", "false")
        ),
        help="Whether to commit state directory after comparison",
    )
    parser.add_argument(
        "--validate-argo",
        default=os.environ.get(
            "INPUT_VALIDATE_ARGO", os.environ.get("INPUT_VALIDATE-ARGO", "true")
        ),
        help="Whether to validate ArgoCD manifest definitions before rendering",
    )
    parser.add_argument(
        "--kube-version",
        default=os.environ.get(
            "INPUT_KUBE_VERSION", os.environ.get("INPUT_KUBE-VERSION", "")
        ),
        help="Kubernetes version for Helm template (e.g. 1.30.0)",
    )
    parser.add_argument(
        "--schema-locations",
        default=os.environ.get(
            "INPUT_SCHEMA_LOCATIONS", os.environ.get("INPUT_SCHEMA-LOCATIONS", "")
        ),
        help="Comma-separated additional kubeconform schema locations",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=os.environ.get("INPUT_VERBOSE", "false").lower() == "true",
        help="Enable verbose output",
    )

    return parser.parse_args(argv)


def _is_truthy(value: str) -> bool:
    """Check if a string value is truthy."""
    return value.lower() in ("true", "1", "yes")


def main(argv: list[str] | None = None) -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    args = parse_args(argv)
    setup_logging(args.verbose)

    # Validate required inputs
    if not args.apps_dir:
        logger.error("--apps-dir is required")
        return 1

    manifests_dir = Path(args.manifests_dir).resolve()
    apps_dir = Path(args.apps_dir).resolve()
    skip_files = [s.strip() for s in args.skip_files.split(",") if s.strip()]
    skip_resources = [s.strip() for s in args.skip_resources.split(",") if s.strip()]
    kube_version = args.kube_version or None
    schema_locations = (
        [s.strip() for s in args.schema_locations.split(",") if s.strip()]
        if args.schema_locations
        else None
    )

    # Set up repo resolver for local and external repositories
    workspace = Path.cwd()
    resolver = RepoResolver(
        workspace=workspace,
        github_token=args.github_token or None,
    )

    # ── Step 1: Validate ArgoCD manifests ──
    if _is_truthy(args.validate_argo):
        logger.info("Validating ArgoCD application definitions...")
        argo_summary = validate_argo_manifests_dir(apps_dir, skip_files)

        if argo_summary.results:
            logger.info(argo_summary.format_text())
            write_step_summary("## ArgoCD Manifest Validation\n")
            write_step_summary(f"```\n{argo_summary.format_text()}\n```\n")

        if not argo_summary.success:
            logger.error("ArgoCD manifest validation failed!")
            return 1
        elif argo_summary.results:
            logger.info("ArgoCD manifest validation passed.")

    # ── Step 2: Discover and parse ArgoCD manifests ──
    logger.info("Discovering ArgoCD applications in %s...", apps_dir)
    applications, application_sets = discover_argo_manifests(apps_dir, skip_files)
    logger.info(
        "Found %d Applications and %d ApplicationSets",
        len(applications),
        len(application_sets),
    )

    # ── Step 3: Expand ApplicationSets ──
    expanded_apps = list(applications)
    for appset in application_sets:
        logger.info("Expanding ApplicationSet: %s", appset.metadata_name)
        generated = expand_application_set(appset, resolver)
        for app_dict in generated:
            app = parse_application(app_dict)
            logger.info("  Generated app from ApplicationSet: %s", app.metadata_name)
            expanded_apps.append(app)

    logger.info("Total applications to render: %d", len(expanded_apps))

    # ── Step 4: Render manifests ──
    manifests_dir.mkdir(parents=True, exist_ok=True)
    errors = []

    for app in expanded_apps:
        try:
            logger.info("Rendering manifests for: %s", app.metadata_name)
            render_application(app, manifests_dir, resolver, kube_version)
        except Exception as e:
            logger.error("Failed to render %s: %s", app.metadata_name, e)
            errors.append((app.metadata_name, str(e)))

    if errors:
        logger.error("Manifest rendering failed for %d application(s):", len(errors))
        for name, err in errors:
            logger.error("  %s: %s", name, err)
        return 1

    logger.info("All manifests generated in: %s", manifests_dir)

    # ── Step 5: Validate rendered K8s manifests ──
    logger.info("Validating rendered Kubernetes manifests...")
    k8s_valid, k8s_output = validate_k8s_manifests(
        manifests_dir, skip_resources, schema_locations
    )

    write_step_summary("## Kubeconform Validation Results\n")
    write_step_summary(f"```\n{k8s_output}\n```\n")

    if not k8s_valid:
        logger.error("Kubeconform validation failed.")
        logger.error(k8s_output)
        return 1
    else:
        logger.info("Kubeconform validation succeeded.")

    # ── Step 6: Compare manifests with state ──
    diff_summary: DiffSummary | None = None
    if args.state_dir:
        state_dir = Path(args.state_dir)
        logger.info("Comparing manifests with state in: %s", state_dir)
        diff_summary = compare_manifests(state_dir, manifests_dir)
        write_github_outputs(diff_summary)

        # Write to step summary
        if diff_summary.status == "changed":
            write_step_summary(diff_summary.comment_body)
        elif diff_summary.status == "initialized":
            write_step_summary("## ArgoCD Manifest State\n")
            write_step_summary(
                "State directory initialized with current manifests. "
                "No previous state to compare against.\n"
            )
        else:
            write_step_summary("## ArgoCD Manifest Changes\n")
            write_step_summary("No changes detected in manifests.\n")

    # ── Step 7: Post PR comment ──
    if (
        diff_summary
        and _is_truthy(args.comment_on_pr)
        and diff_summary.status in ("changed", "unchanged")
    ):
        pr_number = os.environ.get("PR_NUMBER", "")
        if not pr_number:
            # Try to get from github event
            import json

            event_path = os.environ.get("GITHUB_EVENT_PATH", "")
            if event_path:
                try:
                    with open(event_path) as f:
                        event = json.load(f)
                    pr_number = str(event.get("pull_request", {}).get("number", ""))
                except (OSError, json.JSONDecodeError):
                    pass

        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if pr_number and repo:
            comment = diff_summary.comment_body
            if diff_summary.status == "unchanged":
                comment = (
                    "## ArgoCD Manifest Changes\n\n" "No changes detected in manifests."
                )
            post_pr_comment(comment, repo, pr_number, args.github_token)

    # ── Step 8: Commit state ──
    if diff_summary and _is_truthy(args.commit_state) and diff_summary.status:
        commit_state(
            Path(args.state_dir),
            args.github_token,
            diff_summary.status,
        )

    logger.info("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
