"""Parse ArgoCD Application and ApplicationSet YAML manifests into data models."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .models import (
    Application,
    ApplicationSet,
    Destination,
    DirectoryConfig,
    HelmConfig,
    HelmFileParameter,
    HelmParameter,
    KustomizeConfig,
    PluginConfig,
    Source,
)

logger = logging.getLogger(__name__)

ARGO_KINDS = {"Application", "ApplicationSet"}


def _safe_get(data: dict, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dicts."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def parse_helm_config(raw: dict[str, Any] | None) -> HelmConfig | None:
    """Parse helm configuration from a source dict."""
    if not raw:
        return None

    parameters = []
    for p in raw.get("parameters", []) or []:
        if isinstance(p, dict):
            parameters.append(
                HelmParameter(
                    name=p.get("name", ""),
                    value=str(p.get("value", "")),
                    force_string=p.get("forceString", False),
                )
            )

    file_parameters = []
    for fp in raw.get("fileParameters", []) or []:
        if isinstance(fp, dict):
            file_parameters.append(
                HelmFileParameter(
                    name=fp.get("name", ""),
                    path=fp.get("path", ""),
                )
            )

    return HelmConfig(
        release_name=raw.get("releaseName"),
        value_files=raw.get("valueFiles", []) or [],
        values=raw.get("values"),
        values_object=raw.get("valuesObject"),
        parameters=parameters,
        file_parameters=file_parameters,
        pass_credentials=raw.get("passCredentials", False),
        skip_crds=raw.get("skipCrds", False),
        skip_schema_validation=raw.get("skipSchemaValidation", False),
        skip_tests=raw.get("skipTests", False),
        version=raw.get("version"),
        kube_version=raw.get("kubeVersion"),
        api_versions=raw.get("apiVersions", []) or [],
        namespace=raw.get("namespace"),
        ignore_missing_value_files=raw.get("ignoreMissingValueFiles", False),
    )


def parse_kustomize_config(raw: dict[str, Any] | None) -> KustomizeConfig | None:
    """Parse kustomize configuration from a source dict."""
    if not raw:
        return None

    return KustomizeConfig(
        name_prefix=raw.get("namePrefix"),
        name_suffix=raw.get("nameSuffix"),
        common_labels=raw.get("commonLabels", {}) or {},
        common_annotations=raw.get("commonAnnotations", {}) or {},
        force_common_labels=raw.get("forceCommonLabels", False),
        force_common_annotations=raw.get("forceCommonAnnotations", False),
        images=raw.get("images", []) or [],
        replicas=raw.get("replicas", []) or [],
        namespace=raw.get("namespace"),
        components=raw.get("components", []) or [],
        patches=raw.get("patches", []) or [],
        version=raw.get("version"),
        label_without_selector=raw.get("labelWithoutSelector", False),
    )


def parse_directory_config(raw: dict[str, Any] | None) -> DirectoryConfig | None:
    """Parse directory configuration from a source dict."""
    if not raw:
        return None

    return DirectoryConfig(
        recurse=raw.get("recurse", False),
        include=raw.get("include"),
        exclude=raw.get("exclude"),
        jsonnet=raw.get("jsonnet"),
    )


def parse_plugin_config(raw: dict[str, Any] | None) -> PluginConfig | None:
    """Parse plugin configuration from a source dict."""
    if not raw:
        return None

    return PluginConfig(
        name=raw.get("name"),
        env=raw.get("env", []) or [],
        parameters=raw.get("parameters", []) or [],
    )


def parse_source(raw: dict[str, Any]) -> Source:
    """Parse a single source from raw YAML data."""
    return Source(
        repo_url=raw.get("repoURL", ""),
        target_revision=raw.get("targetRevision", ""),
        path=raw.get("path"),
        chart=raw.get("chart"),
        ref=raw.get("ref"),
        name=raw.get("name"),
        helm=parse_helm_config(raw.get("helm")),
        kustomize=parse_kustomize_config(raw.get("kustomize")),
        directory=parse_directory_config(raw.get("directory")),
        plugin=parse_plugin_config(raw.get("plugin")),
    )


def parse_destination(raw: dict[str, Any] | None) -> Destination:
    """Parse destination from raw YAML data."""
    if not raw:
        return Destination()

    return Destination(
        server=raw.get("server"),
        name=raw.get("name"),
        namespace=raw.get("namespace", "default"),
    )


def parse_application(data: dict[str, Any]) -> Application:
    """Parse an ArgoCD Application from a YAML document."""
    spec = data.get("spec", {})
    metadata = data.get("metadata", {})

    sources = []
    if "source" in spec:
        sources.append(parse_source(spec["source"]))
    if "sources" in spec:
        for src_data in spec["sources"]:
            sources.append(parse_source(src_data))

    return Application(
        metadata_name=metadata.get("name", ""),
        metadata_namespace=metadata.get("namespace"),
        project=spec.get("project", "default"),
        sources=sources,
        destination=parse_destination(spec.get("destination")),
        sync_policy=spec.get("syncPolicy"),
        ignore_differences=spec.get("ignoreDifferences", []),
    )


def parse_application_set(data: dict[str, Any]) -> ApplicationSet:
    """Parse an ArgoCD ApplicationSet from a YAML document."""
    spec = data.get("spec", {})
    metadata = data.get("metadata", {})

    return ApplicationSet(
        metadata_name=metadata.get("name", ""),
        metadata_namespace=metadata.get("namespace"),
        generators=spec.get("generators", []),
        template=spec.get("template", {}),
        go_template=spec.get("goTemplate", False),
        go_template_options=spec.get("goTemplateOptions", []),
        sync_policy=spec.get("syncPolicy"),
    )


def load_yaml_file(file_path: Path) -> list[dict[str, Any]]:
    """Load all YAML documents from a file, returning a list of dicts."""
    documents = []
    try:
        with open(file_path) as f:
            for doc in yaml.safe_load_all(f):
                if doc and isinstance(doc, dict):
                    documents.append(doc)
    except yaml.YAMLError as e:
        logger.warning("Failed to parse YAML file %s: %s", file_path, e)
    except OSError as e:
        logger.warning("Failed to read file %s: %s", file_path, e)
    return documents


def discover_argo_manifests(
    apps_dir: Path, skip_files: list[str] | None = None
) -> tuple[list[Application], list[ApplicationSet]]:
    """Discover and parse all ArgoCD Application and ApplicationSet manifests
    in a directory.

    Returns:
        Tuple of (applications, application_sets)
    """
    applications: list[Application] = []
    application_sets: list[ApplicationSet] = []
    skip = set(skip_files or [])

    if not apps_dir.is_dir():
        logger.warning("Apps directory does not exist: %s", apps_dir)
        return applications, application_sets

    for yaml_file in sorted(apps_dir.rglob("*")):
        if yaml_file.suffix not in (".yaml", ".yml"):
            continue
        if yaml_file.name in skip:
            logger.info("Skipping file: %s", yaml_file.name)
            continue

        for doc in load_yaml_file(yaml_file):
            kind = doc.get("kind", "")
            if kind == "Application":
                app = parse_application(doc)
                app._source_file = str(yaml_file)
                applications.append(app)
                logger.info("Found Application: %s", app.metadata_name)
            elif kind == "ApplicationSet":
                appset = parse_application_set(doc)
                appset._source_file = str(yaml_file)
                application_sets.append(appset)
                logger.info("Found ApplicationSet: %s", appset.metadata_name)
            elif kind in ARGO_KINDS:
                logger.debug("Skipping unknown ArgoCD kind: %s", kind)

    return applications, application_sets
