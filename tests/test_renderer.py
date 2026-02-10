"""Tests for manifest rendering logic."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

import pytest
import yaml

from src.models import (
    Application,
    Destination,
    DirectoryConfig,
    HelmConfig,
    HelmFileParameter,
    HelmParameter,
    KustomizeConfig,
    Source,
    SourceType,
)
from src.renderer import (
    _build_kustomize_overlay,
    _glob_match,
    _parse_kustomize_images,
    _resolve_value_file,
    get_kube_version,
    render_application,
    render_directory,
    render_source,
)
from src.repos import RepoResolver


# Test fixture repo URL used across all test sources
_TEST_REPO_URL = "https://github.com/example/repo"


@pytest.fixture
def resolver(tmp_path):
    """RepoResolver that treats the test repo URL as local."""
    return RepoResolver(workspace=tmp_path, local_repo_url=_TEST_REPO_URL)


@pytest.fixture
def app():
    return Application(
        metadata_name="test-app",
        destination=Destination(
            server="https://kubernetes.default.svc", namespace="default"
        ),
    )


@pytest.fixture
def helm_source():
    return Source(
        repo_url="https://charts.example.com",
        chart="my-chart",
        target_revision="1.2.3",
        helm=HelmConfig(),
    )


@pytest.fixture
def kustomize_source():
    return Source(
        repo_url="https://github.com/example/repo",
        path="overlays/prod",
        kustomize=KustomizeConfig(),
    )


@pytest.fixture
def directory_source():
    return Source(
        repo_url="https://github.com/example/repo",
        path="manifests",
        directory=DirectoryConfig(),
    )


class TestGetKubeVersion:
    def test_override(self):
        assert get_kube_version("1.28.0") == "1.28.0"

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("HELM_KUBEVERSION", "1.27.0")
        assert get_kube_version() == "1.27.0"

    @patch("src.renderer.subprocess.run")
    def test_kubectl_detection(self, mock_run, monkeypatch):
        monkeypatch.delenv("HELM_KUBEVERSION", raising=False)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"clientVersion":{"major":"1","minor":"29"}}',
        )
        assert get_kube_version() == "1.29.0"

    @patch("src.renderer.subprocess.run")
    def test_fallback_default(self, mock_run, monkeypatch):
        monkeypatch.delenv("HELM_KUBEVERSION", raising=False)
        mock_run.side_effect = FileNotFoundError
        result = get_kube_version()
        assert result == "1.30.0"


class TestGlobMatch:
    def test_simple(self):
        assert _glob_match("deploy.yaml", "*.yaml")
        assert not _glob_match("deploy.json", "*.yaml")

    def test_brace_expansion(self):
        assert _glob_match("deploy.yaml", "{*.yaml,*.yml}")
        assert _glob_match("deploy.yml", "{*.yaml,*.yml}")
        assert not _glob_match("deploy.json", "{*.yaml,*.yml}")

    def test_subdirectory(self):
        assert _glob_match("subdir/deploy.yaml", "*.yaml")
        # fnmatch doesn't match across slashes by default
        assert _glob_match("subdir/deploy.yaml", "*/*.yaml")

    def test_exact_match(self):
        assert _glob_match("deploy.yaml", "deploy.yaml")
        assert not _glob_match("service.yaml", "deploy.yaml")


class TestParseKustomizeImages:
    def test_with_equals_and_tag(self):
        result = _parse_kustomize_images(["nginx=custom-nginx:latest"])
        assert result == [
            {"name": "nginx", "newName": "custom-nginx", "newTag": "latest"}
        ]

    def test_with_equals_no_tag(self):
        result = _parse_kustomize_images(["nginx=custom-nginx"])
        assert result == [{"name": "nginx", "newName": "custom-nginx"}]

    def test_tag_only(self):
        result = _parse_kustomize_images(["nginx:v2"])
        assert result == [{"name": "nginx", "newTag": "v2"}]

    def test_multiple_images(self):
        result = _parse_kustomize_images(
            [
                "nginx=my-nginx:1.0",
                "redis:7.0",
            ]
        )
        assert len(result) == 2
        assert result[0]["name"] == "nginx"
        assert result[1]["name"] == "redis"


class TestBuildKustomizeOverlay:
    def test_minimal(self):
        overlay = _build_kustomize_overlay(Path("/base"), KustomizeConfig())
        assert overlay["kind"] == "Kustomization"
        assert overlay["resources"] == ["/base"]

    def test_name_prefix_suffix(self):
        overlay = _build_kustomize_overlay(
            Path("/base"),
            KustomizeConfig(name_prefix="pre-", name_suffix="-suf"),
        )
        assert overlay["namePrefix"] == "pre-"
        assert overlay["nameSuffix"] == "-suf"

    def test_common_labels(self):
        overlay = _build_kustomize_overlay(
            Path("/base"),
            KustomizeConfig(common_labels={"app": "test"}),
        )
        assert overlay["commonLabels"] == {"app": "test"}

    def test_common_annotations(self):
        overlay = _build_kustomize_overlay(
            Path("/base"),
            KustomizeConfig(common_annotations={"note": "hello"}),
        )
        assert overlay["commonAnnotations"] == {"note": "hello"}

    def test_images(self):
        overlay = _build_kustomize_overlay(
            Path("/base"),
            KustomizeConfig(images=["nginx=my-nginx:v1"]),
        )
        assert overlay["images"] == [
            {"name": "nginx", "newName": "my-nginx", "newTag": "v1"}
        ]

    def test_namespace(self):
        overlay = _build_kustomize_overlay(
            Path("/base"),
            KustomizeConfig(namespace="prod"),
        )
        assert overlay["namespace"] == "prod"

    def test_components(self):
        overlay = _build_kustomize_overlay(
            Path("/base"),
            KustomizeConfig(components=["../components/logging"]),
        )
        assert overlay["components"] == ["../components/logging"]

    def test_patches(self):
        patches = [{"path": "patch.yaml", "target": {"kind": "Deployment"}}]
        overlay = _build_kustomize_overlay(
            Path("/base"),
            KustomizeConfig(patches=patches),
        )
        assert overlay["patches"] == patches


class TestResolveValueFile:
    def test_regular_file(self, tmp_path):
        (tmp_path / "values.yaml").write_text("key: value")
        result = _resolve_value_file("values.yaml", tmp_path, {})
        assert result is not None
        assert result.name == "values.yaml"

    def test_absolute_path(self, tmp_path):
        values = tmp_path / "values.yaml"
        values.write_text("key: value")
        result = _resolve_value_file(str(values), tmp_path, {})
        assert result is not None

    def test_missing_file(self, tmp_path):
        result = _resolve_value_file("nonexistent.yaml", tmp_path, {})
        assert result is None

    def test_ref_syntax(self, tmp_path):
        ref_dir = tmp_path / "values-repo"
        ref_dir.mkdir()
        (ref_dir / "common.yaml").write_text("key: val")

        ref_source = Source(repo_url="https://github.com/test/repo", path="values-repo")
        result = _resolve_value_file(
            "$values/common.yaml", tmp_path, {"values": ref_source}
        )
        assert result is not None
        assert result.name == "common.yaml"

    def test_ref_syntax_missing(self, tmp_path):
        result = _resolve_value_file("$missing/values.yaml", tmp_path, {})
        assert result is None

    def test_none_base_path_regular_file(self):
        """When base_path is None (chart-repo source), regular files return None."""
        result = _resolve_value_file("values.yaml", None, {})
        assert result is None

    def test_none_base_path_absolute_file(self, tmp_path):
        """Absolute paths still work even with None base_path."""
        values = tmp_path / "values.yaml"
        values.write_text("key: value")
        result = _resolve_value_file(str(values), None, {})
        assert result is not None

    def test_none_base_path_ref_with_resolver(self, tmp_path):
        """$ref files still resolve via resolver even with None base_path."""
        ref_dir = tmp_path / "ref-checkout"
        ref_dir.mkdir()
        (ref_dir / "vals.yaml").write_text("key: val")

        ref_source = Source(repo_url="https://github.com/test/values-repo")
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = ref_dir

        result = _resolve_value_file(
            "$vals/vals.yaml", None, {"vals": ref_source}, mock_resolver
        )
        assert result is not None
        assert result.name == "vals.yaml"
        mock_resolver.resolve.assert_called_once()

    def test_none_base_path_ref_missing(self):
        """$ref with missing ref name and None base_path returns None."""
        result = _resolve_value_file("$missing/values.yaml", None, {})
        assert result is None


class TestRenderDirectory:
    def test_basic(self, tmp_path, app, resolver):
        src_dir = tmp_path / "manifests"
        src_dir.mkdir()
        (src_dir / "deploy.yaml").write_text("kind: Deployment")
        (src_dir / "service.yaml").write_text("kind: Service")

        source = Source(
            repo_url="https://github.com/example/repo",
            path="manifests",
            directory=DirectoryConfig(),
        )
        output = tmp_path / "output"
        render_directory(source, app, output, resolver)

        assert (output / "deploy.yaml").exists()
        assert (output / "service.yaml").exists()

    def test_ignore_non_yaml(self, tmp_path, app, resolver):
        src_dir = tmp_path / "manifests"
        src_dir.mkdir()
        (src_dir / "deploy.yaml").write_text("kind: Deployment")
        (src_dir / "readme.md").write_text("# README")
        (src_dir / "script.sh").write_text("#!/bin/bash")

        source = Source(
            repo_url="https://github.com/example/repo",
            path="manifests",
            directory=DirectoryConfig(),
        )
        output = tmp_path / "output"
        render_directory(source, app, output, resolver)

        assert (output / "deploy.yaml").exists()
        assert not (output / "readme.md").exists()
        assert not (output / "script.sh").exists()

    def test_recurse(self, tmp_path, app, resolver):
        src_dir = tmp_path / "manifests"
        sub = src_dir / "subdir"
        sub.mkdir(parents=True)
        (src_dir / "deploy.yaml").write_text("kind: Deployment")
        (sub / "nested.yaml").write_text("kind: Service")

        source = Source(
            repo_url="https://github.com/example/repo",
            path="manifests",
            directory=DirectoryConfig(recurse=True),
        )
        output = tmp_path / "output"
        render_directory(source, app, output, resolver)

        assert (output / "deploy.yaml").exists()
        assert (output / "subdir" / "nested.yaml").exists()

    def test_no_recurse(self, tmp_path, app, resolver):
        src_dir = tmp_path / "manifests"
        sub = src_dir / "subdir"
        sub.mkdir(parents=True)
        (src_dir / "deploy.yaml").write_text("kind: Deployment")
        (sub / "nested.yaml").write_text("kind: Service")

        source = Source(
            repo_url="https://github.com/example/repo",
            path="manifests",
            directory=DirectoryConfig(recurse=False),
        )
        output = tmp_path / "output"
        render_directory(source, app, output, resolver)

        assert (output / "deploy.yaml").exists()
        # Subdirectory itself is not a file, so nested.yaml not collected
        assert not (output / "subdir" / "nested.yaml").exists()

    def test_include_pattern(self, tmp_path, app, resolver):
        src_dir = tmp_path / "manifests"
        src_dir.mkdir()
        (src_dir / "deploy.yaml").write_text("kind: Deployment")
        (src_dir / "service.yaml").write_text("kind: Service")

        source = Source(
            repo_url="https://github.com/example/repo",
            path="manifests",
            directory=DirectoryConfig(include="deploy*"),
        )
        output = tmp_path / "output"
        render_directory(source, app, output, resolver)

        assert (output / "deploy.yaml").exists()
        assert not (output / "service.yaml").exists()

    def test_exclude_pattern(self, tmp_path, app, resolver):
        src_dir = tmp_path / "manifests"
        src_dir.mkdir()
        (src_dir / "deploy.yaml").write_text("kind: Deployment")
        (src_dir / "service.yaml").write_text("kind: Service")

        source = Source(
            repo_url="https://github.com/example/repo",
            path="manifests",
            directory=DirectoryConfig(exclude="service*"),
        )
        output = tmp_path / "output"
        render_directory(source, app, output, resolver)

        assert (output / "deploy.yaml").exists()
        assert not (output / "service.yaml").exists()

    def test_skip_rendering_directive(self, tmp_path, app, resolver):
        src_dir = tmp_path / "manifests"
        src_dir.mkdir()
        (src_dir / "deploy.yaml").write_text("kind: Deployment")
        (src_dir / "skip.yaml").write_text(
            "# +argocd:skip-file-rendering\nkind: Secret"
        )

        source = Source(
            repo_url="https://github.com/example/repo",
            path="manifests",
            directory=DirectoryConfig(),
        )
        output = tmp_path / "output"
        render_directory(source, app, output, resolver)

        assert (output / "deploy.yaml").exists()
        assert not (output / "skip.yaml").exists()

    def test_missing_path_error(self, tmp_path, app, resolver):
        source = Source(
            repo_url="https://github.com/example/repo",
            path="nonexistent",
            directory=DirectoryConfig(),
        )
        with pytest.raises(FileNotFoundError):
            render_directory(source, app, tmp_path / "output", resolver)

    def test_json_files_included(self, tmp_path, app, resolver):
        src_dir = tmp_path / "manifests"
        src_dir.mkdir()
        (src_dir / "deploy.json").write_text('{"kind": "Deployment"}')

        source = Source(
            repo_url="https://github.com/example/repo",
            path="manifests",
            directory=DirectoryConfig(),
        )
        output = tmp_path / "output"
        render_directory(source, app, output, resolver)

        assert (output / "deploy.json").exists()


class TestRenderSource:
    def test_ref_only_source_skipped(self, tmp_path, app, resolver):
        """Ref-only sources should be skipped."""
        source = Source(
            repo_url="https://github.com/example/values-repo",
            ref="values",
        )
        output = tmp_path / "output"
        output.mkdir()
        render_source(source, app, output, resolver)
        # No files should be created
        assert not list(output.iterdir())

    def test_chart_repo_source_no_clone(self, tmp_path, app):
        """Helm chart-repo sources must NOT trigger resolver.resolve()."""
        source = Source(
            repo_url="https://bitnami-labs.github.io/sealed-secrets",
            chart="sealed-secrets",
            target_revision="2.18.0",
            helm=HelmConfig(),
        )
        mock_resolver = MagicMock(spec=RepoResolver)
        output = tmp_path / "output"

        with patch("src.renderer.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            render_source(source, app, output, mock_resolver)

        # resolver.resolve() should NEVER be called for chart-repo sources
        mock_resolver.resolve.assert_not_called()

    def test_chart_repo_source_with_value_files_no_clone(self, tmp_path, app):
        """Helm chart-repo with valueFiles must NOT clone the chart repo URL."""
        source = Source(
            repo_url="https://guerzon.github.io/vaultwarden",
            chart="vaultwarden",
            target_revision="0.34.5",
            helm=HelmConfig(
                value_files=["values.yaml"],
                ignore_missing_value_files=True,
            ),
        )
        mock_resolver = MagicMock(spec=RepoResolver)
        output = tmp_path / "output"

        with patch("src.renderer.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            render_source(source, app, output, mock_resolver)

        mock_resolver.resolve.assert_not_called()

    def test_directory_source_dispatched(self, tmp_path, app, resolver):
        src_dir = tmp_path / "manifests"
        src_dir.mkdir()
        (src_dir / "deploy.yaml").write_text("kind: Deployment")

        source = Source(
            repo_url="https://github.com/example/repo",
            path="manifests",
            directory=DirectoryConfig(),
        )
        output = tmp_path / "output"
        render_source(source, app, output, resolver)
        assert (output / "deploy.yaml").exists()

    def test_autodetect_directory(self, tmp_path, app, resolver):
        """If no explicit config, should detect type from filesystem."""
        src_dir = tmp_path / "plain-manifests"
        src_dir.mkdir()
        (src_dir / "deploy.yaml").write_text("kind: Deployment")

        source = Source(
            repo_url="https://github.com/example/repo",
            path="plain-manifests",
        )
        output = tmp_path / "output"
        render_source(source, app, output, resolver)
        assert (output / "deploy.yaml").exists()

    def test_autodetect_kustomize(self, tmp_path, app):
        """Kustomize detected from kustomization.yaml presence."""
        kust_dir = tmp_path / "kust-app"
        kust_dir.mkdir()
        (kust_dir / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []"
        )

        source = Source(
            repo_url="https://github.com/example/repo",
            path="kust-app",
        )
        # This will try to actually run kustomize, so we just test type detection
        assert source.detect_type(kust_dir) == SourceType.KUSTOMIZE

    def test_autodetect_helm(self, tmp_path, app):
        """Helm detected from Chart.yaml presence."""
        helm_dir = tmp_path / "helm-app"
        helm_dir.mkdir()
        (helm_dir / "Chart.yaml").write_text("apiVersion: v2\nname: test")

        source = Source(
            repo_url="https://github.com/example/repo",
            path="helm-app",
        )
        assert source.detect_type(helm_dir) == SourceType.HELM


class TestRenderApplication:
    def test_single_source_directory(self, tmp_path, resolver):
        src_dir = tmp_path / "manifests"
        src_dir.mkdir()
        (src_dir / "deploy.yaml").write_text("kind: Deployment")

        app = Application(
            metadata_name="my-app",
            sources=[
                Source(
                    repo_url="https://github.com/example/repo",
                    path="manifests",
                    directory=DirectoryConfig(),
                )
            ],
            destination=Destination(namespace="default"),
        )

        output = tmp_path / "output"
        render_application(app, output, resolver)

        assert (output / "my-app" / "deploy.yaml").exists()

    def test_multi_source_with_ref(self, tmp_path, resolver):
        """Multi-source with a ref source for values - ref is skipped."""
        src_dir = tmp_path / "manifests"
        src_dir.mkdir()
        (src_dir / "deploy.yaml").write_text("kind: Deployment")

        app = Application(
            metadata_name="multi-app",
            sources=[
                Source(
                    repo_url="https://github.com/example/values",
                    ref="values",
                ),
                Source(
                    repo_url="https://github.com/example/repo",
                    path="manifests",
                    directory=DirectoryConfig(),
                ),
            ],
            destination=Destination(namespace="default"),
        )

        output = tmp_path / "output"
        render_application(app, output, resolver)

        assert (output / "multi-app" / "deploy.yaml").exists()

    def test_empty_sources(self, tmp_path, resolver):
        app = Application(
            metadata_name="empty-app",
            sources=[],
            destination=Destination(namespace="default"),
        )
        output = tmp_path / "output"
        render_application(app, output, resolver)
        assert (output / "empty-app").is_dir()
