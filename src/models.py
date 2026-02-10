"""Data models for ArgoCD Application and ApplicationSet resources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SourceType(Enum):
    """Detected source type for an ArgoCD application source."""

    HELM = "helm"
    KUSTOMIZE = "kustomize"
    DIRECTORY = "directory"
    PLUGIN = "plugin"


@dataclass
class HelmParameter:
    """A single Helm --set parameter."""

    name: str
    value: str
    force_string: bool = False


@dataclass
class HelmFileParameter:
    """A single Helm --set-file parameter."""

    name: str
    path: str


@dataclass
class HelmConfig:
    """Helm-specific source configuration."""

    release_name: str | None = None
    value_files: list[str] = field(default_factory=list)
    values: str | None = None
    values_object: dict[str, Any] | None = None
    parameters: list[HelmParameter] = field(default_factory=list)
    file_parameters: list[HelmFileParameter] = field(default_factory=list)
    pass_credentials: bool = False
    skip_crds: bool = False
    skip_schema_validation: bool = False
    skip_tests: bool = False
    version: str | None = None
    kube_version: str | None = None
    api_versions: list[str] = field(default_factory=list)
    namespace: str | None = None
    ignore_missing_value_files: bool = False


@dataclass
class KustomizeConfig:
    """Kustomize-specific source configuration."""

    name_prefix: str | None = None
    name_suffix: str | None = None
    common_labels: dict[str, str] = field(default_factory=dict)
    common_annotations: dict[str, str] = field(default_factory=dict)
    force_common_labels: bool = False
    force_common_annotations: bool = False
    images: list[str] = field(default_factory=list)
    replicas: list[dict[str, Any]] = field(default_factory=list)
    namespace: str | None = None
    components: list[str] = field(default_factory=list)
    patches: list[dict[str, Any]] = field(default_factory=list)
    version: str | None = None
    label_without_selector: bool = False


@dataclass
class DirectoryConfig:
    """Directory-specific source configuration."""

    recurse: bool = False
    include: str | None = None
    exclude: str | None = None
    jsonnet: dict[str, Any] | None = None


@dataclass
class PluginConfig:
    """Plugin-specific source configuration."""

    name: str | None = None
    env: list[dict[str, str]] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Source:
    """An ArgoCD application source."""

    repo_url: str = ""
    target_revision: str = ""
    path: str | None = None
    chart: str | None = None
    ref: str | None = None
    name: str | None = None
    helm: HelmConfig | None = None
    kustomize: KustomizeConfig | None = None
    directory: DirectoryConfig | None = None
    plugin: PluginConfig | None = None

    def detect_type(self, resolved_path: Path | None = None) -> SourceType:
        """Detect the source type based on configuration and filesystem."""
        # Explicit plugin
        if self.plugin and self.plugin.name:
            return SourceType.PLUGIN

        # Chart-based helm
        if self.chart:
            return SourceType.HELM

        # Explicit helm config
        if self.helm:
            return SourceType.HELM

        # Explicit kustomize config
        if self.kustomize:
            return SourceType.KUSTOMIZE

        # Explicit directory config
        if self.directory:
            return SourceType.DIRECTORY

        # Auto-detect from filesystem
        if resolved_path and resolved_path.is_dir():
            # Check for Helm
            if (resolved_path / "Chart.yaml").exists():
                return SourceType.HELM

            # Check for Kustomize
            for kust_file in [
                "kustomization.yaml",
                "kustomization.yml",
                "Kustomization",
            ]:
                if (resolved_path / kust_file).exists():
                    return SourceType.KUSTOMIZE

        return SourceType.DIRECTORY


@dataclass
class Destination:
    """ArgoCD application destination."""

    server: str | None = None
    name: str | None = None
    namespace: str = "default"


@dataclass
class Application:
    """An ArgoCD Application resource."""

    metadata_name: str = ""
    metadata_namespace: str | None = None
    project: str = "default"
    sources: list[Source] = field(default_factory=list)
    destination: Destination = field(default_factory=Destination)
    sync_policy: dict[str, Any] | None = None
    ignore_differences: list[dict[str, Any]] = field(default_factory=list)

    @property
    def source(self) -> Source | None:
        """Return the first source (for single-source apps)."""
        return self.sources[0] if self.sources else None

    @property
    def is_multi_source(self) -> bool:
        return len(self.sources) > 1


@dataclass
class GitDirectoryGenerator:
    """Git directory-based ApplicationSet generator."""

    repo_url: str = ""
    revision: str = "HEAD"
    directories: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GitFileGenerator:
    """Git file-based ApplicationSet generator."""

    repo_url: str = ""
    revision: str = "HEAD"
    files: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ListGenerator:
    """List-based ApplicationSet generator."""

    elements: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MatrixGenerator:
    """Matrix (cartesian product) ApplicationSet generator."""

    generators: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MergeGenerator:
    """Merge ApplicationSet generator."""

    merge_keys: list[str] = field(default_factory=list)
    generators: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ApplicationSet:
    """An ArgoCD ApplicationSet resource."""

    metadata_name: str = ""
    metadata_namespace: str | None = None
    generators: list[dict[str, Any]] = field(default_factory=list)
    template: dict[str, Any] = field(default_factory=dict)
    go_template: bool = False
    go_template_options: list[str] = field(default_factory=list)
    sync_policy: dict[str, Any] | None = None


def substitute_params(text: str, params: dict[str, Any]) -> str:
    """Substitute ArgoCD template parameters in text.

    Supports both legacy {{param}} and nested {{path.basename}} syntax.
    """
    if not isinstance(text, str):
        return text

    result = text
    # Sort by key length descending so longer keys match first
    for key in sorted(params.keys(), key=len, reverse=True):
        value = str(params[key])
        # Legacy fasttemplate syntax: {{key}}
        result = result.replace("{{" + key + "}}", value)
        # Also support dot notation: {{path.basename}} etc.
        # This is handled by the above since we flatten keys

    return result


def substitute_params_deep(obj: Any, params: dict[str, Any]) -> Any:
    """Recursively substitute template parameters in a nested structure."""
    if isinstance(obj, str):
        return substitute_params(obj, params)
    elif isinstance(obj, dict):
        return {k: substitute_params_deep(v, params) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [substitute_params_deep(item, params) for item in obj]
    return obj
