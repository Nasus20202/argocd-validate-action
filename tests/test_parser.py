"""Tests for ArgoCD manifest parser."""

import textwrap
import tempfile
from pathlib import Path

import pytest
import yaml

from src.parser import (
    discover_argo_manifests,
    load_yaml_file,
    parse_application,
    parse_application_set,
    parse_helm_config,
    parse_kustomize_config,
    parse_source,
)
from src.models import SourceType


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


class TestParseSource:
    def test_helm_chart_source(self):
        raw = {
            "repoURL": "https://charts.example.com",
            "chart": "nginx",
            "targetRevision": "1.0.0",
            "helm": {
                "releaseName": "my-release",
                "valueFiles": ["values.yaml"],
                "parameters": [{"name": "replicas", "value": "3"}],
            },
        }
        source = parse_source(raw)
        assert source.chart == "nginx"
        assert source.repo_url == "https://charts.example.com"
        assert source.target_revision == "1.0.0"
        assert source.helm is not None
        assert source.helm.release_name == "my-release"
        assert source.helm.value_files == ["values.yaml"]
        assert len(source.helm.parameters) == 1
        assert source.helm.parameters[0].name == "replicas"
        assert source.helm.parameters[0].value == "3"

    def test_path_source(self):
        raw = {
            "repoURL": "https://github.com/example/repo",
            "path": "manifests/app1",
            "targetRevision": "main",
        }
        source = parse_source(raw)
        assert source.path == "manifests/app1"
        assert source.chart is None
        assert source.target_revision == "main"

    def test_ref_source(self):
        raw = {
            "repoURL": "https://github.com/example/values",
            "ref": "values",
            "targetRevision": "main",
        }
        source = parse_source(raw)
        assert source.ref == "values"
        assert source.chart is None

    def test_kustomize_source(self):
        raw = {
            "repoURL": "https://github.com/example/repo",
            "path": "k8s/app",
            "kustomize": {
                "namePrefix": "dev-",
                "images": ["nginx=nginx:1.25"],
                "commonLabels": {"env": "dev"},
            },
        }
        source = parse_source(raw)
        assert source.kustomize is not None
        assert source.kustomize.name_prefix == "dev-"
        assert source.kustomize.images == ["nginx=nginx:1.25"]
        assert source.kustomize.common_labels == {"env": "dev"}

    def test_directory_source(self):
        raw = {
            "repoURL": "https://github.com/example/repo",
            "path": "manifests/",
            "directory": {
                "recurse": True,
                "include": "*.yaml",
                "exclude": "test-*",
            },
        }
        source = parse_source(raw)
        assert source.directory is not None
        assert source.directory.recurse is True
        assert source.directory.include == "*.yaml"
        assert source.directory.exclude == "test-*"

    def test_plugin_source(self):
        raw = {
            "repoURL": "https://github.com/example/repo",
            "path": "app/",
            "plugin": {
                "name": "my-plugin",
                "env": [{"name": "FOO", "value": "bar"}],
            },
        }
        source = parse_source(raw)
        assert source.plugin is not None
        assert source.plugin.name == "my-plugin"

    def test_helm_with_force_string(self):
        raw = {
            "repoURL": "https://charts.example.com",
            "chart": "app",
            "targetRevision": "1.0.0",
            "helm": {
                "parameters": [
                    {"name": "image.tag", "value": "123", "forceString": True}
                ],
            },
        }
        source = parse_source(raw)
        assert source.helm.parameters[0].force_string is True

    def test_helm_file_parameters(self):
        raw = {
            "repoURL": "https://charts.example.com",
            "chart": "app",
            "targetRevision": "1.0.0",
            "helm": {
                "fileParameters": [{"name": "config", "path": "config.json"}],
            },
        }
        source = parse_source(raw)
        assert len(source.helm.file_parameters) == 1
        assert source.helm.file_parameters[0].name == "config"

    def test_helm_values_object(self):
        raw = {
            "repoURL": "https://charts.example.com",
            "chart": "app",
            "targetRevision": "1.0.0",
            "helm": {
                "valuesObject": {"replicas": 3, "image": {"tag": "latest"}},
            },
        }
        source = parse_source(raw)
        assert source.helm.values_object == {"replicas": 3, "image": {"tag": "latest"}}

    def test_helm_inline_values(self):
        raw = {
            "repoURL": "https://charts.example.com",
            "chart": "app",
            "targetRevision": "1.0.0",
            "helm": {
                "values": "replicas: 3\nimage:\n  tag: latest",
            },
        }
        source = parse_source(raw)
        assert "replicas: 3" in source.helm.values

    def test_helm_skip_crds(self):
        raw = {
            "repoURL": "https://charts.example.com",
            "chart": "app",
            "targetRevision": "1.0.0",
            "helm": {"skipCrds": True, "skipTests": True},
        }
        source = parse_source(raw)
        assert source.helm.skip_crds is True
        assert source.helm.skip_tests is True

    def test_helm_api_versions(self):
        raw = {
            "repoURL": "https://charts.example.com",
            "chart": "app",
            "targetRevision": "1.0.0",
            "helm": {"apiVersions": ["monitoring.coreos.com/v1"]},
        }
        source = parse_source(raw)
        assert source.helm.api_versions == ["monitoring.coreos.com/v1"]

    def test_kustomize_replicas(self):
        raw = {
            "repoURL": "https://github.com/example/repo",
            "path": "k8s/app",
            "kustomize": {
                "replicas": [{"name": "my-deploy", "count": 5}],
            },
        }
        source = parse_source(raw)
        assert source.kustomize.replicas == [{"name": "my-deploy", "count": 5}]

    def test_kustomize_patches(self):
        raw = {
            "repoURL": "https://github.com/example/repo",
            "path": "k8s/app",
            "kustomize": {
                "patches": [
                    {
                        "target": {"kind": "Deployment", "name": "my-deploy"},
                        "patch": "- op: replace\n  path: /spec/replicas\n  value: 3",
                    }
                ],
            },
        }
        source = parse_source(raw)
        assert len(source.kustomize.patches) == 1

    def test_kustomize_components(self):
        raw = {
            "repoURL": "https://github.com/example/repo",
            "path": "k8s/app",
            "kustomize": {
                "components": ["components/monitoring"],
            },
        }
        source = parse_source(raw)
        assert source.kustomize.components == ["components/monitoring"]


class TestSourceTypeDetection:
    def test_chart_is_helm(self):
        source = parse_source({"chart": "nginx", "repoURL": "https://example.com"})
        assert source.detect_type() == SourceType.HELM

    def test_explicit_helm_config(self):
        source = parse_source(
            {
                "repoURL": "https://example.com",
                "path": "charts/app",
                "helm": {"values": "foo: bar"},
            }
        )
        assert source.detect_type() == SourceType.HELM

    def test_explicit_kustomize_config(self):
        source = parse_source(
            {
                "repoURL": "https://example.com",
                "path": "k8s/app",
                "kustomize": {"namePrefix": "dev-"},
            }
        )
        assert source.detect_type() == SourceType.KUSTOMIZE

    def test_explicit_directory_config(self):
        source = parse_source(
            {
                "repoURL": "https://example.com",
                "path": "manifests/",
                "directory": {"recurse": True},
            }
        )
        assert source.detect_type() == SourceType.DIRECTORY

    def test_explicit_plugin(self):
        source = parse_source(
            {
                "repoURL": "https://example.com",
                "path": "app/",
                "plugin": {"name": "my-plugin"},
            }
        )
        assert source.detect_type() == SourceType.PLUGIN

    def test_auto_detect_helm(self, tmp_dir):
        chart_dir = tmp_dir / "chart"
        chart_dir.mkdir()
        (chart_dir / "Chart.yaml").write_text("name: test")
        source = parse_source(
            {
                "repoURL": "https://example.com",
                "path": "chart",
            }
        )
        assert source.detect_type(chart_dir) == SourceType.HELM

    def test_auto_detect_kustomize(self, tmp_dir):
        kust_dir = tmp_dir / "k8s"
        kust_dir.mkdir()
        (kust_dir / "kustomization.yaml").write_text("resources: []")
        source = parse_source(
            {
                "repoURL": "https://example.com",
                "path": "k8s",
            }
        )
        assert source.detect_type(kust_dir) == SourceType.KUSTOMIZE

    def test_auto_detect_kustomize_yml(self, tmp_dir):
        kust_dir = tmp_dir / "k8s"
        kust_dir.mkdir()
        (kust_dir / "kustomization.yml").write_text("resources: []")
        source = parse_source(
            {
                "repoURL": "https://example.com",
                "path": "k8s",
            }
        )
        assert source.detect_type(kust_dir) == SourceType.KUSTOMIZE

    def test_default_directory(self, tmp_dir):
        plain_dir = tmp_dir / "manifests"
        plain_dir.mkdir()
        source = parse_source(
            {
                "repoURL": "https://example.com",
                "path": "manifests",
            }
        )
        assert source.detect_type(plain_dir) == SourceType.DIRECTORY


class TestParseApplication:
    def test_single_source_app(self):
        data = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {"name": "my-app", "namespace": "argocd"},
            "spec": {
                "source": {
                    "repoURL": "https://charts.example.com",
                    "chart": "nginx",
                    "targetRevision": "1.0.0",
                },
                "destination": {
                    "server": "https://kubernetes.default.svc",
                    "namespace": "default",
                },
                "project": "my-project",
            },
        }
        app = parse_application(data)
        assert app.metadata_name == "my-app"
        assert app.metadata_namespace == "argocd"
        assert app.project == "my-project"
        assert len(app.sources) == 1
        assert app.source.chart == "nginx"
        assert app.destination.namespace == "default"
        assert not app.is_multi_source

    def test_multi_source_app(self):
        data = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {"name": "multi-app"},
            "spec": {
                "sources": [
                    {
                        "repoURL": "https://charts.example.com",
                        "chart": "nginx",
                        "targetRevision": "1.0.0",
                        "helm": {
                            "valueFiles": ["$values/values.yaml"],
                        },
                    },
                    {
                        "repoURL": "https://github.com/example/values",
                        "ref": "values",
                        "targetRevision": "main",
                    },
                ],
                "destination": {"namespace": "web"},
            },
        }
        app = parse_application(data)
        assert app.metadata_name == "multi-app"
        assert app.is_multi_source
        assert len(app.sources) == 2
        assert app.sources[0].chart == "nginx"
        assert app.sources[1].ref == "values"

    def test_app_defaults(self):
        data = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {"name": "minimal"},
            "spec": {
                "source": {"repoURL": "https://example.com", "path": "k8s"},
                "destination": {},
            },
        }
        app = parse_application(data)
        assert app.project == "default"
        assert app.destination.namespace == "default"


class TestParseApplicationSet:
    def test_basic_appset(self):
        data = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationSet",
            "metadata": {"name": "my-appset", "namespace": "argocd"},
            "spec": {
                "generators": [
                    {
                        "git": {
                            "repoURL": "https://github.com/example/repo",
                            "revision": "main",
                            "directories": [{"path": "apps/*"}],
                        }
                    }
                ],
                "template": {
                    "metadata": {"name": "{{path.basename}}"},
                    "spec": {
                        "source": {
                            "repoURL": "https://github.com/example/repo",
                            "path": "{{path}}",
                        },
                        "destination": {"namespace": "default"},
                    },
                },
            },
        }
        appset = parse_application_set(data)
        assert appset.metadata_name == "my-appset"
        assert len(appset.generators) == 1
        assert "git" in appset.generators[0]

    def test_go_template_appset(self):
        data = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationSet",
            "metadata": {"name": "go-appset"},
            "spec": {
                "goTemplate": True,
                "goTemplateOptions": ["missingkey=error"],
                "generators": [{"list": {"elements": [{"name": "app1"}]}}],
                "template": {
                    "metadata": {"name": "{{.name}}"},
                    "spec": {
                        "source": {
                            "repoURL": "https://example.com",
                            "path": "apps/{{.name}}",
                        },
                        "destination": {"namespace": "default"},
                    },
                },
            },
        }
        appset = parse_application_set(data)
        assert appset.go_template is True
        assert appset.go_template_options == ["missingkey=error"]


class TestDiscoverManifests:
    def test_discover_applications(self, tmp_dir):
        apps_dir = tmp_dir / "apps"
        apps_dir.mkdir()

        app_yaml = apps_dir / "app1.yaml"
        app_yaml.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "app1"},
                    "spec": {
                        "source": {"repoURL": "https://example.com", "path": "k8s"},
                        "destination": {"namespace": "default"},
                    },
                }
            )
        )

        apps, appsets = discover_argo_manifests(apps_dir)
        assert len(apps) == 1
        assert len(appsets) == 0
        assert apps[0].metadata_name == "app1"

    def test_discover_application_sets(self, tmp_dir):
        apps_dir = tmp_dir / "apps"
        apps_dir.mkdir()

        appset_yaml = apps_dir / "appset.yaml"
        appset_yaml.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationSet",
                    "metadata": {"name": "my-appset"},
                    "spec": {
                        "generators": [{"list": {"elements": [{"name": "a"}]}}],
                        "template": {
                            "metadata": {"name": "{{name}}"},
                            "spec": {
                                "source": {
                                    "repoURL": "https://example.com",
                                    "path": "k8s",
                                },
                                "destination": {"namespace": "default"},
                            },
                        },
                    },
                }
            )
        )

        apps, appsets = discover_argo_manifests(apps_dir)
        assert len(apps) == 0
        assert len(appsets) == 1

    def test_skip_files(self, tmp_dir):
        apps_dir = tmp_dir / "apps"
        apps_dir.mkdir()

        for name in ["app1.yaml", "skip-me.yaml"]:
            (apps_dir / name).write_text(
                yaml.dump(
                    {
                        "apiVersion": "argoproj.io/v1alpha1",
                        "kind": "Application",
                        "metadata": {"name": name.replace(".yaml", "")},
                        "spec": {
                            "source": {"repoURL": "https://example.com", "path": "k8s"},
                            "destination": {"namespace": "default"},
                        },
                    }
                )
            )

        apps, _ = discover_argo_manifests(apps_dir, skip_files=["skip-me.yaml"])
        assert len(apps) == 1
        assert apps[0].metadata_name == "app1"

    def test_multi_document_yaml(self, tmp_dir):
        apps_dir = tmp_dir / "apps"
        apps_dir.mkdir()

        content = textwrap.dedent(
            """\
            apiVersion: argoproj.io/v1alpha1
            kind: Application
            metadata:
              name: app1
            spec:
              source:
                repoURL: https://example.com
                path: k8s/app1
              destination:
                namespace: default
            ---
            apiVersion: argoproj.io/v1alpha1
            kind: Application
            metadata:
              name: app2
            spec:
              source:
                repoURL: https://example.com
                path: k8s/app2
              destination:
                namespace: default
        """
        )
        (apps_dir / "apps.yaml").write_text(content)

        apps, _ = discover_argo_manifests(apps_dir)
        assert len(apps) == 2
        names = {a.metadata_name for a in apps}
        assert names == {"app1", "app2"}

    def test_nonexistent_dir(self, tmp_dir):
        apps, appsets = discover_argo_manifests(tmp_dir / "nonexistent")
        assert apps == []
        assert appsets == []

    def test_ignores_non_argo_resources(self, tmp_dir):
        apps_dir = tmp_dir / "apps"
        apps_dir.mkdir()

        (apps_dir / "deployment.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "my-deploy"},
                    "spec": {"replicas": 1},
                }
            )
        )

        apps, appsets = discover_argo_manifests(apps_dir)
        assert len(apps) == 0
        assert len(appsets) == 0


class TestLoadYamlFile:
    def test_valid_yaml(self, tmp_dir):
        f = tmp_dir / "test.yaml"
        f.write_text("foo: bar\n")
        docs = load_yaml_file(f)
        assert len(docs) == 1
        assert docs[0]["foo"] == "bar"

    def test_invalid_yaml(self, tmp_dir):
        f = tmp_dir / "bad.yaml"
        f.write_text("{{invalid: yaml: stuff")
        docs = load_yaml_file(f)
        assert docs == []

    def test_nonexistent_file(self, tmp_dir):
        docs = load_yaml_file(tmp_dir / "missing.yaml")
        assert docs == []

    def test_empty_file(self, tmp_dir):
        f = tmp_dir / "empty.yaml"
        f.write_text("")
        docs = load_yaml_file(f)
        assert docs == []
