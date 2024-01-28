import logging
import os
import platform
import json
import logging
from definitions.logger import setup_logger
import time

debug_level = 2

autoconf_rpc_log = setup_logger(name="autoconf_rpc_log",
                                level=logging.INFO, console=True)


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
    config_path = ''

    try:
        from tkinter import filedialog, Tk
        # from ttkbootstrap import Style
        import ttkbootstrap
        from config import config_pingpong as config

        root = Tk()
        style = ttkbootstrap.Style(theme=config.ttk_theme)
        root.style = style
        root.withdraw()  # Hide the main window
        config_path = filedialog.askopenfilename(
            title="Select blocknet.conf",
            filetypes=[("Config files", "blocknet.conf"), ("All files", "*.*")],
            parent=root
        )
        root.destroy()
        ttkbootstrap.Style.instance = None
        if config_path:
            autoconf_rpc_log.debug(f'User selected config path: {config_path}')
        else:
            autoconf_rpc_log.warning('User canceled the file dialog')
            exit()
    except ImportError:
        autoconf_rpc_log.error('Tkinter or ttkbootstrap is not available. Asking for config path on terminal.')
        time.sleep(0.1)
        config_path = input("Enter the path to blocknet.conf: ")

    return config_path


def save_config_path_to_json(json_path, config_path):
    with open(json_path, 'w') as json_file:
        json.dump({'blocknet_path': config_path}, json_file)
    autoconf_rpc_log.debug(f'Saved config path to {json_path}: {config_path}')


def read_config_file(config_path):
    if config_path:  # Only try to open the file if the path is not an empty string
        if os.path.exists(config_path):
            autoconf_rpc_log.debug(f'Reading config file: {config_path}')
            with open(config_path, 'r') as file:
                config_content = file.readlines()

            for line in config_content:
                if '=' in line:
                    key, value = map(str.strip, line.split('=', 1))
                    if key == 'rpcuser':
                        rpc_user = value
                    elif key == 'rpcpassword':
                        rpc_password = value
                    elif key == 'rpcport':
                        rpc_port = int(value)
            autoconf_rpc_log.debug(f'Read config successfully')
        else:
            autoconf_rpc_log.warning(f'Config file not found: {config_path}')

    return rpc_user, rpc_password, rpc_port


def detect_rpc():
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

    rpc_user, rpc_password, rpc_port = read_config_file(config_path)

    # print(f'Blocknet Core Config path: {config_path}')
    # print(f'RPC User: {rpc_user}')
    # print(f'RPC Port: {rpc_port}')
    return rpc_user, rpc_port, rpc_password


if __name__ == "__main__":
    detect_rpc()
