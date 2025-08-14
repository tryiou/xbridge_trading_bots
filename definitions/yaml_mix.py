import yaml
from typing import Any, Dict, Union


class YamlToObject:
    """A class to convert YAML data into a Python object with attribute access."""
    def __init__(self, yaml_data: Union[str, Dict[str, Any]]):
        """
        Initializes a YamlToObject instance from a YAML file path or a dictionary.

        Args:
            yaml_data: Either a path to a YAML file or a dictionary.
        """
        if isinstance(yaml_data, str):  # It's a path
            try:
                with open(yaml_data, 'r') as file:
                    config = yaml.safe_load(file) or {}
            except FileNotFoundError:
                # Handle case where file might not exist, e.g., optional configs
                config = {}
        elif isinstance(yaml_data, dict):  # It's already a dictionary
            config = yaml_data
        else:
            raise TypeError("YamlToObject must be initialized with a file path or a dictionary.")

        # Dynamically set attributes based on YAML content
        for key, value in config.items():
            # If the value is a dictionary, convert it to an object recursively
            setattr(self, key, self.__class__(value) if isinstance(value, dict) else value)
