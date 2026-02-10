"""Repository resolver for cloning and caching git repositories."""

from __future__ import annotations

import atexit
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _normalize_repo_url(url: str) -> str:
    """Normalize a git repo URL to a canonical form for cache keying.

    Handles HTTPS, SSH, and git:// URLs.  Strips trailing .git and protocol
    differences so that the same repo is only cloned once.

    >>> _normalize_repo_url("https://github.com/org/repo.git")
    'github.com/org/repo'
    >>> _normalize_repo_url("git@github.com:org/repo.git")
    'github.com/org/repo'
    """
    url = url.strip()

    # SSH: git@host:org/repo.git
    ssh_match = re.match(r"^[\w.-]+@([\w.-]+):(.*?)(?:\.git)?$", url)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}"

    # HTTPS / git:// / http://
    url = re.sub(r"^(https?|git)://", "", url)
    url = re.sub(r"\.git$", "", url)
    url = url.rstrip("/")
    return url


class RepoResolver:
    """Resolve ArgoCD source repo URLs to local filesystem paths.

    For sources that reference the current repository (matching
    ``local_repo_url``), the workspace root is returned directly.

    For external repositories the repo is cloned (shallow) into a temporary
    directory and cached for the lifetime of the resolver.  If a
    ``github_token`` is provided it is injected into HTTPS clone URLs for
    private-repo access.

    Call :meth:`cleanup` (or use as a context-manager) to remove temp clones.
    """

    def __init__(
        self,
        workspace: Path,
        local_repo_url: str | None = None,
        github_token: str | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.github_token = github_token or ""
        self._cache: dict[str, Path] = {}
        self._tmp_root: Path | None = None

        # Determine local repo URL
        if local_repo_url:
            self._local_key = _normalize_repo_url(local_repo_url)
        else:
            self._local_key = self._detect_local_repo_url()

        # Register cleanup
        atexit.register(self.cleanup)

    # -- public API ----------------------------------------------------------

    def resolve(self, repo_url: str, revision: str | None = None) -> Path:
        """Return a local path for the given ``repo_url``.

        If ``repo_url`` matches the workspace repository the workspace root
        is returned.  Otherwise the repo is cloned (or returned from cache).

        Args:
            repo_url: The ``spec.source.repoURL`` value.
            revision: Optional ``targetRevision`` to check out.

        Returns:
            Path to the repository root on disk.
        """
        if not repo_url:
            return self.workspace

        key = _normalize_repo_url(repo_url)

        # Local repo?
        if key == self._local_key:
            return self.workspace

        # Already cloned?
        cache_key = f"{key}@{revision or 'HEAD'}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Clone
        clone_path = self._clone(repo_url, revision)
        self._cache[cache_key] = clone_path
        return clone_path

    def cleanup(self) -> None:
        """Remove all temporary clones."""
        if self._tmp_root and self._tmp_root.exists():
            shutil.rmtree(self._tmp_root, ignore_errors=True)
            self._tmp_root = None
        self._cache.clear()

    # context-manager support
    def __enter__(self) -> RepoResolver:
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()

    # -- internals -----------------------------------------------------------

    def _detect_local_repo_url(self) -> str:
        """Try to determine the local repo URL from git remote or env."""
        # GitHub Actions sets GITHUB_REPOSITORY=owner/repo
        gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
        gh_server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        if gh_repo:
            return _normalize_repo_url(f"{gh_server}/{gh_repo}")

        # Fallback: read git remote origin
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=self.workspace,
            )
            if result.returncode == 0 and result.stdout.strip():
                return _normalize_repo_url(result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return ""

    def _clone(self, repo_url: str, revision: str | None) -> Path:
        """Clone a repository."""
        if self._tmp_root is None:
            self._tmp_root = Path(tempfile.mkdtemp(prefix="argocd-repos-"))

        # Build a safe directory name
        safe_name = re.sub(r"[^\w.-]", "_", _normalize_repo_url(repo_url))
        if revision:
            safe_name += f"__{re.sub(r'[^\w.-]', '_', revision)}"
        clone_dir = self._tmp_root / safe_name

        if clone_dir.exists():
            return clone_dir

        clone_url = self._inject_token(repo_url)

        logger.info("Cloning %s (revision: %s)...", repo_url, revision or "HEAD")

        cmd = ["git", "clone", "--depth", "1"]
        if revision:
            cmd.extend(["--branch", revision])
        cmd.extend([clone_url, str(clone_dir)])

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            if result.returncode != 0:
                # Retry without --branch for commit SHAs
                if revision and "Could not find remote branch" in result.stderr:
                    logger.debug(
                        "Branch not found, trying full clone + checkout for %s",
                        revision,
                    )
                    return self._clone_full(clone_url, revision, clone_dir, env)

                raise RuntimeError(f"Failed to clone {repo_url}:\n{result.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Timeout cloning {repo_url} — check network access and credentials"
            )

        logger.info("Cloned %s to %s", repo_url, clone_dir)
        return clone_dir

    def _clone_full(
        self, clone_url: str, revision: str, clone_dir: Path, env: dict[str, str]
    ) -> Path:
        """Full clone + checkout — needed for commit SHA revisions."""
        result = subprocess.run(
            ["git", "clone", clone_url, str(clone_dir)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to clone {clone_url}:\n{result.stderr}")

        result = subprocess.run(
            ["git", "checkout", revision],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=clone_dir,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to checkout {revision}:\n{result.stderr}")

        return clone_dir

    def _inject_token(self, repo_url: str) -> str:
        """Inject GitHub token into HTTPS URLs for private repo access."""
        if not self.github_token:
            return repo_url

        # Only inject into HTTPS GitHub URLs
        match = re.match(r"^https://([^/]+)/(.+)$", repo_url)
        if match:
            host = match.group(1)
            path = match.group(2)
            return f"https://x-access-token:{self.github_token}@{host}/{path}"

        return repo_url
