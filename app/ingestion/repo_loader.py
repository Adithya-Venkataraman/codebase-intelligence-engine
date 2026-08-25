"""
Repository acquisition: parse GitHub URLs, clone/update repositories, and
provide a single `prepare_repo` entry point that guarantees a local, up to
date checkout for downstream processing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from git import GitCommandError, Repo

logger = logging.getLogger(__name__)

BASE_REPO_DIR = Path("data/repos")


def get_repo_name(repo_url: str) -> str:
    """Derive a unique, filesystem-safe name from a GitHub URL.

    e.g. https://github.com/psf/requests(.git) -> "psf_requests"
    """
    path = urlparse(repo_url).path.strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError(f"Not a valid GitHub repository URL: {repo_url!r}")

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{owner}_{repo}"


def repo_exists(repo_name: str) -> bool:
    """Check whether a repository has already been cloned locally."""
    return (BASE_REPO_DIR / repo_name).exists()


def clone_repo(repo_url: str) -> Path:
    """Clone `repo_url` into BASE_REPO_DIR and return the local path.

    Raises RuntimeError if the clone fails.
    """
    repo_name = get_repo_name(repo_url)
    repo_path = BASE_REPO_DIR / repo_name

    BASE_REPO_DIR.mkdir(parents=True, exist_ok=True)

    try:
        Repo.clone_from(repo_url, repo_path)
        logger.info("Cloned %s -> %s", repo_url, repo_path)
    except GitCommandError as e:
        raise RuntimeError(f"Failed to clone {repo_url}: {e}") from e

    return repo_path


def update_repo(repo_path: Path) -> bool:
    """Pull the latest changes for an existing local repository."""
    try:
        repo = Repo(repo_path)
        repo.remotes.origin.pull()
        logger.info("Updated %s", repo_path)
        return True
    except Exception as e:
        logger.error("Error updating repository at %s: %s", repo_path, e)
        return False


def prepare_repo(repo_url: str) -> Path:
    """Ensure a repository is cloned and up to date; return its local path.

    This is the single entry point downstream stages (scanning, parsing,
    indexing) should call.
    """
    repo_name = get_repo_name(repo_url)
    repo_path = BASE_REPO_DIR / repo_name

    if repo_exists(repo_name):
        update_repo(repo_path)
    else:
        clone_repo(repo_url)

    return repo_path