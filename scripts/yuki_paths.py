"""Separate shipped, read-only resources from writable personal data."""
import os
from pathlib import Path
import sys


def resource_root():
    return Path(__file__).resolve().parents[1]


def data_root():
    override = os.environ.get('YUKI_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        local = os.environ.get('LOCALAPPDATA')
        return (Path(local) if local else Path.home() / 'AppData' / 'Local') / 'YukiAI'
    return resource_root()  # Existing source checkouts keep their personal records in place.
