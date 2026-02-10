# Releasing

This document describes how to create a new release of this GitHub Action.

## Creating a Release

Releases are created using the `Release` GitHub Actions workflow, which can be triggered manually:

1. Go to the [Actions tab](../../actions/workflows/release.yaml)
2. Click on "Run workflow"
3. Enter the version tag in semver format (e.g., `v1.0.0`)
4. Click "Run workflow"

The workflow will:
- Validate the version format (must be `vX.Y.Z`)
- Create and push the version tag (e.g., `v1.0.0`)
- Create and push the minor version tag (e.g., `v1.0`)
- Create and push the major version tag (e.g., `v1`)
- Generate release notes with a list of commits since the previous version
- Create a GitHub release with the generated release notes

## Release Notes

The release notes are automatically generated and include:
- A list of all commits since the previous version (e.g., `v1.0.0` to `v1.1.0`)
- Each commit is shown with its message and short hash
- A link to the full changelog comparing the previous version to the new version

For the first release (when no previous version exists), all commits from the repository's first commit to HEAD are included.

## Version Tags

The action maintains three levels of version tags:
- **Full version** (e.g., `v1.0.0`): Points to a specific release
- **Minor version** (e.g., `v1.0`): Points to the latest patch within the minor version
- **Major version** (e.g., `v1`): Points to the latest minor version within the major version

This allows users to reference the action using different levels of stability:
```yaml
# Pin to a specific version
uses: nasus20202/argocd-validate-action@v1.0.0

# Auto-update to latest patch version
uses: nasus20202/argocd-validate-action@v1.0

# Auto-update to latest minor version (recommended for most users)
uses: nasus20202/argocd-validate-action@v1
```
