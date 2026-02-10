"""ApplicationSet generator expansion and template rendering."""

from __future__ import annotations

import itertools
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from .models import ApplicationSet, substitute_params_deep
from .parser import parse_application
from .repos import RepoResolver

logger = logging.getLogger(__name__)


def expand_application_set(
    appset: ApplicationSet,
    resolver: RepoResolver,
) -> list[dict[str, Any]]:
    """Expand an ApplicationSet into a list of Application-like dicts.

    Processes all generators and applies template substitution to produce
    concrete Application specs.

    Args:
        appset: The ApplicationSet to expand
        resolver: Repository resolver for git directory/file generators

    Returns:
        List of dicts, each representing a rendered Application spec
    """
    all_params = []
    for generator in appset.generators:
        params = _expand_generator(generator, resolver, appset.go_template)
        all_params.extend(params)

    applications = []
    for params in all_params:
        app_dict = substitute_params_deep(appset.template, params)
        # Wrap in Application-like structure
        full_dict = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": app_dict.get("metadata", {}),
            "spec": app_dict.get("spec", app_dict),
        }
        # If template already has the right structure, use it directly
        if "metadata" not in app_dict and "spec" not in app_dict:
            full_dict = {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                **app_dict,
            }
        applications.append(full_dict)

    return applications


def _expand_generator(
    generator: dict[str, Any],
    resolver: RepoResolver,
    go_template: bool = False,
) -> list[dict[str, Any]]:
    """Expand a single generator into parameter sets.

    Args:
        generator: Raw generator dict from the ApplicationSet spec
        resolver: Repository resolver for filesystem resolution
        go_template: Whether Go template syntax is used

    Returns:
        List of parameter dicts
    """
    params: list[dict[str, Any]] = []

    # Extra values to merge into every parameter set
    extra_values = generator.get("values", {})

    # Selector for post-filtering
    selector = generator.get("selector")

    if "list" in generator:
        params = _expand_list_generator(generator["list"])
    elif "git" in generator:
        params = _expand_git_generator(generator["git"], resolver)
    elif "clusters" in generator:
        params = _expand_clusters_generator(generator["clusters"])
    elif "matrix" in generator:
        params = _expand_matrix_generator(generator["matrix"], resolver, go_template)
    elif "merge" in generator:
        params = _expand_merge_generator(generator["merge"], resolver, go_template)
    else:
        logger.warning("Unsupported generator type: %s", list(generator.keys()))
        return []

    # Merge extra values
    if extra_values and isinstance(extra_values, dict):
        for p in params:
            for k, v in extra_values.items():
                p[f"values.{k}"] = v

    # Apply selector filter
    if selector:
        params = _apply_selector(params, selector)

    return params


def _expand_list_generator(
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expand a list generator."""
    elements = config.get("elements", [])
    params = []
    for element in elements:
        if isinstance(element, dict):
            # Flatten nested dicts with dot notation
            flat = _flatten_dict(element)
            params.append(flat)
    return params


def _expand_git_generator(
    config: dict[str, Any],
    resolver: RepoResolver,
) -> list[dict[str, Any]]:
    """Expand a git generator (directories or files)."""
    params = []
    path_param_prefix = config.get("pathParamPrefix", "")
    repo_url = config.get("repoURL", "")
    revision = config.get("revision", "") or config.get("targetRevision", "")
    base_path = resolver.resolve(repo_url, revision or None)

    if "directories" in config:
        params = _expand_git_directory_generator(config, base_path, path_param_prefix)
    elif "files" in config:
        params = _expand_git_file_generator(config, base_path, path_param_prefix)

    return params


def _expand_git_directory_generator(
    config: dict[str, Any],
    base_path: Path,
    path_param_prefix: str = "",
) -> list[dict[str, Any]]:
    """Expand a git directory generator by scanning the filesystem."""
    directories = config.get("directories", [])
    params = []

    include_patterns = []
    exclude_patterns = []

    for dir_entry in directories:
        path_pattern = dir_entry.get("path", "")
        if dir_entry.get("exclude", False):
            exclude_patterns.append(path_pattern)
        else:
            include_patterns.append(path_pattern)

    # Find matching directories
    matched_dirs = set()
    for pattern in include_patterns:
        matched = _find_matching_directories(pattern, base_path)
        matched_dirs.update(matched)

    # Remove excluded directories
    for pattern in exclude_patterns:
        excluded = _find_matching_directories(pattern, base_path)
        matched_dirs -= excluded

    # Generate parameters for each matched directory
    prefix = f"{path_param_prefix}." if path_param_prefix else ""
    for dir_path in sorted(matched_dirs):
        path_str = str(dir_path)
        basename = dir_path.name
        basename_normalized = _normalize_name(basename)
        segments = path_str.split("/")

        p: dict[str, Any] = {
            f"{prefix}path": path_str,
            f"{prefix}path.path": path_str,
            f"{prefix}path.basename": basename,
            f"{prefix}path.basenameNormalized": basename_normalized,
            # Legacy compatibility
            "path": path_str,
            "path.path": path_str,
            "path.basename": basename,
            "path.basenameNormalized": basename_normalized,
        }

        # Add path segments
        for i, segment in enumerate(segments):
            p[f"{prefix}path.segments.{i}"] = segment
            p[f"path.segments.{i}"] = segment

        params.append(p)

    return params


def _expand_git_file_generator(
    config: dict[str, Any],
    base_path: Path,
    path_param_prefix: str = "",
) -> list[dict[str, Any]]:
    """Expand a git file generator by reading matching files."""
    files_config = config.get("files", [])
    params = []

    for file_entry in files_config:
        path_pattern = file_entry.get("path", "")
        if file_entry.get("exclude", False):
            continue

        matched_files = _find_matching_files(path_pattern, base_path)
        for file_path in sorted(matched_files):
            try:
                content = file_path.read_text()
                data = yaml.safe_load(content)
                if not isinstance(data, dict):
                    data = json.loads(content)
            except (yaml.YAMLError, json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to parse file %s: %s", file_path, e)
                continue

            if not isinstance(data, dict):
                continue

            # Flatten the file contents
            flat = _flatten_dict(data)

            # Add path parameters
            rel_path = file_path.relative_to(base_path)
            path_str = str(rel_path)
            prefix = f"{path_param_prefix}." if path_param_prefix else ""
            flat.update(
                {
                    f"{prefix}path": path_str,
                    f"{prefix}path.path": str(rel_path.parent),
                    f"{prefix}path.basename": rel_path.parent.name,
                    f"{prefix}path.basenameNormalized": _normalize_name(
                        rel_path.parent.name
                    ),
                    f"{prefix}path.filename": rel_path.name,
                    f"{prefix}path.filenameNormalized": _normalize_name(rel_path.name),
                    "path": path_str,
                    "path.path": str(rel_path.parent),
                    "path.basename": rel_path.parent.name,
                    "path.basenameNormalized": _normalize_name(rel_path.parent.name),
                    "path.filename": rel_path.name,
                    "path.filenameNormalized": _normalize_name(rel_path.name),
                }
            )

            params.append(flat)

    return params


def _expand_clusters_generator(
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expand a clusters generator.

    Since we don't have access to ArgoCD cluster API, we generate a single
    'in-cluster' entry as a local simulation.
    """
    logger.warning(
        "Clusters generator is not fully supported in local mode. "
        "Generating in-cluster entry only."
    )
    return [
        {
            "name": "in-cluster",
            "server": "https://kubernetes.default.svc",
        }
    ]


def _expand_matrix_generator(
    config: dict[str, Any],
    resolver: RepoResolver,
    go_template: bool = False,
) -> list[dict[str, Any]]:
    """Expand a matrix generator (cartesian product of child generators)."""
    child_generators = config.get("generators", [])
    if len(child_generators) < 2:
        logger.warning("Matrix generator requires at least 2 child generators")
        return []

    # Expand each child generator
    child_params = []
    for child in child_generators:
        expanded = _expand_generator(child, resolver, go_template)
        child_params.append(expanded)

    # Cartesian product
    result = []
    for combo in itertools.product(*child_params):
        merged = {}
        for params in combo:
            merged.update(params)
        result.append(merged)

    return result


def _expand_merge_generator(
    config: dict[str, Any],
    resolver: RepoResolver,
    go_template: bool = False,
) -> list[dict[str, Any]]:
    """Expand a merge generator (merge child generator results by key)."""
    merge_keys = config.get("mergeKeys", [])
    child_generators = config.get("generators", [])

    if not child_generators:
        return []

    # First generator provides the base
    all_expanded = []
    for child in child_generators:
        expanded = _expand_generator(child, resolver, go_template)
        all_expanded.append(expanded)

    if not all_expanded:
        return []

    # Start with first generator results as base
    result_map: dict[str, dict[str, Any]] = {}
    for params in all_expanded[0]:
        key = _make_merge_key(params, merge_keys)
        result_map[key] = dict(params)

    # Merge subsequent generators
    for expanded_list in all_expanded[1:]:
        for params in expanded_list:
            key = _make_merge_key(params, merge_keys)
            if key in result_map:
                result_map[key].update(params)

    return list(result_map.values())


def _make_merge_key(params: dict[str, Any], merge_keys: list[str]) -> str:
    """Create a merge key from parameter values."""
    return "|".join(str(params.get(k, "")) for k in merge_keys)


def _find_matching_directories(pattern: str, base_path: Path) -> set[Path]:
    """Find directories matching a glob pattern relative to base_path.

    The pattern is treated as a glob. If it contains glob characters (* ? [ ]),
    it is globbed directly. If it's a literal path ending with no glob chars,
    the directory itself is returned if it exists (used for exact-match excludes).
    A trailing /* or * is interpreted as "all immediate subdirectories".
    """
    matched = set()

    # Check for trailing glob: "apps/*" or just "*"
    has_trailing_glob = re.search(r"/?\*$", pattern)
    if has_trailing_glob:
        # Use glob directly — this matches subdirectories
        for d in base_path.glob(pattern):
            if d.is_dir():
                matched.add(d.relative_to(base_path))
        return matched

    # Check if this is an absolute path pattern (for backwards compat)
    pattern_path = Path(pattern)
    if pattern_path.is_absolute():
        if pattern_path.is_dir():
            matched.add(pattern_path)
        return matched

    has_glob = any(c in pattern for c in "*?[]")

    if has_glob:
        # Glob pattern without trailing * (e.g. "apps/**/prod")
        for d in base_path.glob(pattern):
            if d.is_dir():
                matched.add(d.relative_to(base_path))
    else:
        # Literal path - return the directory itself if it exists
        search_path = base_path / pattern
        if search_path.is_dir():
            matched.add(Path(pattern))

    return matched


def _find_matching_files(pattern: str, base_path: Path) -> list[Path]:
    """Find files matching a glob pattern relative to base_path."""
    matched = []
    for f in base_path.glob(pattern):
        if f.is_file():
            matched.append(f)
    return sorted(matched)


def _flatten_dict(
    d: dict[str, Any], parent_key: str = "", sep: str = "."
) -> dict[str, Any]:
    """Flatten a nested dict into dot-separated keys."""
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def _normalize_name(name: str) -> str:
    """Normalize a name for use in Kubernetes resource names.

    Replaces non-alphanumeric characters with hyphens.
    """
    return re.sub(r"[^a-zA-Z0-9-]", "-", name).strip("-").lower()


def _apply_selector(
    params: list[dict[str, Any]],
    selector: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply label selector filtering to parameter sets."""
    match_labels = selector.get("matchLabels", {})
    match_expressions = selector.get("matchExpressions", [])

    filtered = []
    for p in params:
        if _matches_selector(p, match_labels, match_expressions):
            filtered.append(p)
    return filtered


def _matches_selector(
    params: dict[str, Any],
    match_labels: dict[str, str],
    match_expressions: list[dict[str, Any]],
) -> bool:
    """Check if a parameter set matches a label selector."""
    # Check matchLabels
    for key, value in match_labels.items():
        if str(params.get(key, "")) != str(value):
            return False

    # Check matchExpressions
    for expr in match_expressions:
        key = expr.get("key", "")
        operator = expr.get("operator", "")
        values = expr.get("values", [])
        param_value = str(params.get(key, ""))

        if operator == "In":
            if param_value not in [str(v) for v in values]:
                return False
        elif operator == "NotIn":
            if param_value in [str(v) for v in values]:
                return False
        elif operator == "Exists":
            if key not in params:
                return False
        elif operator == "DoesNotExist":
            if key in params:
                return False

    return True
