# ArgoCD Validate Action

This GitHub Action builds, validates, and compares Kubernetes manifests from ArgoCD **Application** and **ApplicationSet** definitions. It supports Helm, Kustomize, and plain directory sources, expands ApplicationSet generators locally, validates both ArgoCD and Kubernetes manifests, and posts diff summaries to PRs.

**External repositories** referenced in `spec.source.repoURL` are automatically cloned and cached. Private repos are accessible when `github-token` is provided.

## Configuration Options

| Input              | Description                                                                                      | Required | Default                    |
| ------------------ | ------------------------------------------------------------------------------------------------ | -------- | -------------------------- |
| `manifests-dir`    | Directory to output generated manifests                                                          | No       | `manifests`                |
| `apps-dir`         | Directory containing ArgoCD application manifests                                                | Yes      | -                          |
| `skip-files`       | Comma-separated list of files to skip                                                            | No       | ``                         |
| `skip-resources`   | Comma-separated list of Kubernetes resources to skip during validation                           | No       | `CustomResourceDefinition` |
| `state-dir`        | Directory for storing manifest state for diff comparison. Initialized automatically if empty.    | No       | ``                         |
| `comment-on-pr`    | Whether to post manifest diff as a PR comment (requires `state-dir` and `github-token`)          | No       | `false`                    |
| `commit-state`     | Whether to commit the state directory after comparison (requires `state-dir` and `github-token`) | No       | `false`                    |
| `github-token`     | GitHub token for posting PR comments and committing state                                        | No       | ``                         |
| `validate-argo`    | Whether to validate ArgoCD manifest definitions before rendering                                 | No       | `true`                     |
| `kube-version`     | Kubernetes version for `helm template` (e.g. `1.30.0`)                                           | No       | auto-detected              |
| `schema-locations` | Comma-separated additional `kubeconform` schema locations                                        | No       | ``                         |
| `verbose`          | Enable verbose/debug output                                                                      | No       | `false`                    |

## Outputs

| Output              | Description                                                 |
| ------------------- | ----------------------------------------------------------- |
| `diff-status`       | `initialized`, `changed`, or `unchanged`                    |
| `diff-summary`      | One-line summary of changes (Added/Removed/Modified counts) |
| `diff-comment-file` | Path to generated PR comment markdown file (when changed)   |

## Usage

### Basic Usage

```yaml
- name: Build and validate ArgoCD Manifests
  uses: nasus20202/argocd-validate-action@v3
  with:
    apps-dir: "kubernetes/argocd-apps"
```

### With ArgoCD Validation and Manifest Diff

```yaml
- name: Build and validate ArgoCD Manifests
  uses: nasus20202/argocd-validate-action@v3
  with:
    apps-dir: "kubernetes/argocd-apps"
    validate-argo: "true"
    state-dir: ".argocd-state"
    comment-on-pr: "true"
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

### With State Commit on Merge

To keep the state directory up-to-date, enable `commit-state` on your main branch after PRs are merged.
The workflow must have `contents: write` permission:

```yaml
on:
  push:
    branches: [main]

jobs:
  update-state:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v6

      - name: Build, validate, and commit state
        uses: nasus20202/argocd-validate-action@v3
        with:
          apps-dir: "kubernetes/argocd-apps"
          state-dir: ".argocd-state"
          commit-state: "true"
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

## How It Works

The action runs an 8-step pipeline:

1. **Validate ArgoCD definitions** — checks Application/ApplicationSet structure for errors
2. **Discover manifests** — scans the apps directory for ArgoCD resources
3. **Expand ApplicationSets** — runs generators to produce concrete Applications
4. **Render manifests** — runs `helm template`, `kustomize build`, or collects directory manifests
5. **Validate K8s manifests** — runs `kubeconform` against rendered output
6. **Compare with state** — diffs current output against stored state (if `state-dir` set)
7. **Post PR comment** — posts diff summary to the PR (if `comment-on-pr` is enabled)
8. **Commit state** — pushes updated state to the repository (if `commit-state` is enabled)

## Requirements

- Python 3 (available on `ubuntu-latest` runners)
- `helm` (for Helm sources)
- `kubectl` or `kustomize` (for Kustomize sources)
- `kubeconform` (for K8s manifest validation)

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pip install pytest
pytest tests/ -v

# Run the action locally
python -m src.main --apps-dir path/to/apps
```
