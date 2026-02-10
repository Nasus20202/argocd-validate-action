"""Tests for ArgoCD manifest validator."""

import textwrap
from pathlib import Path

import pytest
import yaml

from src.validator import (
    ValidationResult,
    ValidationSummary,
    validate_argo_manifest,
    validate_argo_manifests_dir,
    _validate_application,
    _validate_application_set,
    _validate_source,
)


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


class TestValidationResult:
    def test_default_valid(self):
        r = ValidationResult(file_path="test.yaml")
        assert r.valid is True
        assert r.errors == []

    def test_add_error(self):
        r = ValidationResult(file_path="test.yaml")
        r.add_error("something went wrong")
        assert r.valid is False
        assert len(r.errors) == 1

    def test_add_warning(self):
        r = ValidationResult(file_path="test.yaml")
        r.add_warning("minor issue")
        assert r.valid is True  # warnings don't invalidate
        assert len(r.warnings) == 1


class TestValidationSummary:
    def test_empty_summary(self):
        s = ValidationSummary()
        assert s.success is True
        assert s.total == 0

    def test_add_results(self):
        s = ValidationSummary()
        s.add(ValidationResult(file_path="a.yaml"))
        s.add(ValidationResult(file_path="b.yaml"))
        assert s.total == 2
        assert s.valid == 2
        assert s.invalid == 0
        assert s.success is True

    def test_invalid_result(self):
        s = ValidationSummary()
        r = ValidationResult(file_path="bad.yaml")
        r.add_error("fail")
        s.add(r)
        assert s.success is False
        assert s.invalid == 1

    def test_format_text(self):
        s = ValidationSummary()
        r = ValidationResult(
            file_path="test.yaml",
            resource_name="my-app",
            resource_kind="Application",
        )
        r.add_error("missing field")
        s.add(r)
        text = s.format_text()
        assert "INVALID" in text
        assert "my-app" in text
        assert "missing field" in text


class TestValidateArgoManifest:
    def test_valid_application(self, tmp_dir):
        f = tmp_dir / "app.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "my-app"},
                    "spec": {
                        "project": "default",
                        "source": {
                            "repoURL": "https://charts.example.com",
                            "chart": "nginx",
                            "targetRevision": "1.0.0",
                        },
                        "destination": {
                            "server": "https://kubernetes.default.svc",
                            "namespace": "default",
                        },
                    },
                }
            )
        )

        results = validate_argo_manifest(f)
        assert len(results) == 1
        assert results[0].valid is True

    def test_application_missing_source(self, tmp_dir):
        f = tmp_dir / "app.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "bad-app"},
                    "spec": {
                        "destination": {"namespace": "default"},
                    },
                }
            )
        )

        results = validate_argo_manifest(f)
        assert len(results) == 1
        assert results[0].valid is False
        assert any("source" in e.lower() for e in results[0].errors)

    def test_application_missing_destination(self, tmp_dir):
        f = tmp_dir / "app.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "bad-app"},
                    "spec": {
                        "source": {
                            "repoURL": "https://example.com",
                            "path": "k8s",
                        },
                    },
                }
            )
        )

        results = validate_argo_manifest(f)
        assert len(results) == 1
        assert results[0].valid is False
        assert any("destination" in e.lower() for e in results[0].errors)

    def test_application_both_source_and_sources(self, tmp_dir):
        f = tmp_dir / "app.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "dual-app"},
                    "spec": {
                        "source": {"repoURL": "https://example.com", "path": "k8s"},
                        "sources": [{"repoURL": "https://example.com", "path": "k8s"}],
                        "destination": {
                            "server": "https://kubernetes.default.svc",
                            "namespace": "default",
                        },
                    },
                }
            )
        )

        results = validate_argo_manifest(f)
        assert len(results) == 1
        assert any("both" in w.lower() for w in results[0].warnings)

    def test_source_chart_and_path(self, tmp_dir):
        f = tmp_dir / "app.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "bad-source"},
                    "spec": {
                        "source": {
                            "repoURL": "https://example.com",
                            "chart": "nginx",
                            "path": "charts/nginx",
                        },
                        "destination": {
                            "server": "https://kubernetes.default.svc",
                            "namespace": "default",
                        },
                    },
                }
            )
        )

        results = validate_argo_manifest(f)
        assert results[0].valid is False

    def test_source_missing_repo_url(self, tmp_dir):
        f = tmp_dir / "app.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "no-repo"},
                    "spec": {
                        "source": {"path": "k8s"},
                        "destination": {"namespace": "default"},
                    },
                }
            )
        )

        results = validate_argo_manifest(f)
        assert results[0].valid is False

    def test_helm_chart_no_version_warns(self, tmp_dir):
        f = tmp_dir / "app.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "no-ver"},
                    "spec": {
                        "source": {
                            "repoURL": "https://charts.example.com",
                            "chart": "nginx",
                        },
                        "destination": {
                            "server": "https://kubernetes.default.svc",
                            "namespace": "default",
                        },
                    },
                }
            )
        )

        results = validate_argo_manifest(f)
        assert any("targetRevision" in w for w in results[0].warnings)

    def test_valid_application_set(self, tmp_dir):
        f = tmp_dir / "appset.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationSet",
                    "metadata": {"name": "my-appset"},
                    "spec": {
                        "generators": [{"list": {"elements": [{"name": "app1"}]}}],
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

        results = validate_argo_manifest(f)
        assert len(results) == 1
        assert results[0].valid is True

    def test_appset_missing_generators(self, tmp_dir):
        f = tmp_dir / "appset.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationSet",
                    "metadata": {"name": "bad-appset"},
                    "spec": {
                        "template": {
                            "metadata": {"name": "test"},
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

        results = validate_argo_manifest(f)
        assert results[0].valid is False

    def test_appset_missing_template(self, tmp_dir):
        f = tmp_dir / "appset.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationSet",
                    "metadata": {"name": "no-template"},
                    "spec": {
                        "generators": [{"list": {"elements": [{"name": "a"}]}}],
                    },
                }
            )
        )

        results = validate_argo_manifest(f)
        assert results[0].valid is False

    def test_appset_git_missing_dirs_and_files(self, tmp_dir):
        f = tmp_dir / "appset.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationSet",
                    "metadata": {"name": "bad-git"},
                    "spec": {
                        "generators": [{"git": {"repoURL": "https://example.com"}}],
                        "template": {
                            "metadata": {"name": "test"},
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

        results = validate_argo_manifest(f)
        assert results[0].valid is False

    def test_appset_matrix_insufficient_generators(self, tmp_dir):
        f = tmp_dir / "appset.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationSet",
                    "metadata": {"name": "bad-matrix"},
                    "spec": {
                        "generators": [
                            {
                                "matrix": {
                                    "generators": [
                                        {"list": {"elements": [{"name": "a"}]}}
                                    ]
                                }
                            }
                        ],
                        "template": {
                            "metadata": {"name": "test"},
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

        results = validate_argo_manifest(f)
        assert results[0].valid is False

    def test_invalid_yaml_syntax(self, tmp_dir):
        f = tmp_dir / "bad.yaml"
        f.write_text("{{invalid: yaml")
        results = validate_argo_manifest(f)
        assert len(results) == 1
        assert results[0].valid is False

    def test_non_argo_resource_skipped(self, tmp_dir):
        f = tmp_dir / "deploy.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "test"},
                }
            )
        )

        results = validate_argo_manifest(f)
        assert len(results) == 0

    def test_ref_source_without_repo_url(self, tmp_dir):
        """Ref-only sources should not require repoURL."""
        f = tmp_dir / "app.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "multi-src"},
                    "spec": {
                        "sources": [
                            {
                                "repoURL": "https://charts.example.com",
                                "chart": "nginx",
                                "targetRevision": "1.0.0",
                                "helm": {"valueFiles": ["$values/values.yaml"]},
                            },
                            {
                                "repoURL": "https://github.com/example/values",
                                "ref": "values",
                                "targetRevision": "main",
                            },
                        ],
                        "destination": {
                            "server": "https://kubernetes.default.svc",
                            "namespace": "default",
                        },
                    },
                }
            )
        )
        results = validate_argo_manifest(f)
        assert results[0].valid is True

    def test_helm_values_and_values_object_warns(self, tmp_dir):
        f = tmp_dir / "app.yaml"
        f.write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "both-values"},
                    "spec": {
                        "source": {
                            "repoURL": "https://charts.example.com",
                            "chart": "app",
                            "targetRevision": "1.0.0",
                            "helm": {
                                "values": "foo: bar",
                                "valuesObject": {"foo": "baz"},
                            },
                        },
                        "destination": {
                            "server": "https://kubernetes.default.svc",
                            "namespace": "default",
                        },
                    },
                }
            )
        )
        results = validate_argo_manifest(f)
        assert any("valuesObject" in w for w in results[0].warnings)


class TestValidateArgoManifestsDir:
    def test_validates_all_files(self, tmp_dir):
        apps_dir = tmp_dir / "apps"
        apps_dir.mkdir()

        for i in range(3):
            (apps_dir / f"app{i}.yaml").write_text(
                yaml.dump(
                    {
                        "apiVersion": "argoproj.io/v1alpha1",
                        "kind": "Application",
                        "metadata": {"name": f"app{i}"},
                        "spec": {
                            "source": {
                                "repoURL": "https://example.com",
                                "path": f"k8s/app{i}",
                            },
                            "destination": {
                                "server": "https://kubernetes.default.svc",
                                "namespace": "default",
                            },
                        },
                    }
                )
            )

        summary = validate_argo_manifests_dir(apps_dir)
        assert summary.total == 3
        assert summary.success is True

    def test_skip_files(self, tmp_dir):
        apps_dir = tmp_dir / "apps"
        apps_dir.mkdir()

        (apps_dir / "app.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "app"},
                    "spec": {
                        "source": {"repoURL": "https://example.com", "path": "k8s"},
                        "destination": {
                            "server": "https://kubernetes.default.svc",
                            "namespace": "default",
                        },
                    },
                }
            )
        )
        (apps_dir / "skip.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {"name": "skip"},
                    "spec": {},  # invalid but should be skipped
                }
            )
        )

        summary = validate_argo_manifests_dir(apps_dir, skip_files=["skip.yaml"])
        assert summary.total == 1
        assert summary.skipped == 1

    def test_nonexistent_dir(self, tmp_dir):
        summary = validate_argo_manifests_dir(tmp_dir / "nonexistent")
        assert summary.success is False
