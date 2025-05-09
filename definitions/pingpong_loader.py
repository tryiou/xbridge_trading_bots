import os

import yaml


class ConfigPP:
    def __init__(self, config_data=None):
        # Initialize with None values
        self.pair_configs = None
        self.debug_level = None
        self.ttk_theme = None
        self.user_pairs = None
        self.price_variation_tolerance = None
        self.sell_price_offset = None
        self.usd_amount_default = None
        self.usd_amount_custom = {}
        self.spread_default = None
        self.spread_custom = {}

        # Override with provided config_data
        if config_data:
            self.update_config(config_data)
        else:
            # Set default values if no config_data provided
            self.set_defaults()

    def set_defaults(self):
        self.debug_level = 2
        self.ttk_theme = "darkly"
        self.pair_configs = [
            {
                "name": "BLOCK_LTC_1",
                "pair": "BLOCK/LTC",
                "enabled": True,
                "price_variation_tolerance": 0.02,
                "sell_price_offset": 0.05,
                "usd_amount": 1,
                "spread": 0.05,
            },
            {
                "name": "LTC_BLOCK_1",
                "enabled": True,
                "price_variation_tolerance": 0.02,
                "sell_price_offset": 0.05,
                "usd_amount": 1,
                "spread": 0.05,
            }
        ]

    def update_config(self, config_data):
        # Update only with the provided values
        if 'debug_level' in config_data:
            self.debug_level = config_data['debug_level']
        if 'ttk_theme' in config_data:
            self.ttk_theme = config_data['ttk_theme']
        if 'pair_configs' in config_data:
            self.pair_configs = config_data['pair_configs']

    def get(self, key, default=None):
        """Return the value for the given key or a default value if the key does not exist."""
        return getattr(self, key, default)

    @staticmethod
    def load_config(filename):
        if not os.path.exists(filename):
            # File does not exist, save default configuration
            default_config = ConfigPP()
            ConfigPP.save_config(default_config, filename)

        with open(filename, 'r') as file:
            config_data = yaml.safe_load(file)
        return ConfigPP(config_data)

    @staticmethod
    def save_config(config, filename):
        with open(filename, 'w') as file:
            yaml.dump(config.__dict__, file)  # Save the instance's dictionary to file
