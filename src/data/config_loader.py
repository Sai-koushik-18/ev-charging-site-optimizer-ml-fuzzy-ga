"""
Configuration loader for YAML config files.
Provides typed access to all project settings.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional
import yaml
from functools import lru_cache


class Config:
    """Wrapper for configuration with dot-notation access."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name in self._data:
            value = self._data[name]
            if isinstance(value, dict):
                return Config(value)
            return value
        raise AttributeError(f"Config has no attribute '{name}'")

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def to_dict(self) -> Dict[str, Any]:
        return self._data


@lru_cache(maxsize=4)
def _load_yaml(config_path: Path) -> Dict[str, Any]:
    """Load and cache YAML config file."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def load_config(config_name: str, config_dir: Optional[Path] = None) -> Config:
    """
    Load a configuration file by name.

    Args:
        config_name: Name of config file (without .yaml extension)
        config_dir: Directory containing config files (default: project_root/config)

    Returns:
        Config object with dot-notation access
    """
    if config_dir is None:
        # Find project root by looking for config directory
        current = Path.cwd()
        while current != current.parent:
            if (current / "config").exists():
                config_dir = current / "config"
                break
            current = current.parent
        else:
            config_dir = Path(__file__).parent.parent.parent / "config"

    config_path = config_dir / f"{config_name}.yaml"
    data = _load_yaml(config_path)
    return Config(data)


def get_config(config_name: str = "settings") -> Config:
    """Get cached configuration."""
    return load_config(config_name)


def reload_config(config_name: str) -> Config:
    """Force reload of configuration (clears cache)."""
    _load_yaml.cache_clear()
    return load_config(config_name)


# Convenience function for settings
def get_settings() -> Config:
    """Get main settings configuration."""
    return get_config("settings")


def get_fuzzy_config() -> Config:
    """Get fuzzy logic configuration."""
    return get_config("fuzzy_config")


def get_ga_config() -> Config:
    """Get genetic algorithm configuration."""
    return get_config("ga_config")


def get_data_sources_config() -> Config:
    """Get data sources configuration."""
    return get_config("data_sources")


# Environment variable helpers
def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get environment variable with optional default."""
    return os.environ.get(key, default)


def require_env(key: str) -> str:
    """Get required environment variable, raise if not set."""
    value = os.environ.get(key)
    if value is None:
        raise EnvironmentError(f"Required environment variable '{key}' is not set")
    return value