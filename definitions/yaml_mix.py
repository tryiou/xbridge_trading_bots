from typing import Any

from definitions.yaml_utils import load_yaml


class YamlToObject:
    """A class to convert YAML data into a Python object with attribute access."""

    def __init__(self, yaml_data: str | dict[str, Any]) -> None:
        if isinstance(yaml_data, str):
            try:
                config = load_yaml(yaml_data)
            except FileNotFoundError:
                config = {}
        elif isinstance(yaml_data, dict):
            config = yaml_data
        else:
            raise TypeError(
                "YamlToObject must be initialized with a file path or a dictionary."
            )

        for key, value in config.items():
            setattr(
                self, key, self.__class__(value) if isinstance(value, dict) else value
            )
