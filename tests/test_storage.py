from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from verimind.storage import Storage, StorageError


def zip_bytes(entries: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    buf.seek(0)
    return buf


def test_saves_logs_and_counts_them(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    saved = storage.save_files(1, [("a.log", io.BytesIO(b"x\n")), ("sub/b.log.gz", io.BytesIO(b"yy"))])
    assert (saved.count, saved.size_bytes) == (2, 4)
    assert (storage.logs_dir(1) / "sub" / "b.log.gz").exists()


@pytest.mark.parametrize("name", ["../evil.log", "/etc/x.log", "C:/x.log", "a/../../b.log", "..\\x.log"])
def test_rejects_path_traversal(tmp_path: Path, name: str) -> None:
    with pytest.raises(StorageError, match="Unsafe"):
        Storage(tmp_path).save_stream(1, name, io.BytesIO(b"x"))


def test_rejects_non_log_files(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="not a log file"):
        Storage(tmp_path).save_stream(1, "run.exe", io.BytesIO(b"x"))


def test_zip_is_extracted_and_non_logs_skipped(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    archive = zip_bytes({"reg/t1.log": b"a", "reg/t2.log": b"bb", "README.md": b"hi"})
    saved = storage.save_files(7, [("regression.zip", archive)])
    assert saved.count == 2
    assert not (storage.logs_dir(7) / "README.md").exists()


def test_zip_slip_is_blocked(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="Unsafe"):
        Storage(tmp_path).save_stream(1, "x.zip", zip_bytes({"../../escape.log": b"x"}))
    assert not (tmp_path.parent / "escape.log").exists()


def test_size_limit_applies_across_files_and_inside_zips(tmp_path: Path) -> None:
    storage = Storage(tmp_path, max_bytes=10)
    with pytest.raises(StorageError, match="larger than the limit"):
        storage.save_files(1, [("a.log", io.BytesIO(b"123456")), ("b.log", io.BytesIO(b"123456"))])
    with pytest.raises(StorageError, match="expands beyond"):
        storage.save_stream(2, "big.zip", zip_bytes({"x.log": b"0" * 50}))


def test_damaged_zip_and_empty_upload(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    with pytest.raises(StorageError, match="damaged"):
        storage.save_stream(1, "x.zip", io.BytesIO(b"not a zip"))
    with pytest.raises(StorageError, match="No log files"):
        storage.save_files(2, [("x.zip", zip_bytes({"notes.md": b"x"}))])


def test_import_folder_and_delete(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "deep").mkdir(parents=True)
    (src / "deep" / "t.log").write_text("hello")
    storage = Storage(tmp_path / "store")
    assert storage.import_folder(3, src).count == 1
    storage.delete_run(3)
    assert not storage.logs_dir(3).exists()
