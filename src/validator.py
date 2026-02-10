"""Validate Kubernetes manifests and ArgoCD application definitions."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ArgoCD CRD schemas for validation
ARGO_API_GROUPS = {
    "argoproj.io/v1alpha1": {
        "Application",
        "ApplicationSet",
        "AppProject",
    }
}

REQUIRED_APPLICATION_FIELDS = {
    "spec.destination",
}

REQUIRED_APPLICATION_SOURCE_FIELDS = {
    "repoURL",
}


@dataclass
class ValidationResult:
    """Result of validating a manifest file or resource."""

    file_path: str
    resource_name: str = ""
    resource_kind: str = ""
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.valid = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


@dataclass
class ValidationSummary:
    """Summary of all validation results."""

    results: list[ValidationResult] = field(default_factory=list)
    total: int = 0
    valid: int = 0
    invalid: int = 0
    warnings: int = 0
    skipped: int = 0

    @property
    def success(self) -> bool:
        return self.invalid == 0

    def add(self, result: ValidationResult) -> None:
        self.results.append(result)
        self.total += 1
        if result.valid:
            self.valid += 1
        else:
            self.invalid += 1
        if result.warnings:
            self.warnings += len(result.warnings)

    def format_text(self) -> str:
        """Format summary as human-readable text."""
        lines = ["Validation Summary:"]
        lines.append(f"  Total:    {self.total}")
        lines.append(f"  Valid:    {self.valid}")
        lines.append(f"  Invalid:  {self.invalid}")
        lines.append(f"  Warnings: {self.warnings}")
        lines.append(f"  Skipped:  {self.skipped}")
        lines.append("")

        for result in self.results:
            if not result.valid:
                status = "INVALID"
            elif result.warnings:
                status = "WARNING"
            else:
                status = "VALID"

            name = result.resource_name or result.file_path
            kind = f" ({result.resource_kind})" if result.resource_kind else ""
            lines.append(f"  [{status}] {name}{kind}")

            for error in result.errors:
                lines.append(f"    ERROR: {error}")
            for warning in result.warnings:
                lines.append(f"    WARNING: {warning}")

        return "\n".join(lines)


def validate_argo_manifest(file_path: Path) -> list[ValidationResult]:
    """Validate an ArgoCD Application or ApplicationSet manifest.

    Checks:
    - Valid YAML syntax
    - Required ArgoCD fields present
    - Source configuration is valid
    - Destination is specified
    """
    results = []

    try:
        with open(file_path) as f:
            documents = list(yaml.safe_load_all(f))
    except yaml.YAMLError as e:
        result = ValidationResult(
            file_path=str(file_path),
            resource_kind="Unknown",
        )
        result.add_error(f"Invalid YAML syntax: {e}")
        return [result]
    except OSError as e:
        result = ValidationResult(
            file_path=str(file_path),
            resource_kind="Unknown",
        )
        result.add_error(f"Cannot read file: {e}")
        return [result]

    for doc in documents:
        if not isinstance(doc, dict):
            continue

        kind = doc.get("kind", "")
        api_version = doc.get("apiVersion", "")
        metadata = doc.get("metadata", {}) or {}
        name = metadata.get("name", "unknown")
        spec = doc.get("spec", {}) or {}

        result = ValidationResult(
            file_path=str(file_path),
            resource_name=name,
            resource_kind=kind,
        )

        if kind == "Application":
            _validate_application(doc, result)
        elif kind == "ApplicationSet":
            _validate_application_set(doc, result)
        elif api_version.startswith("argoproj.io/"):
            result.add_warning(f"Unknown ArgoCD kind: {kind}")
        else:
            # Not an ArgoCD resource, skip
            continue

        results.append(result)

    return results


def _validate_application(doc: dict[str, Any], result: ValidationResult) -> None:
    """Validate an Application document."""
    spec = doc.get("spec", {}) or {}

    # Check for source or sources
    has_source = "source" in spec
    has_sources = "sources" in spec

    if not has_source and not has_sources:
        result.add_error("Application must have 'spec.source' or 'spec.sources'")

    if has_source and has_sources:
        result.add_warning(
            "Application has both 'spec.source' and 'spec.sources'; "
            "'spec.sources' takes precedence"
        )

    # Validate sources
    sources = []
    if has_sources:
        sources = spec.get("sources", []) or []
    elif has_source:
        sources = [spec["source"]]

    for i, source in enumerate(sources):
        _validate_source(source, result, index=i)

    # Check destination
    destination = spec.get("destination", {})
    if not destination:
        result.add_error("Application must have 'spec.destination'")
    else:
        if not destination.get("server") and not destination.get("name"):
            result.add_warning("Destination should have 'server' or 'name' specified")

    # Check project
    if not spec.get("project"):
        result.add_warning("No project specified, defaults to 'default'")


def _validate_source(
    source: dict[str, Any], result: ValidationResult, index: int = 0
) -> None:
    """Validate a single source entry."""
    prefix = f"sources[{index}]" if index > 0 else "source"

    if not source.get("repoURL"):
        # ref-only sources don't need repoURL
        if not source.get("ref"):
            result.add_error(f"{prefix}: 'repoURL' is required")

    chart = source.get("chart")
    path = source.get("path")
    ref = source.get("ref")

    if chart and path:
        result.add_error(
            f"{prefix}: Cannot have both 'chart' and 'path' in the same source"
        )

    if chart and not source.get("targetRevision"):
        result.add_warning(
            f"{prefix}: Helm chart source should have 'targetRevision' (chart version)"
        )

    # Validate helm config
    helm = source.get("helm", {})
    if helm:
        _validate_helm_config(helm, result, prefix)

    # Validate kustomize config
    kustomize = source.get("kustomize", {})
    if kustomize and chart:
        result.add_warning(f"{prefix}: Source has both 'chart' and 'kustomize' config")


def _validate_helm_config(
    helm: dict[str, Any], result: ValidationResult, prefix: str
) -> None:
    """Validate Helm-specific configuration."""
    value_files = helm.get("valueFiles", []) or []
    for vf in value_files:
        if not isinstance(vf, str):
            result.add_error(f"{prefix}.helm.valueFiles: entries must be strings")

    parameters = helm.get("parameters", []) or []
    for i, param in enumerate(parameters):
        if isinstance(param, dict):
            if "name" not in param:
                result.add_error(f"{prefix}.helm.parameters[{i}]: 'name' is required")

    values = helm.get("values")
    values_object = helm.get("valuesObject")
    if values and values_object:
        result.add_warning(
            f"{prefix}.helm: both 'values' and 'valuesObject' set; "
            "'valuesObject' takes precedence"
        )


def _validate_application_set(doc: dict[str, Any], result: ValidationResult) -> None:
    """Validate an ApplicationSet document."""
    spec = doc.get("spec", {}) or {}

    generators = spec.get("generators", [])
    if not generators:
        result.add_error("ApplicationSet must have at least one generator")

    template = spec.get("template", {})
    if not template:
        result.add_error("ApplicationSet must have a template")
    else:
        # Validate template has required fields
        template_spec = template.get("spec", {})
        if not template_spec:
            result.add_error("ApplicationSet template must have 'spec'")
        else:
            if not template_spec.get("source") and not template_spec.get("sources"):
                result.add_error(
                    "ApplicationSet template must have 'source' or 'sources'"
                )

    for i, gen in enumerate(generators):
        _validate_generator(gen, result, index=i)


def _validate_generator(
    generator: dict[str, Any], result: ValidationResult, index: int = 0
) -> None:
    """Validate a single generator configuration."""
    prefix = f"generators[{index}]"

    known_types = {
        "list",
        "clusters",
        "git",
        "matrix",
        "merge",
        "scmProvider",
        "pullRequest",
        "clusterDecisionResource",
        "plugin",
    }
    # Find generator type (exclude meta-fields)
    meta_fields = {"selector", "values", "template"}
    gen_types = set(generator.keys()) - meta_fields

    if not gen_types & known_types:
        result.add_warning(f"{prefix}: Unknown generator type(s): {gen_types}")

    if "git" in generator:
        git_config = generator["git"]
        if "directories" not in git_config and "files" not in git_config:
            result.add_error(f"{prefix}.git: Must have 'directories' or 'files'")

    if "matrix" in generator:
        matrix_config = generator["matrix"]
        child_gens = matrix_config.get("generators", [])
        if len(child_gens) < 2:
            result.add_error(f"{prefix}.matrix: Requires at least 2 child generators")

    if "merge" in generator:
        merge_config = generator["merge"]
        if not merge_config.get("mergeKeys"):
            result.add_error(f"{prefix}.merge: 'mergeKeys' is required")


def validate_k8s_manifests(
    manifests_dir: Path,
    skip_resources: list[str] | None = None,
    schema_locations: list[str] | None = None,
) -> tuple[bool, str]:
    """Validate rendered Kubernetes manifests using kubeconform.

    Args:
        manifests_dir: Directory containing rendered manifests
        skip_resources: List of resource kinds to skip
        schema_locations: Custom schema locations for CRDs

    Returns:
        Tuple of (success, output_text)
    """
    if not manifests_dir.is_dir():
        return False, f"Manifests directory does not exist: {manifests_dir}"

    kubeconform = _find_kubeconform()
    if not kubeconform:
        logger.warning("kubeconform not found, skipping K8s validation")
        return True, "kubeconform not available, skipping validation"

    cmd = [
        kubeconform,
        "-summary",
        "-output",
        "text",
        "-schema-location",
        "default",
        "-schema-location",
        "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
        "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json",
    ]

    if schema_locations:
        for loc in schema_locations:
            cmd.extend(["-schema-location", loc])

    if skip_resources:
        cmd.extend(["-skip", ",".join(skip_resources)])

    cmd.append(str(manifests_dir))

    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    output = result.stdout
    if result.stderr:
        output += "\n" + result.stderr

    return result.returncode == 0, output.strip()


def validate_argo_manifests_dir(
    apps_dir: Path,
    skip_files: list[str] | None = None,
) -> ValidationSummary:
    """Validate all ArgoCD manifests in a directory.

    Args:
        apps_dir: Directory containing ArgoCD application manifests
        skip_files: List of files to skip

    Returns:
        ValidationSummary with all results
    """
    summary = ValidationSummary()
    skip = set(skip_files or [])

    if not apps_dir.is_dir():
        result = ValidationResult(
            file_path=str(apps_dir),
            resource_kind="Directory",
        )
        result.add_error(f"Directory does not exist: {apps_dir}")
        summary.add(result)
        return summary

    for yaml_file in sorted(apps_dir.rglob("*")):
        if yaml_file.suffix not in (".yaml", ".yml"):
            continue
        if yaml_file.name in skip:
            summary.skipped += 1
            continue

        results = validate_argo_manifest(yaml_file)
        for result in results:
            summary.add(result)

    return summary


def _find_kubeconform() -> str | None:
    """Find kubeconform binary."""
    import shutil

    return shutil.which("kubeconform")
