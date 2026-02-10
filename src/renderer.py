"""Render manifests from ArgoCD sources using Helm, Kustomize, or plain copy."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .models import (
    Application,
    DirectoryConfig,
    HelmConfig,
    KustomizeConfig,
    Source,
    SourceType,
)
from .repos import RepoResolver

logger = logging.getLogger(__name__)

# Default kube version if not detected
DEFAULT_KUBE_VERSION = "1.30.0"


def get_kube_version(override: str | None = None) -> str:
    """Get the Kubernetes version to use for helm template."""
    if override:
        return override

    env_version = os.environ.get("HELM_KUBEVERSION")
    if env_version:
        return env_version

    try:
        result = subprocess.run(
            ["kubectl", "version", "--client", "--output=json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            ver = data.get("clientVersion", {})
            major = ver.get("major", "1")
            minor = ver.get("minor", "30")
            return f"{major}.{minor}.0"
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    return DEFAULT_KUBE_VERSION


def render_helm(
    source: Source,
    app: Application,
    output_dir: Path,
    resolver: RepoResolver,
    ref_sources: dict[str, Source] | None = None,
    kube_version: str | None = None,
) -> None:
    """Render manifests using helm template.

    Args:
        source: The Helm source configuration
        app: The parent Application
        output_dir: Directory to write rendered manifests
        resolver: Repository resolver for cloning/caching repos
        ref_sources: Map of ref name -> Source for multi-source $ref resolution
        kube_version: Kubernetes version override
    """
    helm = source.helm or HelmConfig()

    release_name = helm.release_name or app.metadata_name
    chart = source.chart
    namespace = helm.namespace or app.destination.namespace or "default"
    version = source.target_revision
    effective_kube_version = helm.kube_version or get_kube_version(kube_version)

    # Only resolve the git repo when we actually need a local path
    # (chart-from-path, value files, file parameters).  Helm chart repos
    # are fetched by helm itself via --repo.
    base_path: Path | None = None

    def _get_base_path() -> Path:
        nonlocal base_path
        if base_path is None:
            base_path = resolver.resolve(source.repo_url, source.target_revision)
        return base_path

    cmd = ["helm", "template", release_name]

    if chart:
        # Chart from Helm repository — Helm handles fetching, no git clone needed
        cmd.extend([chart, "--repo", source.repo_url])
        if version:
            cmd.extend(["--version", version])
    else:
        # Chart from git path — resolve the repo locally
        resolved_base = _get_base_path()
        chart_path = source.path or "."
        resolved_chart_path = resolved_base / chart_path
        if not resolved_chart_path.exists():
            raise FileNotFoundError(
                f"Helm chart path does not exist: {resolved_chart_path}"
            )
        cmd.append(str(resolved_chart_path))

    cmd.extend(["--namespace", namespace])

    if effective_kube_version:
        cmd.extend(["--kube-version", effective_kube_version])

    for api_ver in helm.api_versions:
        cmd.extend(["--api-versions", api_ver])

    if helm.skip_crds:
        cmd.append("--skip-crds")

    if helm.skip_tests:
        cmd.append("--skip-tests")

    if helm.skip_schema_validation:
        cmd.append("--no-hooks")  # Closest equivalent

    # Handle value files with $ref resolution
    ref_sources = ref_sources or {}
    for vf in helm.value_files:
        # For chart-repo sources, we have no local checkout so base_path is
        # None.  $ref files are still resolved via the ref source's repo and
        # non-$ref files that ship inside the chart are not resolvable here
        # (Helm will handle them from the downloaded archive).
        vf_base = None if chart else _get_base_path()
        resolved = _resolve_value_file(vf, vf_base, ref_sources, resolver)
        if resolved:
            cmd.extend(["--values", str(resolved)])
        elif not helm.ignore_missing_value_files:
            raise FileNotFoundError(f"Values file not found: {vf}")
        else:
            logger.warning("Ignoring missing values file: %s", vf)

    # Handle inline values
    values_yaml = None
    if helm.values_object:
        values_yaml = yaml.dump(helm.values_object)
    elif helm.values:
        values_yaml = helm.values

    tmp_values_file = None
    if values_yaml:
        tmp_values_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        )
        tmp_values_file.write(values_yaml)
        tmp_values_file.close()
        cmd.extend(["--values", tmp_values_file.name])

    # Handle --set parameters
    for param in helm.parameters:
        flag = "--set-string" if param.force_string else "--set"
        cmd.extend([flag, f"{param.name}={param.value}"])

    # Handle --set-file parameters
    for fp in helm.file_parameters:
        fp_base = None if chart else _get_base_path()
        resolved_fp = _resolve_value_file(fp.path, fp_base, ref_sources, resolver)
        if resolved_fp:
            cmd.extend(["--set-file", f"{fp.name}={resolved_fp}"])

    cmd.extend(["--output-dir", str(output_dir.resolve())])

    # Set environment variables that ArgoCD provides
    env = os.environ.copy()
    env.update(
        {
            "ARGOCD_APP_NAME": app.metadata_name,
            "ARGOCD_APP_NAMESPACE": app.destination.namespace or "default",
            "ARGOCD_APP_SOURCE_REPO_URL": source.repo_url,
            "ARGOCD_APP_SOURCE_TARGET_REVISION": source.target_revision or "",
            "ARGOCD_APP_SOURCE_PATH": source.path or "",
        }
    )

    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=tempfile.gettempdir(),
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Helm template failed for {app.metadata_name}:\n"
                f"Command: {' '.join(cmd)}\n"
                f"Stderr: {result.stderr}\n"
                f"Stdout: {result.stdout}"
            )
        logger.info("Helm template succeeded for %s", app.metadata_name)
        if result.stdout:
            logger.debug("Helm stdout: %s", result.stdout)
    finally:
        if tmp_values_file:
            os.unlink(tmp_values_file.name)


def render_kustomize(
    source: Source,
    app: Application,
    output_dir: Path,
    resolver: RepoResolver,
    kube_version: str | None = None,
) -> None:
    """Render manifests using kubectl kustomize.

    Args:
        source: The Kustomize source configuration
        app: The parent Application
        output_dir: Directory to write rendered manifests
        resolver: Repository resolver for cloning/caching repos
        kube_version: Kubernetes version override (unused for kustomize currently)
    """
    kustomize = source.kustomize or KustomizeConfig()
    source_path = source.path or "."
    base_path = resolver.resolve(source.repo_url, source.target_revision)
    resolved_path = base_path / source_path

    if not resolved_path.is_dir():
        raise FileNotFoundError(f"Kustomize path does not exist: {resolved_path}")

    # Build kustomize command
    use_kustomize_binary = shutil.which("kustomize") is not None
    if use_kustomize_binary:
        cmd = ["kustomize", "build", str(resolved_path)]
    else:
        cmd = ["kubectl", "kustomize", str(resolved_path)]

    # Apply kustomize-specific options via environment or edit
    # For complex overrides, we create a temporary kustomization overlay
    needs_overlay = any(
        [
            kustomize.name_prefix,
            kustomize.name_suffix,
            kustomize.common_labels,
            kustomize.common_annotations,
            kustomize.images,
            kustomize.replicas,
            kustomize.components,
            kustomize.patches,
            kustomize.namespace,
        ]
    )

    overlay_dir = None
    try:
        if needs_overlay:
            overlay_dir = tempfile.mkdtemp(prefix="argocd-kustomize-")
            overlay_kustomization = _build_kustomize_overlay(resolved_path, kustomize)
            with open(Path(overlay_dir) / "kustomization.yaml", "w") as f:
                yaml.dump(overlay_kustomization, f)

            if use_kustomize_binary:
                cmd = ["kustomize", "build", overlay_dir]
            else:
                cmd = ["kubectl", "kustomize", overlay_dir]

        # Set environment variables
        env = os.environ.copy()
        env.update(
            {
                "ARGOCD_APP_NAME": app.metadata_name,
                "ARGOCD_APP_NAMESPACE": app.destination.namespace or "default",
                "ARGOCD_APP_SOURCE_REPO_URL": source.repo_url,
                "ARGOCD_APP_SOURCE_TARGET_REVISION": source.target_revision or "",
                "ARGOCD_APP_SOURCE_PATH": source.path or "",
            }
        )

        logger.info("Running: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Kustomize build failed for {app.metadata_name}:\n"
                f"Command: {' '.join(cmd)}\n"
                f"Stderr: {result.stderr}\n"
                f"Stdout: {result.stdout}"
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "manifests.yaml"
        output_file.write_text(result.stdout)
        logger.info("Kustomize build succeeded for %s", app.metadata_name)

    finally:
        if overlay_dir:
            shutil.rmtree(overlay_dir, ignore_errors=True)


def _build_kustomize_overlay(
    base_path: Path, kustomize: KustomizeConfig
) -> dict[str, Any]:
    """Build a kustomization.yaml overlay dict with ArgoCD overrides."""
    overlay: dict[str, Any] = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": [str(base_path)],
    }

    if kustomize.name_prefix:
        overlay["namePrefix"] = kustomize.name_prefix
    if kustomize.name_suffix:
        overlay["nameSuffix"] = kustomize.name_suffix
    if kustomize.common_labels:
        overlay["commonLabels"] = kustomize.common_labels
    if kustomize.common_annotations:
        overlay["commonAnnotations"] = kustomize.common_annotations
    if kustomize.images:
        overlay["images"] = _parse_kustomize_images(kustomize.images)
    if kustomize.replicas:
        overlay["replicas"] = kustomize.replicas
    if kustomize.namespace:
        overlay["namespace"] = kustomize.namespace
    if kustomize.components:
        overlay["components"] = kustomize.components
    if kustomize.patches:
        overlay["patches"] = kustomize.patches

    return overlay


def _parse_kustomize_images(images: list[str]) -> list[dict[str, str]]:
    """Parse ArgoCD-style image overrides to kustomize format.

    ArgoCD format: "image=newimage:tag" or "image:tag" or "image=newimage"
    """
    result = []
    for img in images:
        entry: dict[str, str] = {}
        if "=" in img:
            parts = img.split("=", 1)
            entry["name"] = parts[0]
            new_parts = parts[1].rsplit(":", 1)
            entry["newName"] = new_parts[0]
            if len(new_parts) > 1:
                entry["newTag"] = new_parts[1]
        else:
            parts = img.rsplit(":", 1)
            entry["name"] = parts[0]
            if len(parts) > 1:
                entry["newTag"] = parts[1]
        result.append(entry)
    return result


def render_directory(
    source: Source,
    app: Application,
    output_dir: Path,
    resolver: RepoResolver,
) -> None:
    """Collect plain YAML/JSON manifests from a directory.

    Args:
        source: The directory source configuration
        app: The parent Application
        output_dir: Directory to write collected manifests
        resolver: Repository resolver for cloning/caching repos
    """
    directory = source.directory or DirectoryConfig()
    source_path = source.path or "."
    base_path = resolver.resolve(source.repo_url, source.target_revision)
    resolved_path = base_path / source_path

    if not resolved_path.is_dir():
        raise FileNotFoundError(f"Directory path does not exist: {resolved_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    include_pattern = directory.include
    exclude_pattern = directory.exclude

    if directory.recurse:
        files = list(resolved_path.rglob("*"))
    else:
        files = list(resolved_path.iterdir())

    for f in sorted(files):
        if not f.is_file():
            continue
        if f.suffix not in (".yaml", ".yml", ".json"):
            continue

        # Check include/exclude patterns
        rel_name = str(f.relative_to(resolved_path))
        if include_pattern and not _glob_match(rel_name, include_pattern):
            continue
        if exclude_pattern and _glob_match(rel_name, exclude_pattern):
            continue

        # Check for skip rendering directive
        try:
            content = f.read_text()
            if "# +argocd:skip-file-rendering" in content:
                logger.debug("Skipping file with skip directive: %s", f)
                continue
        except OSError:
            continue

        dest = output_dir / f.relative_to(resolved_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)

    logger.info("Directory manifests collected for %s", app.metadata_name)


def _glob_match(filename: str, pattern: str) -> bool:
    """Simple glob matching for include/exclude patterns."""
    import fnmatch

    # Handle {*.yml,*.yaml} brace expansion
    if pattern.startswith("{") and pattern.endswith("}"):
        patterns = pattern[1:-1].split(",")
        return any(fnmatch.fnmatch(filename, p.strip()) for p in patterns)

    return fnmatch.fnmatch(filename, pattern)


def _resolve_value_file(
    value_file: str,
    base_path: Path | None,
    ref_sources: dict[str, Source],
    resolver: RepoResolver | None = None,
) -> Path | None:
    """Resolve a values file path, handling $ref syntax.

    Args:
        value_file: The value file path (may contain $ref/ prefix)
        base_path: Base path for resolving relative paths.  May be ``None``
            for Helm chart-repo sources where we do not have a local checkout.
        ref_sources: Map of ref name -> Source
        resolver: Optional repo resolver for ref source resolution
    """
    # Handle $ref/ syntax for multi-source values
    if value_file.startswith("$"):
        parts = value_file.split("/", 1)
        ref_name = parts[0][1:]  # Remove $ prefix
        if ref_name in ref_sources:
            ref_source = ref_sources[ref_name]
            ref_base = base_path
            if resolver:
                ref_base = resolver.resolve(
                    ref_source.repo_url, ref_source.target_revision
                )
            if ref_base is not None:
                ref_path = ref_source.path or ""
                if len(parts) > 1:
                    resolved = ref_base / ref_path / parts[1]
                else:
                    resolved = ref_base / ref_path
                if resolved.exists():
                    return resolved.resolve()
        # Fallback: try removing $ref/ prefix and resolve against base_path
        if base_path is not None and len(parts) > 1:
            local_path = parts[1]
            resolved = base_path / local_path
            if resolved.exists():
                return resolved.resolve()
        return None

    # Regular path resolution
    resolved = Path(value_file)
    if resolved.is_absolute() and resolved.exists():
        return resolved

    if base_path is not None:
        resolved = base_path / value_file
        if resolved.exists():
            return resolved.resolve()

    return None


def render_source(
    source: Source,
    app: Application,
    output_dir: Path,
    resolver: RepoResolver,
    ref_sources: dict[str, Source] | None = None,
    kube_version: str | None = None,
) -> None:
    """Render a single source based on its detected type.

    Args:
        source: The source to render
        app: The parent Application
        output_dir: Directory to write rendered manifests
        resolver: Repository resolver for cloning/caching repos
        ref_sources: Map of ref name -> Source for multi-source resolution
        kube_version: Kubernetes version override
    """
    # Skip ref-only sources (they provide values but don't generate manifests)
    if source.ref and not source.chart and not source.path:
        logger.debug("Skipping ref-only source: %s", source.ref)
        return

    resolved_path = None
    if source.path and not source.chart:
        # Only resolve git repos for path-based sources (not Helm chart repos)
        base_path = resolver.resolve(source.repo_url, source.target_revision)
        resolved_path = base_path / source.path
        if not resolved_path.exists():
            resolved_path = None

    source_type = source.detect_type(resolved_path)

    logger.info(
        "Rendering source for %s (type: %s)",
        app.metadata_name,
        source_type.value,
    )

    if source_type == SourceType.HELM:
        render_helm(source, app, output_dir, resolver, ref_sources, kube_version)
    elif source_type == SourceType.KUSTOMIZE:
        render_kustomize(source, app, output_dir, resolver, kube_version)
    elif source_type == SourceType.DIRECTORY:
        render_directory(source, app, output_dir, resolver)
    elif source_type == SourceType.PLUGIN:
        logger.warning(
            "Plugin sources are not supported for local rendering: %s",
            app.metadata_name,
        )


def render_application(
    app: Application,
    output_dir: Path,
    resolver: RepoResolver,
    kube_version: str | None = None,
) -> None:
    """Render all sources for an Application.

    Args:
        app: The Application to render
        output_dir: Base output directory (app gets its own subdir)
        resolver: Repository resolver for cloning/caching repos
        kube_version: Kubernetes version override
    """
    app_output_dir = output_dir / app.metadata_name
    app_output_dir.mkdir(parents=True, exist_ok=True)

    # Build ref source map for multi-source apps
    ref_sources: dict[str, Source] = {}
    for source in app.sources:
        if source.ref:
            ref_sources[source.ref] = source

    for source in app.sources:
        render_source(source, app, app_output_dir, resolver, ref_sources, kube_version)
