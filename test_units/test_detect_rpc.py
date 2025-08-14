import os
import sys
from unittest.mock import patch, mock_open

import pytest
import yaml

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.detect_rpc import (
    get_default_config_path,
    prompt_user_for_config_path,
    read_config_file,
    detect_rpc,
    save_config_path_to_yaml,
    load_config_path_from_yaml,
)
from definitions.errors import RPCConfigError


@pytest.mark.parametrize("platform_system, expected_path_fragment", [
    ("Linux", ".blocknet/blocknet.conf"),
    ("Darwin", "Library/Application Support/Blocknet/blocknet.conf"),
    ("Windows", "Blocknet/blocknet.conf"),
    ("Java", ""),  # Unsupported OS
])
def test_get_default_config_path(platform_system, expected_path_fragment):
    """Test get_default_config_path for different operating systems."""
    with patch("platform.system", return_value=platform_system), \
            patch("os.path.exists", return_value=True), \
            patch("os.getenv", return_value="/appdata"):  # for windows
        path = get_default_config_path()
        if expected_path_fragment:
            assert expected_path_fragment in path.replace("\\", "/")
        else:
            assert path == ""


def test_read_config_file_success():
    """Test reading a valid blocknet.conf file."""
    mock_conf_content = "rpcuser=testuser\nrpcpassword=testpass\nrpcport=41414\n"
    with patch("builtins.open", mock_open(read_data=mock_conf_content)), \
            patch("os.path.exists", return_value=True):
        user, password, port = read_config_file("/fake/path/blocknet.conf")
        assert user == "testuser"
        assert password == "testpass"
        assert port == 41414


def test_read_config_file_missing_keys():
    """Test reading a blocknet.conf file with missing keys."""
    mock_conf_content = "rpcuser=testuser\n"
    with patch("builtins.open", mock_open(read_data=mock_conf_content)), \
            patch("os.path.exists", return_value=True):
        with pytest.raises(RPCConfigError) as excinfo:
            read_config_file("/fake/path/blocknet.conf")
        assert "Missing keys" in str(excinfo.value)
        assert "rpcpassword" in str(excinfo.value)
        assert "rpcport" in str(excinfo.value)


def test_read_config_file_not_found():
    """Test behavior when config file does not exist."""
    with patch("os.path.exists", return_value=False):
        # It should not raise an error, just return None values
        user, password, port = read_config_file("/non/existent/path")
        assert user is None
        assert password is None
        assert port is None


def test_prompt_user_for_config_path_console():
    """Test console prompt for config path."""
    with patch("definitions.detect_rpc._prompt_with_dialog", side_effect=ImportError), \
            patch("builtins.input", return_value="/path/from/console/blocknet.conf"):
        path = prompt_user_for_config_path()
        assert path == "/path/from/console/blocknet.conf"


@patch('tkinter.filedialog.askopenfilename')
@patch('tkinter.Tk')
@patch('ttkbootstrap.Style')
@patch('ttkbootstrap.Bootstyle.setup_ttkbootstrap_api')
def test_prompt_user_for_config_path_dialog(mock_setup_api, mock_style, mock_tk, mock_askopenfilename):
    """Test tkinter dialog for config path."""
    mock_askopenfilename.return_value = "/path/from/dialog/blocknet.conf"
    path = prompt_user_for_config_path()
    assert path == "/path/from/dialog/blocknet.conf"


def test_save_and_load_config_path_yaml(tmp_path):
    """Test saving and loading the config path from a YAML file."""
    yaml_path = tmp_path / "config.yaml"
    config_path = "/path/to/blocknet.conf"

    save_config_path_to_yaml(str(yaml_path), config_path)
    assert os.path.exists(yaml_path)

    with open(yaml_path, 'r') as f:
        content = yaml.safe_load(f)
        assert content['blocknet_path'] == config_path

    loaded_path = load_config_path_from_yaml(str(yaml_path))
    assert loaded_path == config_path


@patch('definitions.detect_rpc.read_config_file', return_value=('user', 'pass', 1234))
@patch('definitions.detect_rpc.prompt_user_for_config_path')
@patch('definitions.detect_rpc.get_default_config_path')
@patch('definitions.detect_rpc.load_config_path_from_yaml')
@patch('os.path.exists')
def test_detect_rpc_flow(mock_exists, mock_load_yaml, mock_get_default, mock_prompt, mock_read_config):
    """Test the complete logic flow of detect_rpc."""

    # Scenario 1: Path is found in config_blocknet.yaml
    mock_load_yaml.return_value = "/path/from/yaml/blocknet.conf"
    mock_exists.return_value = True
    detect_rpc()
    mock_read_config.assert_called_with("/path/from/yaml/blocknet.conf")
    mock_get_default.assert_not_called()
    mock_prompt.assert_not_called()

    # Reset mocks
    mock_read_config.reset_mock()
    mock_get_default.reset_mock()
    mock_prompt.reset_mock()
    mock_load_yaml.reset_mock()
    mock_exists.reset_mock()
    mock_load_yaml.reset_mock()
    mock_exists.reset_mock()

    # Scenario 2: Path from yaml is invalid, fallback to default path
    mock_load_yaml.return_value = "/bad/path"
    mock_exists.side_effect = [False, True, True]  # 1st for yaml path, 2nd for default path, 3rd for final check
    mock_get_default.return_value = "/path/from/default/blocknet.conf"
    detect_rpc()
    mock_get_default.assert_called_once()
    mock_read_config.assert_called_with("/path/from/default/blocknet.conf")
    mock_prompt.assert_not_called()

    # Reset mocks
    mock_read_config.reset_mock()
    mock_get_default.reset_mock()
    mock_prompt.reset_mock()

    # Scenario 3: Default path also fails, fallback to user prompt
    mock_load_yaml.return_value = None
    mock_exists.side_effect = [False, True]  # 1st for default path, 2nd for prompt path
    mock_get_default.return_value = "/bad/default"
    mock_prompt.return_value = "/path/from/prompt/blocknet.conf"
    with patch('definitions.detect_rpc.save_config_path_to_yaml') as mock_save:
        detect_rpc()
        mock_get_default.assert_called_once()
        mock_prompt.assert_called_once()
        mock_save.assert_called_once()
        mock_read_config.assert_called_with("/path/from/prompt/blocknet.conf")

    # Scenario 4: All methods fail to find a path
    mock_exists.return_value = False
    mock_load_yaml.return_value = None
    mock_get_default.return_value = None
    mock_prompt.return_value = None
    with pytest.raises(RPCConfigError):
        detect_rpc()
