"""Unit tests for the local object-storage backend."""

import pytest
from backend.core.storage import LocalObjectStorage


class TestLocalObjectStorage:
    def test_round_trip(self, tmp_path):
        storage = LocalObjectStorage(root=str(tmp_path))
        key = storage.put("articles/abc.txt", b"hello world")
        assert key == "articles/abc.txt"
        assert storage.exists(key)
        assert storage.get(key) == b"hello world"

    def test_missing_key(self, tmp_path):
        storage = LocalObjectStorage(root=str(tmp_path))
        with pytest.raises(FileNotFoundError):
            storage.get("nope.txt")

    def test_delete(self, tmp_path):
        storage = LocalObjectStorage(root=str(tmp_path))
        key = storage.put("a.txt", b"x")
        storage.delete(key)
        assert not storage.exists(key)

    def test_rejects_path_traversal(self, tmp_path):
        storage = LocalObjectStorage(root=str(tmp_path))
        with pytest.raises(ValueError):
            storage.put("../escape.txt", b"x")
        with pytest.raises(ValueError):
            storage.get("..\\escape.txt")

    def test_nested_dirs_created(self, tmp_path):
        storage = LocalObjectStorage(root=str(tmp_path))
        storage.put("deep/nested/dir/f.txt", b"x")
        assert storage.exists("deep/nested/dir/f.txt")
