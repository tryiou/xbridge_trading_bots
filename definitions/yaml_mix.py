import yaml


class YamlToObject:
    def __init__(self, yaml_path):
        with open(yaml_path, 'r') as file:
            config = yaml.safe_load(file)
            # Dynamically set attributes based on YAML content
            for key, value in config.items():
                setattr(self, key, value)
