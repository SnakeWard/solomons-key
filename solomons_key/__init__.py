"""Installed resources for the Solomon's Key command-line tools."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("solomons-key")
except PackageNotFoundError:  # Source checkout before the distribution is installed.
    __version__ = "0+uninstalled"

__all__ = ["__version__"]
