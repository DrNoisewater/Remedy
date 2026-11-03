"""Smoke tests: every part of Remedy loads, and the main window (with every page) can be built."""
import importlib
import pkgutil
import re

import pytest

import remedy


def _modules():
    return [m.name for m in pkgutil.walk_packages(remedy.__path__, "remedy.")]


@pytest.mark.parametrize("name", _modules())
def test_module_imports(name):
    importlib.import_module(name)


def test_version_is_three_numbers():
    from remedy.core.utils import APP_VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION), APP_VERSION


def test_main_window_builds():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from remedy.app import NAV, MainWindow
    window = MainWindow()
    try:
        assert window.windowTitle() == "Remedy"
        assert set(window.pages) == {key for key, *_ in NAV}
    finally:
        window.close()
        app.processEvents()
