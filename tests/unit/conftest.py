"""Shared pytest fixtures/helpers for this collection's unit tests.

This repository has no ansible-test/collection-install scaffolding, so
`plugins/modules/install_cluster.py` (which imports
`ansible_collections.rhpds.assisted_installer.plugins.module_utils.access_token`)
can't be imported the normal way without the collection being installed
under `~/.ansible/collections`. This registers just enough of that
namespace in `sys.modules`, pointing at this checkout, so the module can
be imported directly for unit testing.
"""
import importlib.util
import pathlib
import sys
import types

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_NAMESPACE_PACKAGES = (
    "ansible_collections",
    "ansible_collections.rhpds",
    "ansible_collections.rhpds.assisted_installer",
    "ansible_collections.rhpds.assisted_installer.plugins",
    "ansible_collections.rhpds.assisted_installer.plugins.module_utils",
    "ansible_collections.rhpds.assisted_installer.plugins.modules",
)


def _load_from_path(module_name, file_path):
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _register_collection_namespace():
    for name in _NAMESPACE_PACKAGES:
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = []  # mark as a namespace package
            sys.modules[name] = package

    _load_from_path(
        "ansible_collections.rhpds.assisted_installer.plugins.module_utils.access_token",
        str(REPO_ROOT / "plugins" / "module_utils" / "access_token.py"),
    )


def load_install_cluster():
    """Import plugins/modules/install_cluster.py and return the module object."""
    _register_collection_namespace()
    return _load_from_path(
        "ansible_collections.rhpds.assisted_installer.plugins.modules.install_cluster",
        str(REPO_ROOT / "plugins" / "modules" / "install_cluster.py"),
    )
