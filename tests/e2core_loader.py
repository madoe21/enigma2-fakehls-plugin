# -*- coding: utf-8 -*-
"""Load src/E2HLSServer/core modules without importing the enigma2 platform.

The E2HLSServer package __init__ pulls in enigma2-only modules (Components,
Tools, enigma), which do not exist off the receiver. Tests therefore mount
the core directory as a synthetic package so its relative imports resolve
while the platform layer stays untouched.
"""
import importlib.util
import os
import sys
import types

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "E2HLSServer", "core",
)
_PKG_NAME = "e2core"


def _ensure_package():
    if _PKG_NAME not in sys.modules:
        package = types.ModuleType(_PKG_NAME)
        package.__path__ = [_CORE_DIR]
        sys.modules[_PKG_NAME] = package


def load(module_name):
    """Import a core module (e.g. 'stream_service') as e2core.<module_name>."""
    _ensure_package()
    full_name = _PKG_NAME + "." + module_name
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(
        full_name, os.path.join(_CORE_DIR, module_name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module
