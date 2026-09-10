"""Preferencias persistentes del plugin."""

from __future__ import annotations

from typing import Any

from qgis.core import QgsSettings

from .constants import SETTINGS_GROUP


def _key(name: str) -> str:
    return f"{SETTINGS_GROUP}/{name}"


def get(name: str, default: Any = None, type_=None) -> Any:
    s = QgsSettings()
    if type_ is not None:
        return s.value(_key(name), default, type=type_)
    return s.value(_key(name), default)


def set_value(name: str, value: Any) -> None:
    QgsSettings().setValue(_key(name), value)


def get_bool(name: str, default: bool = False) -> bool:
    return get(name, default, type_=bool)


def get_float(name: str, default: float = 0.0) -> float:
    try:
        return float(get(name, default))
    except (TypeError, ValueError):
        return default


def get_int(name: str, default: int = 0) -> int:
    try:
        return int(get(name, default))
    except (TypeError, ValueError):
        return default
