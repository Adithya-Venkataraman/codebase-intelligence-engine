"""
Repository & File Processing.

Recursively scans a cloned repository, filters out files we never want to
index (VCS internals, dependency directories, build artefacts, binaries,
oversized files), detects source language by extension, and produces
metadata (relative path, size, language, content hash) for every file that
survives filtering.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Ignore rules -----------------------------------------------------

# Directory names to skip entirely, wherever they appear in the tree.
IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    ".env",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    "coverage",
    ".idea",
    ".vscode",
    "vendor",
    "site-packages",
    "egg-info",
}

# File extensions that are binary / non-source and should never be read.
IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a",
    ".pyc", ".pyo", ".class", ".jar",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".wav",
    ".db", ".sqlite", ".sqlite3",
    ".lock",
}

# Max file size to index (bytes). Anything larger is skipped.
MAX_FILE_SIZE_BYTES = 1_000_000  # 1 MB

# Extension -> language name, used for language detection / routing to
# the right parser later on.
LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".rst": "text",
    ".txt": "text",
}


@dataclass(frozen=True)
class FileMetadata:
    """Metadata captured for a single file in the repository."""

    path: str          # path relative to the repo root, POSIX-style
    absolute_path: str
    language: str      # from LANGUAGE_BY_EXTENSION, or "unknown"
    size_bytes: int
    sha256: str        # content hash, used later to skip re-embedding unchanged files


def detect_language(path: Path) -> str:
    """Return the language name for a file based on its extension."""
    return LANGUAGE_BY_EXTENSION.get(path.suffix.lower(), "unknown")


def _is_ignored_dir(dir_name: str) -> bool:
    return dir_name in IGNORED_DIR_NAMES or dir_name.endswith(".egg-info")


def _should_index_file(path: Path) -> bool:
    """Decide whether a file should be included, applying extension and
    size filters. Returns False (without raising) for unreadable files.
    """
    if path.suffix.lower() in IGNORED_EXTENSIONS:
        return False

    try:
        size = path.stat().st_size
    except OSError as e:
        logger.warning("Could not stat %s: %s", path, e)
        return False

    if size == 0 or size > MAX_FILE_SIZE_BYTES:
        return False

    # Skip files that aren't valid UTF-8 text (best-effort binary check).
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        if b"\x00" in chunk:
            return False
        chunk.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    return True


def _hash_file(path: Path) -> str:
    """Compute the SHA-256 hash of a file's contents, streamed to avoid
    loading large files fully into memory."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def scan_repository(repo_path: Path) -> list[FileMetadata]:
    """Recursively walk `repo_path`, applying ignore rules, and return
    metadata for every file worth indexing.
    """
    repo_path = Path(repo_path)
    if not repo_path.exists():
        raise FileNotFoundError(f"Repository path does not exist: {repo_path}")

    results: list[FileMetadata] = []

    for current_dir, dir_names, file_names in os_walk_filtered(repo_path):
        for file_name in file_names:
            file_path = current_dir / file_name

            if not _should_index_file(file_path):
                continue

            try:
                results.append(
                    FileMetadata(
                        path=file_path.relative_to(repo_path).as_posix(),
                        absolute_path=str(file_path),
                        language=detect_language(file_path),
                        size_bytes=file_path.stat().st_size,
                        sha256=_hash_file(file_path),
                    )
                )
            except OSError as e:
                logger.warning("Skipping unreadable file %s: %s", file_path, e)

    logger.info("Scanned %s: %d files indexed", repo_path, len(results))
    return results


def os_walk_filtered(repo_path: Path):
    """Thin wrapper around os.walk that prunes ignored directories in
    place (so os.walk never descends into them) and yields (Path, dirs,
    files) tuples with the current directory as a Path.
    """
    import os

    for root, dir_names, file_names in os.walk(repo_path):
        dir_names[:] = [d for d in dir_names if not _is_ignored_dir(d)]
        yield Path(root), dir_names, file_names