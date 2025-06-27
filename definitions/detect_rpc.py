import json
import logging
import os
import platform

import yaml

from definitions.logger import setup_logging

debug_level = 2

autoconf_rpc_log = setup_logging(name="autoconf_rpc_log",
                                 level=logging.DEBUG, console=True)


def load_config_path_from_json(json_path):
    if os.path.exists(json_path):
        autoconf_rpc_log.debug(f'Loading config path from {json_path}')
        with open(json_path, 'r') as json_file:
            json_data = json.load(json_file)
            if 'blocknet_path' in json_data and os.path.exists(json_data['blocknet_path']):
                autoconf_rpc_log.debug(f'Loaded config path: {json_data["blocknet_path"]}')
                return json_data['blocknet_path']
    return ''


def get_default_config_path():
    # Define default config file paths based on the operating system
    config_paths = {
        'windows': os.path.join(os.getenv('APPDATA') or '', 'Blocknet', 'blocknet.conf'),
        'darwin': os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'Blocknet', 'blocknet.conf'),
        'linux': os.path.join(os.path.expanduser('~'), '.blocknet', 'blocknet.conf')
    }

    # Check if the config file exists in the default paths
    default_path = config_paths.get(platform.system().lower(), '')
    if os.path.exists(default_path):
        autoconf_rpc_log.debug(f'Using default config path: {default_path}')
        return default_path
    else:
        autoconf_rpc_log.warning(f'Default config path does not exist: {default_path}')
        return ''


def prompt_user_for_config_path():
    max_attempts = 1
    attempt_count = 0

    def _prompt_with_dialog():
        from tkinter import filedialog, Tk
        import ttkbootstrap
        root = Tk()
        style = ttkbootstrap.Style(theme="darkly")
        root.style = style
        root.withdraw()  # Hide the main window
        config_path = filedialog.askopenfilename(
            title="Select blocknet.conf",
            filetypes=[("Config files", "blocknet.conf"), ("All files", "*.*")],
            parent=root
        )
        root.destroy()
        ttkbootstrap.Style.instance = None
        return config_path

    def _prompt_on_console():
        return input("Enter path to blocknet.conf (including filename): ")

    while True:  # Keep asking until a valid path is provided or max attempts reached
        attempt_count += 1
        if attempt_count > max_attempts:
            autoconf_rpc_log.error('Maximum attempts exceeded.')
            exit()

        try:
            config_path = _prompt_with_dialog()
        except ImportError:
            config_path = _prompt_on_console()

        if not config_path:
            autoconf_rpc_log.warning('No path provided. Please enter the correct path.')
            continue

        if not os.path.basename(config_path) == 'blocknet.conf':
            autoconf_rpc_log.warning('Selected file is not blocknet.conf. Please select the correct file.')
        else:
            break  # Valid path provided, exit loop

    if config_path:
        autoconf_rpc_log.debug(f'User selected config path: {config_path}')
    else:
        autoconf_rpc_log.warning('No valid path provided.')

    return config_path


def save_config_path_to_json(json_path, config_path):
    with open(json_path, 'w') as json_file:
        json.dump({'blocknet_path': config_path}, json_file)
    autoconf_rpc_log.debug(f'Saved config path to {json_path}: {config_path}')


def read_config_file(config_path):
    if not config_path:
        autoconf_rpc_log.error('Config path is empty.')
        exit()

    if os.path.exists(config_path):
        autoconf_rpc_log.debug(f'Reading config file: {config_path}')
        with open(config_path, 'r') as file:
            lines = file.readlines()

        config_content = [line.strip() for line in
                          lines]  # Remove leading/trailing whitespace and newline characters from each line

        rpc_user = None
        rpc_password = None
        rpc_port = None

        for line in config_content:
            if '=' in line:
                key, value = map(str.strip, line.split('=', 1))
                if key == 'rpcuser':
                    rpc_user = value
                elif key == 'rpcpassword':
                    rpc_password = value
                elif key == 'rpcport':
                    try:
                        rpc_port = int(value)
                    except ValueError:
                        autoconf_rpc_log.warning(f'Invalid rpcport value: {value}')

        if all([rpc_user, rpc_password, rpc_port]):
            autoconf_rpc_log.debug('Read config successfully')
        else:
            missing_config_keys = []
            if not rpc_user:
                missing_config_keys.append('rpcuser')
            if not rpc_password:
                missing_config_keys.append('rpcpassword')
            if not rpc_port:
                missing_config_keys.append('rpcport')

            autoconf_rpc_log.error(f'Missing keys in config file: {", ".join(missing_config_keys)}. Exiting.')
            exit()

    else:
        autoconf_rpc_log.warning(f'Config file not found: {config_path}')

    return rpc_user, rpc_password, rpc_port


def load_config_path_from_yaml(yaml_path):
    if os.path.exists(yaml_path):
        with open(yaml_path, 'r') as file:
            config = yaml.safe_load(file)
            return config.get('blocknet_path')
    return None


def save_config_path_to_yaml(yaml_path, config_path):
    config = {'blocknet_path': config_path}
    with open(yaml_path, 'w') as file:
        yaml.safe_dump(config, file)


def detect_rpc():
    yaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'config_blocknet.yaml')

    # 1. Check if "config_blocknet.yaml" exists
    config_path = load_config_path_from_yaml(yaml_path)
    # 2. If "config_blocknet.yaml" does not contain a valid path, try "get_default_config_path"
    if not config_path or not os.path.exists(config_path):
        config_path = get_default_config_path()

        # 3. If the default path does not exist, ask the user for the "blocknet.conf" path
        if not config_path or not os.path.exists(config_path):
            config_path = prompt_user_for_config_path()

            if config_path:
                # Store the selected path into "config_blocknet.yaml"
                save_config_path_to_yaml(yaml_path, config_path)

    if not config_path or not os.path.exists(config_path):
        autoconf_rpc_log.error("No valid Blocknet Core Config path found.")
        exit()
    else:
        autoconf_rpc_log.info(f"Blocknet Core Config found at {config_path}")

    rpc_user, rpc_password, rpc_port = read_config_file(config_path)

    return rpc_user, rpc_port, rpc_password


def detect_rpc_json():
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'blocknet_cfg.json')

    # 1. Check if "blocknet_cfg.json" exists
    config_path = load_config_path_from_json(json_path)
    # 2. If "blocknet_cfg.json" does not exist, try "get_default_config_path"
    if not config_path or not os.path.exists(config_path):
        config_path = get_default_config_path()

        # 3. If the default path does not exist, ask the user for the "blocknet.conf" path
        if not config_path or not os.path.exists(config_path):
            config_path = prompt_user_for_config_path()

            if config_path:
                # Store the selected path into "blocknet_cfg.json"
                save_config_path_to_json(json_path, config_path)

    if not config_path or not os.path.exists(config_path):
        autoconf_rpc_log.error("No valid Blocknet Core Config path found.")
        exit()
    else:
        autoconf_rpc_log.info(f"Blocknet Core Config found at {config_path}")

    rpc_user, rpc_password, rpc_port = read_config_file(config_path)

    return rpc_user, rpc_port, rpc_password


if __name__ == "__main__":
    detect_rpc()
