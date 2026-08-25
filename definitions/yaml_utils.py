import contextlib
import logging
import os
import stat
import tempfile
from collections.abc import Callable
from typing import Any

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

_simple_yaml = YAML()
_simple_yaml.preserve_quotes = True

_config_yaml = YAML()
_config_yaml.preserve_quotes = True
_config_yaml.indent(mapping=2, sequence=4, offset=2)
_config_yaml.default_flow_style = False


def load_yaml(path: str) -> dict[str, Any]:
    with open(path) as f:
        return _simple_yaml.load(f) or {}


def save_yaml(path: str, data: dict[str, Any] | None) -> None:
    if data is not None:
        _atomic_write(path, lambda f: _simple_yaml.dump(data, f))


def load_config(path: str) -> dict[str, Any]:
    with open(path) as f:
        return _config_yaml.load(f) or {}


def save_config(path: str, data: dict[str, Any] | None) -> None:
    if data is not None:
        _atomic_write(path, lambda f: _config_yaml.dump(data, f))


def _atomic_write(path: str, dump: Callable[[Any], None]) -> None:
    """Atomically replace `path` with the output of `dump`.

    Writes to a uniquely named temp file in the same directory (same
    filesystem), flushes it to disk and renames it over the target so
    readers never observe a partially written file. The temp file is
    removed on any failure. The replacement inherits the previous file's
    permissions when the target already exists.

    Note: on Windows `os.replace` raises if another process holds the
    destination open without FILE_SHARE_DELETE (editors, AV, sync clients),
    where truncate-in-place would have succeeded; and on POSIX the rename
    itself is not directory-fsynced, so it can be lost on power loss.

    Args:
        path: Target file path to (re)write.
        dump: Callable that serializes into an open binary/text stream,
            e.g. a bound ``ruamel.yaml`` ``dump`` method.
    """
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{os.path.basename(path)}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            dump(f)
            f.flush()
            os.fsync(f.fileno())
        with contextlib.suppress(OSError):
            os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(tmp_path, path)
    except Exception:
        logger.warning("Atomic write to %s failed; removing temp file", path)
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise
