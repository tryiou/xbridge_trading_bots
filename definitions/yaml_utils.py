from typing import Any

from ruamel.yaml import YAML

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
        with open(path, "w") as f:
            _simple_yaml.dump(data, f)


def load_config(path: str) -> dict[str, Any]:
    with open(path) as f:
        return _config_yaml.load(f) or {}


def save_config(path: str, data: dict[str, Any] | None) -> None:
    if data is not None:
        with open(path, "w") as f:
            _config_yaml.dump(data, f)
