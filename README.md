# ArgoCD Build Manifests Action

This GitHub Action builds Kubernetes manifests from ArgoCD application definitions and validates them. The validation results are added to the job summary.

## Configuration Options

| Input            | Description                                                            | Required | Default                    |
| ---------------- | ---------------------------------------------------------------------- | -------- | -------------------------- |
| `manifests-dir`  | Directory to output generated manifests                                | No       | `manifests`                |
| `apps-dir`       | Directory containing ArgoCD application manifests                      | Yes      | -                          |
| `base-path`      | Base path for resolving relative paths in ArgoCD apps                  | Yes      | -                          |
| `skip-files`     | Comma-separated list of files to skip                                  | No       | ``                         |
| `skip-resources` | Comma-separated list of Kubernetes resources to skip during validation | No       | `CustomResourceDefinition` |

## Usage

```yaml
- name: Build and validate ArgoCD Manifests
  uses: nasus20202/argocd-validate-action@main
  with:
    apps-dir: "kubernetes/argocd-apps"
    base-path: "kubernetes"
    skip-resources: "CustomResourceDefinition"
```

The validation results will be displayed in the job summary.
