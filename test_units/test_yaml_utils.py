"""Tests for atomic YAML persistence in definitions.yaml_utils."""

import os

import pytest

from definitions import yaml_utils


class TestSaveYaml:
    def test_roundtrip(self, tmp_path):
        path = str(tmp_path / "settings.yaml")
        yaml_utils.save_yaml(path, {"theme": "nord-dark", "count": 2})
        assert yaml_utils.load_yaml(path) == {"theme": "nord-dark", "count": 2}

    def test_none_is_noop(self, tmp_path):
        path = str(tmp_path / "settings.yaml")
        yaml_utils.save_yaml(path, None)
        assert not os.path.exists(path)

    def test_no_tmp_file_residue(self, tmp_path):
        path = str(tmp_path / "settings.yaml")
        yaml_utils.save_yaml(path, {"theme": "nord-dark"})
        assert os.path.exists(path)
        assert not list(tmp_path.glob("*.tmp"))

    def test_overwrite_preserves_no_stale_content(self, tmp_path):
        path = str(tmp_path / "settings.yaml")
        yaml_utils.save_yaml(path, {"theme": "nord-dark"})
        yaml_utils.save_yaml(path, {"theme": "solarized-light"})
        assert yaml_utils.load_yaml(path) == {"theme": "solarized-light"}

    def test_failure_keeps_original_and_cleans_tmp(self, tmp_path, monkeypatch):
        path = str(tmp_path / "settings.yaml")
        yaml_utils.save_yaml(path, {"theme": "nord-dark"})

        def failing_replace(src, dst):
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", failing_replace)
        with pytest.raises(OSError):
            yaml_utils.save_yaml(path, {"theme": "solarized-light"})

        assert yaml_utils.load_yaml(path) == {"theme": "nord-dark"}
        assert not list(tmp_path.glob("*.tmp"))

    def test_dump_failure_keeps_original_and_cleans_tmp(self, tmp_path):
        path = str(tmp_path / "settings.yaml")
        yaml_utils.save_yaml(path, {"theme": "nord-dark"})

        def bad_dump(stream):
            raise RuntimeError("dump exploded")

        with pytest.raises(RuntimeError):
            yaml_utils._atomic_write(path, bad_dump)

        assert yaml_utils.load_yaml(path) == {"theme": "nord-dark"}
        assert not list(tmp_path.glob("*.tmp"))


class TestSaveConfig:
    def test_roundtrip(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        data = {
            "general": {"enabled": True},
            "pairs": [{"name": "LTC_BLOCK", "spread": 0.1}],
        }
        yaml_utils.save_config(path, data)
        assert yaml_utils.load_config(path) == data
        assert not list(tmp_path.glob("*.tmp"))

    def test_none_is_noop(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        yaml_utils.save_config(path, None)
        assert not os.path.exists(path)
