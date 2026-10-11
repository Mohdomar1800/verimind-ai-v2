"""Where uploaded log files live on disk: <storage_dir>/runs/<run_id>/logs/...

Uploads are untrusted, so every write goes through `_safe_target`. It rejects absolute paths, `..`
components and anything that would land outside the run's folder. Zip archives are checked for the
same problems (the "zip-slip" attack) and for size and file-count limits before anything is
extracted.
"""

from __future__ import annotations

import shutil
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO

from verimind.engine.parser import LOG_PATTERNS, discover_logs

CHUNK = 1024 * 1024


class StorageError(ValueError):
    """An upload was rejected. The message is safe to show to the user."""


@dataclass(frozen=True)
class SavedFiles:
    count: int
    size_bytes: int


def _clean_relative(name: str) -> PurePosixPath:
    """Turn an uploaded name into a safe relative path, or raise StorageError."""
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".")]
    if not parts or name.startswith(("/", "\\")) or ":" in parts[0] or ".." in parts:
        raise StorageError(f"Unsafe file name rejected: {name!r}")
    return PurePosixPath(*parts)


COVERAGE_FILE = "coverage.json"  # optional coverage export, see docs/coverage-format.md


def _is_log_name(name: str) -> bool:
    return any(PurePosixPath(name).match(pattern) for pattern in LOG_PATTERNS)


def _is_accepted(name: str) -> bool:
    return _is_log_name(name) or name.lower() == COVERAGE_FILE


class Storage:
    def __init__(self, root: Path, max_bytes: int = 2 * 1024**3, max_files: int = 100_000) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.max_files = max_files

    def logs_dir(self, run_id: int) -> Path:
        return self.root / "runs" / str(run_id) / "logs"

    def _safe_target(self, run_id: int, relative: PurePosixPath) -> Path:
        base = self.logs_dir(run_id).resolve()
        target = (base / relative).resolve()
        if not target.is_relative_to(base):
            raise StorageError(f"Unsafe file name rejected: {relative}")
        return target

    def save_stream(self, run_id: int, name: str, stream: IO[bytes], budget: int | None = None) -> int:
        """Save one uploaded file. Zip archives are extracted. Returns the bytes written."""
        relative = _clean_relative(name)
        if relative.suffix.lower() == ".zip":
            return self._save_zip(run_id, stream, budget)
        if not _is_accepted(relative.name):
            raise StorageError(
                f"{relative.name}: not a log file (expected .log, .out, .txt, optionally .gz, a .zip, or coverage.json)"
            )
        return self._copy(stream, self._safe_target(run_id, relative), budget)

    def save_files(self, run_id: int, files: Iterable[tuple[str, IO[bytes]]]) -> SavedFiles:
        """Save several uploads, enforcing the total size limit across all of them."""
        total = 0
        for name, stream in files:
            total += self.save_stream(run_id, name, stream, budget=self.max_bytes - total)
        count = len(discover_logs(self.logs_dir(run_id)))
        if count == 0:
            raise StorageError("No log files found in the upload.")
        if count > self.max_files:
            raise StorageError(f"Too many files ({count:,}); the limit is {self.max_files:,}.")
        return SavedFiles(count, total)

    def import_folder(self, run_id: int, folder: Path) -> SavedFiles:
        """Copy the log files from a local folder (CLI import and demo data)."""
        total = 0
        extras = [folder / COVERAGE_FILE] if folder.is_dir() and (folder / COVERAGE_FILE).is_file() else []
        for path in [*discover_logs(folder), *extras]:
            with path.open("rb") as stream:
                relative = path.relative_to(folder).as_posix() if folder.is_dir() else path.name
                total += self.save_stream(run_id, relative, stream, budget=self.max_bytes - total)
        count = len(discover_logs(self.logs_dir(run_id)))
        if count == 0:
            raise StorageError(f"No log files found in {folder}.")
        return SavedFiles(count, total)

    def coverage_path(self, run_id: int) -> Path | None:
        """The run's coverage.json, if one was uploaded (at the top level or in a single subfolder)."""
        logs = self.logs_dir(run_id)
        found = sorted(logs.glob(COVERAGE_FILE)) + sorted(logs.glob(f"*/{COVERAGE_FILE}"))
        return found[0] if found else None

    def log_path(self, run_id: int, relative: str) -> Path:
        """Absolute path of a stored log, guaranteed to be inside the run's folder."""
        path = self._safe_target(run_id, _clean_relative(relative))
        if not path.is_file():
            raise FileNotFoundError(f"Log file {relative} is no longer available.")
        return path

    def delete_run(self, run_id: int) -> None:
        shutil.rmtree(self.root / "runs" / str(run_id), ignore_errors=True)

    def _copy(self, stream: IO[bytes], target: Path, budget: int | None) -> int:
        limit = self.max_bytes if budget is None else budget
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with target.open("wb") as out:
            while chunk := stream.read(CHUNK):
                written += len(chunk)
                if written > limit:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise StorageError(f"Upload is larger than the limit of {self.max_bytes / 1024**2:,.0f} MB.")
                out.write(chunk)
        return written

    def _save_zip(self, run_id: int, stream: IO[bytes], budget: int | None) -> int:
        limit = self.max_bytes if budget is None else budget
        try:
            archive = zipfile.ZipFile(stream)
        except zipfile.BadZipFile as exc:
            raise StorageError("The .zip file is damaged or not a zip archive.") from exc
        with archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            # Check everything before extracting anything: declared sizes, names, file count.
            if len(members) > self.max_files:
                raise StorageError(f"The zip contains too many files ({len(members):,}).")
            if sum(m.file_size for m in members) > limit:
                raise StorageError(f"The zip expands beyond the limit of {self.max_bytes / 1024**2:,.0f} MB.")
            written = 0
            for member in members:
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise StorageError(f"The zip contains a symbolic link ({member.filename}), which is not allowed.")
                relative = _clean_relative(member.filename)
                if not _is_accepted(relative.name):
                    continue  # skip non-log files (READMEs, scripts...) instead of failing the upload
                with archive.open(member) as src:
                    # The real size is enforced while copying too: declared sizes can lie (zip bombs).
                    written += self._copy(src, self._safe_target(run_id, relative), limit - written)
            return written


def default_storage() -> Storage:
    """Storage configured from settings (VERIMIND_STORAGE_DIR, VERIMIND_MAX_UPLOAD_MB)."""
    from verimind.config import get_settings

    settings = get_settings()
    return Storage(settings.storage_dir, max_bytes=settings.max_upload_mb * 1024**2)
