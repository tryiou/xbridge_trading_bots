import logging
import os
import platform
from typing import Optional, Tuple

import yaml

from definitions.errors import RPCConfigError

debug_level = 2

autoconf_rpc_log = logging.getLogger("autoconf_rpc_log")
autoconf_rpc_log.setLevel(logging.DEBUG)


def get_default_config_path() -> str:
    """
    Determines the default path for blocknet.conf based on the operating system.

    Returns:
        str: The full path to the configuration file if found, otherwise an empty string.
    """
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


def _prompt_with_dialog() -> str:
    """
    Opens a graphical file dialog to ask the user for the blocknet.conf path.

    Returns:
        str: The path selected by the user, or an empty string if cancelled.
    """
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


def _prompt_on_console() -> str:
    """
    Prompts the user on the console to enter the path to blocknet.conf.

    Returns:
        str: The path entered by the user.
    """
    return input("Enter path to blocknet.conf (including filename): ")


def prompt_user_for_config_path() -> str:
    """
    Prompts the user for the blocknet.conf path, trying a GUI dialog first
    and falling back to a console prompt.

    Returns:
        str: The path provided by the user, or an empty string if none was given.
    """
    try:
        config_path = _prompt_with_dialog()
    except (ImportError, RuntimeError):
        # Fallback to console if tkinter/ttkbootstrap are not available or fail.
        config_path = _prompt_on_console()

    if config_path:
        autoconf_rpc_log.debug(f'User selected config path: {config_path}')
        if not os.path.basename(config_path) == 'blocknet.conf':
            autoconf_rpc_log.warning('Selected file is not named blocknet.conf. Please ensure it is the correct file.')
    else:
        autoconf_rpc_log.warning('No valid path provided.')

    return config_path


def read_config_file(config_path: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Reads RPC credentials and port from the blocknet.conf file.

    Args:
        config_path (str): The path to the blocknet.conf file.

    Returns:
        Tuple[Optional[str], Optional[str], Optional[int]]: A tuple containing
        (rpc_user, rpc_password, rpc_port). Values are None if not found.

    Raises:
        RPCConfigError: If the config path is empty or keys are missing.
    """
    if not config_path:
        autoconf_rpc_log.error('Config path is empty.')
        raise RPCConfigError("Empty configuration path", context={})

    rpc_user = None
    rpc_password = None
    rpc_port = None

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
            raise RPCConfigError(f'Missing keys in config file: {", ".join(missing_config_keys)}', context={})

    else:
        autoconf_rpc_log.warning(f'Config file not found: {config_path}')

    return rpc_user, rpc_password, rpc_port


def load_config_path_from_yaml(yaml_path: str) -> Optional[str]:
    """
    Loads the blocknet.conf path stored in a YAML file.

    Args:
        yaml_path (str): The path to the YAML configuration file.

    Returns:
        Optional[str]: The stored path, or None if not found.
    """
    if os.path.exists(yaml_path):
        with open(yaml_path, 'r') as file:
            config = yaml.safe_load(file)
            return config.get('blocknet_path')
    return None


def save_config_path_to_yaml(yaml_path: str, config_path: str) -> None:
    """
    Saves a given blocknet.conf path to a YAML file.

    Args:
        yaml_path (str): The path to the YAML file where the path will be stored.
        config_path (str): The blocknet.conf path to store.
    """
    config = {'blocknet_path': config_path}
    with open(yaml_path, 'w') as file:
        yaml.safe_dump(config, file)


def detect_rpc() -> Tuple[str, int, str, str]:
    """
    Detects Blocknet RPC configuration by searching in standard locations,
    and prompts the user if necessary.

    The search order is:
    1. Path stored in config/config_blocknet.yaml.
    2. Default OS-specific path.
    3. User prompt.

    Returns:
        Tuple[str, int, str, str]: A tuple containing (rpc_user, rpc_port,
        rpc_password, path_to_datadir).

    Raises:
        RPCConfigError: If no valid configuration can be found.
    """
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
        raise RPCConfigError(f"Config file not found: {config_path}", context={})
    else:
        autoconf_rpc_log.info(f"Blocknet Core Config found at {config_path}")

    rpc_user, rpc_password, rpc_port = read_config_file(config_path)

    return rpc_user, rpc_port, rpc_password, os.path.dirname(config_path)


if __name__ == "__main__":
    detect_rpc()
