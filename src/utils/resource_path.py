"""Bundled resource path helpers."""

from __future__ import annotations

from pathlib import Path

import apppath

_RESOURCES_DIR_NAME = "resources"


def resolve_resource_path(filename: str | Path) -> Path:
    """Return the absolute path to a bundled resource file.

    Prefers the packaged application path once apppath is initialized;
    otherwise falls back to the source-tree resources directory.
    """
    if apppath.app_path is not None:
        resources_dir = apppath.app_path / _RESOURCES_DIR_NAME
    else:
        resources_dir = Path(__file__).resolve().parents[1] / _RESOURCES_DIR_NAME
    return resources_dir / filename
