import yaml


class YamlToObject:
    def __init__(self, yaml_path):
        config = None
        if isinstance(yaml_path, str):  # It's a path
            try:
                with open(yaml_path, 'r') as file:
                    config = yaml.safe_load(file)
            except FileNotFoundError:
                # Handle case where file might not exist, e.g., optional configs
                config = {}
        elif isinstance(yaml_path, dict):  # It's already a dictionary
            config = yaml_path
        else:
            raise TypeError("YamlToObject must be initialized with a file path or a dictionary.")

        if config:
            # Dynamically set attributes based on YAML content
            for key, value in config.items():
                # If the value is a dictionary, convert it to an object recursively
                setattr(self, key, YamlToObject(value) if isinstance(value, dict) else value)
