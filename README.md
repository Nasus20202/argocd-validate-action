# ArgoCD Build Manifests Action

This GitHub Action builds Kubernetes manifests from ArgoCD application definitions and validates them. The validation results are added to the job summary.

## Configuration Options

| Input            | Description                                                                                         | Required | Default                    |
| ---------------- | --------------------------------------------------------------------------------------------------- | -------- | -------------------------- |
| `manifests-dir`  | Directory to output generated manifests                                                             | No       | `manifests`                |
| `apps-dir`       | Directory containing ArgoCD application manifests                                                   | Yes      | -                          |
| `base-path`      | Base path for resolving relative paths in ArgoCD apps                                               | Yes      | -                          |
| `skip-files`     | Comma-separated list of files to skip                                                               | No       | ``                         |
| `skip-resources` | Comma-separated list of Kubernetes resources to skip during validation                              | No       | `CustomResourceDefinition` |
| `state-dir`      | Directory for storing manifest state for diff comparison. Initialized automatically if empty.       | No       | ``                         |
| `comment-on-pr`  | Whether to post manifest diff as a PR comment (requires `state-dir` and `github-token` to be set)   | No       | `false`                    |
| `commit-state`   | Whether to commit the state directory back to the repository after comparison (requires `state-dir` and `github-token` with write permissions) | No       | `false`                    |
| `github-token`   | GitHub token for posting PR comments                                                                | No       | ``                         |

## Usage

### Basic Usage

```yaml
- name: Build and validate ArgoCD Manifests
  uses: nasus20202/argocd-validate-action@main
  with:
    apps-dir: "kubernetes/argocd-apps"
    base-path: "kubernetes"
    skip-resources: "CustomResourceDefinition"
```

### With Manifest Diff and PR Comments

```yaml
- name: Build and validate ArgoCD Manifests
  uses: nasus20202/argocd-validate-action@main
  with:
    apps-dir: "kubernetes/argocd-apps"
    base-path: "kubernetes"
    state-dir: ".argocd-state"
    comment-on-pr: "true"
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

When `state-dir` is set, the action will:
1. Build and validate manifests as usual.
2. Compare the newly generated manifests with the previously stored state.
3. If the state directory is empty or does not exist, initialize it with the current manifests.
4. Report a summary of changes (added, removed, modified files) in the job summary.
5. If `comment-on-pr` is `true`, post the diff as a comment on the pull request.
6. If `commit-state` is `true`, commit and push the updated state directory to the repository so that future runs have something to compare against.

### With State Commit on Merge

To keep the state directory up-to-date, enable `commit-state` on your main branch after PRs are merged.
The workflow must have `contents: write` permission and a valid `github-token` for the push to succeed:

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
      - uses: actions/checkout@v4

      - name: Build, validate, and commit state
        uses: nasus20202/argocd-validate-action@main
        with:
          apps-dir: "kubernetes/argocd-apps"
          base-path: "kubernetes"
          state-dir: ".argocd-state"
          commit-state: "true"
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

The validation results will be displayed in the job summary.
