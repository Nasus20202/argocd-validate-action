"""Integration tests for the full pipeline.

These tests exercise the main entry point end-to-end with real fixture files,
covering: ArgoCD validation, manifest discovery, ApplicationSet expansion,
directory rendering, diff/state management.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from src.main import main


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())


@pytest.fixture()
def sample_fixtures(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """Create a full set of sample ArgoCD fixtures for integration testing.

    Returns a dict with paths: apps_dir, workspace, manifests_dir, state_dir.
    Automatically sets cwd and GITHUB_REPOSITORY so the RepoResolver detects
    the fixture repoURL as the local repository.
    """
    apps_dir = tmp_path / "apps"
    workspace = tmp_path / "repo"
    manifests_dir = tmp_path / "manifests"
    state_dir = tmp_path / "state"

    # Ensure workspace exists before chdir
    workspace.mkdir(parents=True, exist_ok=True)

    # Configure resolver to treat https://github.com/example/repo as local
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")

    # -- Application: directory source --
    _write_yaml(
        apps_dir / "app.yaml",
        {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {"name": "my-app"},
            "spec": {
                "source": {
                    "repoURL": "https://github.com/example/repo",
                    "path": "k8s/my-app",
                    "directory": {"recurse": False},
                },
                "destination": {
                    "server": "https://kubernetes.default.svc",
                    "namespace": "default",
                },
                "project": "default",
            },
        },
    )

    # -- K8s manifests for directory source --
    _write_text(
        workspace / "k8s" / "my-app" / "deployment.yaml",
        """\
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: my-app
          namespace: default
        spec:
          replicas: 1
          selector:
            matchLabels:
              app: my-app
          template:
            metadata:
              labels:
                app: my-app
            spec:
              containers:
                - name: app
                  image: nginx:1.27
                  ports:
                    - containerPort: 80
        """,
    )

    _write_text(
        workspace / "k8s" / "my-app" / "service.yaml",
        """\
        apiVersion: v1
        kind: Service
        metadata:
          name: my-app
          namespace: default
        spec:
          selector:
            app: my-app
          ports:
            - port: 80
              targetPort: 80
        """,
    )

    # -- ApplicationSet: list generator --
    _write_yaml(
        apps_dir / "appset.yaml",
        {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationSet",
            "metadata": {"name": "envs"},
            "spec": {
                "generators": [
                    {
                        "list": {
                            "elements": [
                                {"env": "dev", "namespace": "dev"},
                                {"env": "staging", "namespace": "staging"},
                            ]
                        }
                    }
                ],
                "template": {
                    "metadata": {"name": "my-app-{{env}}"},
                    "spec": {
                        "source": {
                            "repoURL": "https://github.com/example/repo",
                            "path": "k8s/my-app",
                            "directory": {"recurse": False},
                        },
                        "destination": {
                            "server": "https://kubernetes.default.svc",
                            "namespace": "{{namespace}}",
                        },
                        "project": "default",
                    },
                },
            },
        },
    )

    return {
        "apps_dir": apps_dir,
        "workspace": workspace,
        "manifests_dir": manifests_dir,
        "state_dir": state_dir,
    }


class TestIntegrationPipeline:
    """End-to-end integration tests for the full action pipeline."""

    def test_renders_application_manifests(self, sample_fixtures):
        f = sample_fixtures
        rc = main(
            [
                "--apps-dir",
                str(f["apps_dir"]),
                "--manifests-dir",
                str(f["manifests_dir"]),
            ]
        )
        assert rc == 0
        assert (f["manifests_dir"] / "my-app" / "deployment.yaml").exists()
        assert (f["manifests_dir"] / "my-app" / "service.yaml").exists()

    def test_expands_applicationset(self, sample_fixtures):
        f = sample_fixtures
        rc = main(
            [
                "--apps-dir",
                str(f["apps_dir"]),
                "--manifests-dir",
                str(f["manifests_dir"]),
            ]
        )
        assert rc == 0
        assert (f["manifests_dir"] / "my-app-dev").is_dir()
        assert (f["manifests_dir"] / "my-app-staging").is_dir()
        assert (f["manifests_dir"] / "my-app-dev" / "deployment.yaml").exists()
        assert (f["manifests_dir"] / "my-app-staging" / "service.yaml").exists()

    def test_state_initialization(self, sample_fixtures):
        f = sample_fixtures
        rc = main(
            [
                "--apps-dir",
                str(f["apps_dir"]),
                "--manifests-dir",
                str(f["manifests_dir"]),
                "--state-dir",
                str(f["state_dir"]),
            ]
        )
        assert rc == 0
        # State dir should be populated after first run
        assert any(f["state_dir"].iterdir())
        assert (f["state_dir"] / "my-app" / "deployment.yaml").exists()

    def test_second_run_unchanged(self, sample_fixtures):
        f = sample_fixtures
        # First run: initializes state
        rc1 = main(
            [
                "--apps-dir",
                str(f["apps_dir"]),
                "--manifests-dir",
                str(f["manifests_dir"]),
                "--state-dir",
                str(f["state_dir"]),
            ]
        )
        assert rc1 == 0

        # Second run: should detect no changes
        rc2 = main(
            [
                "--apps-dir",
                str(f["apps_dir"]),
                "--manifests-dir",
                str(f["manifests_dir"]),
                "--state-dir",
                str(f["state_dir"]),
            ]
        )
        assert rc2 == 0

    def test_diff_detected_after_change(self, sample_fixtures):
        f = sample_fixtures
        # First run: initializes state
        main(
            [
                "--apps-dir",
                str(f["apps_dir"]),
                "--manifests-dir",
                str(f["manifests_dir"]),
                "--state-dir",
                str(f["state_dir"]),
            ]
        )

        # Modify a manifest
        deploy = f["workspace"] / "k8s" / "my-app" / "deployment.yaml"
        content = deploy.read_text()
        deploy.write_text(content.replace("replicas: 1", "replicas: 3"))

        # Second run: should detect the change
        rc = main(
            [
                "--apps-dir",
                str(f["apps_dir"]),
                "--manifests-dir",
                str(f["manifests_dir"]),
                "--state-dir",
                str(f["state_dir"]),
            ]
        )
        assert rc == 0
        # The updated manifest should be in state now
        state_deploy = f["state_dir"] / "my-app" / "deployment.yaml"
        assert "replicas: 3" in state_deploy.read_text()

    def test_argo_validation_catches_errors(self, tmp_path, monkeypatch):
        apps_dir = tmp_path / "apps"
        manifests_dir = tmp_path / "manifests"
        monkeypatch.chdir(tmp_path)

        # Invalid Application: missing destination
        _write_yaml(
            apps_dir / "bad.yaml",
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
            },
        )

        rc = main(
            [
                "--apps-dir",
                str(apps_dir),
                "--manifests-dir",
                str(manifests_dir),
                "--validate-argo",
                "true",
            ]
        )
        assert rc == 1  # Validation should fail

    def test_argo_validation_skippable(self, tmp_path, monkeypatch):
        apps_dir = tmp_path / "apps"
        manifests_dir = tmp_path / "manifests"
        monkeypatch.chdir(tmp_path)

        # Invalid Application: missing destination (but validation is skipped)
        _write_yaml(
            apps_dir / "bad.yaml",
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {"name": "bad-app"},
                "spec": {
                    "source": {
                        "repoURL": "",
                        "path": "k8s",
                    },
                },
            },
        )
        (tmp_path / "k8s").mkdir(parents=True)

        rc = main(
            [
                "--apps-dir",
                str(apps_dir),
                "--manifests-dir",
                str(manifests_dir),
                "--validate-argo",
                "false",
            ]
        )
        # Should pass since validation is disabled
        assert rc == 0

    def test_skip_files(self, sample_fixtures):
        f = sample_fixtures
        # Add another app that we'll skip
        _write_yaml(
            f["apps_dir"] / "skip-me.yaml",
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {"name": "skipped-app"},
                "spec": {
                    "source": {
                        "repoURL": "https://example.com",
                        "path": "nonexistent",  # Would fail if not skipped
                    },
                    "destination": {
                        "server": "https://kubernetes.default.svc",
                        "namespace": "default",
                    },
                    "project": "default",
                },
            },
        )

        rc = main(
            [
                "--apps-dir",
                str(f["apps_dir"]),
                "--manifests-dir",
                str(f["manifests_dir"]),
                "--skip-files",
                "skip-me.yaml",
            ]
        )
        assert rc == 0
        assert not (f["manifests_dir"] / "skipped-app").exists()

    def test_empty_apps_dir(self, tmp_path, monkeypatch):
        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        manifests_dir = tmp_path / "manifests"
        monkeypatch.chdir(tmp_path)

        rc = main(
            [
                "--apps-dir",
                str(apps_dir),
                "--manifests-dir",
                str(manifests_dir),
            ]
        )
        assert rc == 0

    def test_manifest_content_preserved(self, sample_fixtures):
        f = sample_fixtures
        main(
            [
                "--apps-dir",
                str(f["apps_dir"]),
                "--manifests-dir",
                str(f["manifests_dir"]),
            ]
        )

        rendered = (f["manifests_dir"] / "my-app" / "deployment.yaml").read_text()
        assert "nginx:1.27" in rendered
        assert "my-app" in rendered
