import os
import json

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config.json')
CONFIG_PATH = os.path.abspath(CONFIG_PATH)

try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        _config_data = json.load(f)
except FileNotFoundError:
    _config_data = {}

DOWNLOAD_BASE_DIR = _config_data.get('DOWNLOAD_BASE_DIR', './downloads')