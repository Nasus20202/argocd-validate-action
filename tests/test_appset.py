"""Tests for ApplicationSet generator expansion."""

import json
from pathlib import Path

import pytest
import yaml

from src.appset import (
    expand_application_set,
    _expand_git_directory_generator,
    _expand_git_file_generator,
    _expand_list_generator,
    _expand_matrix_generator,
    _expand_merge_generator,
    _flatten_dict,
    _normalize_name,
    _apply_selector,
)
from src.models import ApplicationSet, substitute_params, substitute_params_deep
from src.parser import parse_application_set
from src.repos import RepoResolver


# Test fixture repo URL used across test sources
_TEST_REPO_URL = "https://github.com/example/repo"


@pytest.fixture
def resolver(tmp_dir):
    """RepoResolver that treats the test repo URL as local."""
    return RepoResolver(workspace=tmp_dir, local_repo_url=_TEST_REPO_URL)


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


class TestListGenerator:
    def test_basic_list(self):
        config = {
            "elements": [
                {"name": "app1", "namespace": "ns1"},
                {"name": "app2", "namespace": "ns2"},
            ]
        }
        params = _expand_list_generator(config)
        assert len(params) == 2
        assert params[0]["name"] == "app1"
        assert params[0]["namespace"] == "ns1"
        assert params[1]["name"] == "app2"

    def test_nested_list(self):
        config = {
            "elements": [
                {"name": "app1", "config": {"env": "dev", "replicas": 1}},
            ]
        }
        params = _expand_list_generator(config)
        assert len(params) == 1
        assert params[0]["config.env"] == "dev"
        assert params[0]["config.replicas"] == 1

    def test_empty_list(self):
        config = {"elements": []}
        params = _expand_list_generator(config)
        assert params == []


class TestGitDirectoryGenerator:
    def test_simple_directories(self, tmp_dir):
        for d in ["app1", "app2", "app3"]:
            (tmp_dir / d).mkdir()

        config = {
            "repoURL": "https://github.com/example/repo",
            "revision": "main",
            "directories": [{"path": "*"}],
        }
        params = _expand_git_directory_generator(config, tmp_dir)
        assert len(params) == 3
        basenames = {p["path.basename"] for p in params}
        assert basenames == {"app1", "app2", "app3"}

    def test_nested_directories(self, tmp_dir):
        apps_dir = tmp_dir / "apps"
        apps_dir.mkdir()
        for d in ["dev", "staging", "prod"]:
            (apps_dir / d).mkdir()

        config = {
            "directories": [{"path": "apps/*"}],
        }
        params = _expand_git_directory_generator(config, tmp_dir)
        assert len(params) == 3
        basenames = {p["path.basename"] for p in params}
        assert basenames == {"dev", "staging", "prod"}

    def test_exclude_directories(self, tmp_dir):
        for d in ["app1", "app2", "excluded"]:
            (tmp_dir / d).mkdir()

        config = {
            "directories": [
                {"path": "*"},
                {"path": "excluded", "exclude": True},
            ],
        }
        params = _expand_git_directory_generator(config, tmp_dir)
        basenames = {p["path.basename"] for p in params}
        assert "excluded" not in basenames
        assert len(params) == 2

    def test_path_segments(self, tmp_dir):
        nested = tmp_dir / "apps" / "envs" / "dev"
        nested.mkdir(parents=True)

        config = {"directories": [{"path": "apps/envs/*"}]}
        params = _expand_git_directory_generator(config, tmp_dir)
        assert len(params) == 1
        assert params[0]["path.basename"] == "dev"
        assert "path.segments.0" in params[0]

    def test_path_param_prefix(self, tmp_dir):
        (tmp_dir / "app1").mkdir()

        config = {
            "directories": [{"path": "*"}],
            "pathParamPrefix": "myprefix",
        }
        params = _expand_git_directory_generator(config, tmp_dir, "myprefix")
        assert len(params) == 1
        assert "myprefix.path" in params[0]
        assert "myprefix.path.basename" in params[0]
        # Legacy keys should also be present
        assert "path" in params[0]
        assert "path.basename" in params[0]

    def test_nonexistent_base(self, tmp_dir):
        config = {"directories": [{"path": "nonexistent/*"}]}
        params = _expand_git_directory_generator(config, tmp_dir)
        assert params == []

    def test_basename_normalized(self, tmp_dir):
        (tmp_dir / "My App_1").mkdir()
        config = {"directories": [{"path": "*"}]}
        params = _expand_git_directory_generator(config, tmp_dir)
        assert params[0]["path.basenameNormalized"] == "my-app-1"


class TestGitFileGenerator:
    def test_json_files(self, tmp_dir):
        config_dir = tmp_dir / "configs"
        config_dir.mkdir()

        for name in ["app1.json", "app2.json"]:
            (config_dir / name).write_text(
                json.dumps(
                    {
                        "appName": name.replace(".json", ""),
                        "namespace": "default",
                    }
                )
            )

        config = {
            "files": [{"path": "configs/*.json"}],
        }
        params = _expand_git_file_generator(config, tmp_dir)
        assert len(params) == 2
        names = {p["appName"] for p in params}
        assert names == {"app1", "app2"}

    def test_yaml_files(self, tmp_dir):
        config_dir = tmp_dir / "configs"
        config_dir.mkdir()

        (config_dir / "app.yaml").write_text(
            yaml.dump(
                {
                    "name": "my-app",
                    "replicas": 3,
                }
            )
        )

        config = {
            "files": [{"path": "configs/*.yaml"}],
        }
        params = _expand_git_file_generator(config, tmp_dir)
        assert len(params) == 1
        assert params[0]["name"] == "my-app"
        assert params[0]["replicas"] == 3

    def test_file_path_params(self, tmp_dir):
        config_dir = tmp_dir / "envs" / "dev"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text(json.dumps({"env": "dev"}))

        config = {
            "files": [{"path": "envs/*/config.json"}],
        }
        params = _expand_git_file_generator(config, tmp_dir)
        assert len(params) == 1
        assert params[0]["path.basename"] == "dev"
        assert params[0]["path.filename"] == "config.json"


class TestMatrixGenerator:
    def test_cartesian_product(self, tmp_dir, resolver):
        config = {
            "generators": [
                {
                    "list": {
                        "elements": [
                            {"env": "dev"},
                            {"env": "staging"},
                        ]
                    }
                },
                {
                    "list": {
                        "elements": [
                            {"app": "frontend"},
                            {"app": "backend"},
                        ]
                    }
                },
            ]
        }
        params = _expand_matrix_generator(config, resolver)
        assert len(params) == 4
        combos = {(p["env"], p["app"]) for p in params}
        assert combos == {
            ("dev", "frontend"),
            ("dev", "backend"),
            ("staging", "frontend"),
            ("staging", "backend"),
        }

    def test_insufficient_generators(self, tmp_dir, resolver):
        config = {"generators": [{"list": {"elements": [{"env": "dev"}]}}]}
        params = _expand_matrix_generator(config, resolver)
        assert params == []


class TestMergeGenerator:
    def test_basic_merge(self, tmp_dir, resolver):
        config = {
            "mergeKeys": ["name"],
            "generators": [
                {
                    "list": {
                        "elements": [
                            {"name": "app1", "replicas": 1},
                            {"name": "app2", "replicas": 2},
                        ]
                    }
                },
                {
                    "list": {
                        "elements": [
                            {"name": "app1", "env": "prod"},
                        ]
                    }
                },
            ],
        }
        params = _expand_merge_generator(config, resolver)
        assert len(params) == 2
        app1 = next(p for p in params if p["name"] == "app1")
        assert app1["replicas"] == 1
        assert app1["env"] == "prod"
        app2 = next(p for p in params if p["name"] == "app2")
        assert app2["replicas"] == 2
        assert "env" not in app2


class TestSelectorFilter:
    def test_match_labels(self):
        params = [
            {"env": "dev", "name": "app1"},
            {"env": "prod", "name": "app2"},
        ]
        selector = {"matchLabels": {"env": "prod"}}
        result = _apply_selector(params, selector)
        assert len(result) == 1
        assert result[0]["name"] == "app2"

    def test_match_expressions_in(self):
        params = [
            {"env": "dev", "name": "app1"},
            {"env": "prod", "name": "app2"},
            {"env": "staging", "name": "app3"},
        ]
        selector = {
            "matchExpressions": [
                {"key": "env", "operator": "In", "values": ["dev", "staging"]}
            ]
        }
        result = _apply_selector(params, selector)
        assert len(result) == 2

    def test_match_expressions_not_in(self):
        params = [
            {"env": "dev", "name": "app1"},
            {"env": "prod", "name": "app2"},
        ]
        selector = {
            "matchExpressions": [
                {"key": "env", "operator": "NotIn", "values": ["prod"]}
            ]
        }
        result = _apply_selector(params, selector)
        assert len(result) == 1
        assert result[0]["env"] == "dev"

    def test_match_expressions_exists(self):
        params = [
            {"env": "dev", "name": "app1"},
            {"name": "app2"},
        ]
        selector = {"matchExpressions": [{"key": "env", "operator": "Exists"}]}
        result = _apply_selector(params, selector)
        assert len(result) == 1

    def test_match_expressions_does_not_exist(self):
        params = [
            {"env": "dev", "name": "app1"},
            {"name": "app2"},
        ]
        selector = {"matchExpressions": [{"key": "env", "operator": "DoesNotExist"}]}
        result = _apply_selector(params, selector)
        assert len(result) == 1
        assert result[0]["name"] == "app2"


class TestExpandApplicationSet:
    def test_full_expansion(self, tmp_dir, resolver):
        # Create directories
        for d in ["app1", "app2"]:
            (tmp_dir / d).mkdir()
            (tmp_dir / d / "deployment.yaml").write_text("kind: Deployment")

        appset_data = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationSet",
            "metadata": {"name": "test-appset"},
            "spec": {
                "generators": [
                    {
                        "git": {
                            "repoURL": "https://github.com/example/repo",
                            "directories": [{"path": "*"}],
                        }
                    }
                ],
                "template": {
                    "metadata": {"name": "app-{{path.basename}}"},
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
        appset = parse_application_set(appset_data)
        apps = expand_application_set(appset, resolver)
        assert len(apps) == 2
        names = {a["metadata"]["name"] for a in apps}
        assert names == {"app-app1", "app-app2"}

    def test_list_expansion(self, tmp_dir, resolver):
        appset_data = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationSet",
            "metadata": {"name": "list-appset"},
            "spec": {
                "generators": [
                    {
                        "list": {
                            "elements": [
                                {"name": "web", "ns": "frontend"},
                                {"name": "api", "ns": "backend"},
                            ]
                        }
                    }
                ],
                "template": {
                    "metadata": {"name": "{{name}}"},
                    "spec": {
                        "source": {
                            "repoURL": "https://example.com",
                            "path": "apps/{{name}}",
                        },
                        "destination": {"namespace": "{{ns}}"},
                    },
                },
            },
        }
        appset = parse_application_set(appset_data)
        apps = expand_application_set(appset, resolver)
        assert len(apps) == 2
        names = {a["metadata"]["name"] for a in apps}
        assert names == {"web", "api"}

    def test_generator_with_values(self, tmp_dir, resolver):
        (tmp_dir / "app1").mkdir()

        appset_data = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationSet",
            "metadata": {"name": "values-appset"},
            "spec": {
                "generators": [
                    {
                        "git": {
                            "directories": [{"path": "*"}],
                        },
                        "values": {
                            "env": "production",
                        },
                    }
                ],
                "template": {
                    "metadata": {"name": "{{path.basename}}-{{values.env}}"},
                    "spec": {
                        "source": {
                            "repoURL": "https://example.com",
                            "path": "{{path}}",
                        },
                        "destination": {"namespace": "default"},
                    },
                },
            },
        }
        appset = parse_application_set(appset_data)
        apps = expand_application_set(appset, resolver)
        assert len(apps) == 1
        assert apps[0]["metadata"]["name"] == "app1-production"


class TestSubstituteParams:
    def test_simple_substitution(self):
        result = substitute_params("Hello {{name}}", {"name": "world"})
        assert result == "Hello world"

    def test_nested_key(self):
        result = substitute_params(
            "{{path.basename}}",
            {"path.basename": "app1"},
        )
        assert result == "app1"

    def test_multiple_substitutions(self):
        result = substitute_params(
            "{{env}}-{{name}}",
            {"env": "dev", "name": "app1"},
        )
        assert result == "dev-app1"

    def test_no_match(self):
        result = substitute_params("{{unknown}}", {"name": "test"})
        assert result == "{{unknown}}"

    def test_deep_substitution(self):
        obj = {
            "metadata": {"name": "{{name}}"},
            "spec": {
                "source": {"path": "apps/{{path}}"},
                "tags": ["{{env}}", "latest"],
            },
        }
        result = substitute_params_deep(
            obj, {"name": "my-app", "path": "web", "env": "prod"}
        )
        assert result["metadata"]["name"] == "my-app"
        assert result["spec"]["source"]["path"] == "apps/web"
        assert result["spec"]["tags"] == ["prod", "latest"]


class TestNormalizeName:
    def test_simple(self):
        assert _normalize_name("my-app") == "my-app"

    def test_spaces(self):
        assert _normalize_name("my app") == "my-app"

    def test_underscores(self):
        assert _normalize_name("my_app_1") == "my-app-1"

    def test_uppercase(self):
        assert _normalize_name("MyApp") == "myapp"

    def test_special_chars(self):
        assert _normalize_name("app@v1.0") == "app-v1-0"


class TestFlattenDict:
    def test_shallow(self):
        assert _flatten_dict({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_nested(self):
        assert _flatten_dict({"a": {"b": {"c": 1}}}) == {"a.b.c": 1}

    def test_mixed(self):
        result = _flatten_dict({"a": 1, "b": {"c": 2}})
        assert result == {"a": 1, "b.c": 2}
