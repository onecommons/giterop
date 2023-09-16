# Copyright (c) 2023 Adam Souzis
# SPDX-License-Identifier: MIT
import sys
import os.path
import logging
from pathlib import Path
from importlib.abc import Loader
from importlib import invalidate_caches
from importlib.machinery import FileFinder, ModuleSpec, PathFinder, SourceFileLoader
from importlib.util import spec_from_file_location, spec_from_loader, module_from_spec
from typing import Any, Dict, Optional, Sequence
from types import ModuleType
import importlib._bootstrap
from .python2yaml import restricted_exec
from .yaml2python import yaml_to_python

logger = logging.getLogger("tosca")


class RepositoryFinder(PathFinder):
    "Place on sys.meta_path to enable finding modules in tosca repositories"

    @classmethod
    def find_spec(cls, fullname: str, path=None, target=None):
        # path is a list with a path to the parent package or None if no parent
        names = fullname.split(".")
        tail = names[-1]
        if path:
            try:
                dir_path = path[0]
            except TypeError:
                # _NamespacePath missing __getitem__ on older Pythons
                dir_path = path._path[0]  # type: ignore
        else:
            dir_path = os.getcwd()
        if tail == "tosca_repositories":
            return ModuleSpec(fullname, None, is_package=True)
        elif tail == "service_template":
            # "tosca_repositories" or "unfurl" in names
            filepath = os.path.join(dir_path, "service_template.yaml")
            # XXX look for service-template.yaml or ensemble-template.yaml files
            loader = ToscaYamlLoader(fullname, filepath)
            return spec_from_file_location(
                fullname, filepath, loader=loader, submodule_search_locations=path
            )  # type: ignore
        return None


class ToscaYamlLoader(Loader):
    """Loads a Yaml service template and converts it to Python"""

    def __init__(self, full_name, filepath, modules=None):
        self.full_name = full_name
        self.filepath = filepath
        self.modules = modules

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        # parse to TOSCA template and convert to python
        path = Path(self.filepath)
        if path.suffix in loader_details[1]:
            python_filepath = str(path.parent / (path.stem + ".py"))
            src = yaml_to_python(self.filepath, python_filepath)
        else:
            with open(path) as f:
                src = f.read()
        restricted_exec(
            src, vars(module), path.parent, self.full_name, self.modules, True
        )


class ImmutableModule(ModuleType):
    def __init__(self, name="__builtins__", **kw):
        ModuleType.__init__(self, name)
        super().__getattribute__("__dict__").update(kw)

    def __getattribute__(self, __name: str) -> Any:
        attrs = super().__getattribute__("__dict__")
        if __name not in attrs.get("__safe__", attrs.get("__all__", ())):
            # only allow access to public attributes
            raise AttributeError(__name)
        return super().__getattribute__(__name)

    def __setattr__(self, name, v):
        raise AttributeError(name)

    def __delattr__(self, name):
        raise AttributeError(name)


def load_private_module(base_dir: str, modules: Dict[str, ModuleType], name: str):
    parent = name.rpartition(".")[0]
    if parent:
        if parent not in modules:
            load_private_module(base_dir, modules, parent)
    if name in modules:
        # cf. "Crazy side-effects!" in _bootstrap.py (e.g. parent could have imported child)
        return modules[name]

    origin_path = os.path.join(base_dir, name.replace(".", "/")) + ".py"
    if not os.path.isfile(origin_path):
        raise ModuleNotFoundError("No module named " + name, name=name)
    loader = ToscaYamlLoader(name, origin_path, modules)
    spec = spec_from_loader(name, loader, origin=origin_path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    modules[name] = module
    try:
        spec.loader.exec_module(module)
    except:
        del modules[name]
        raise
    if parent:
        # Set the module as an attribute on its parent.
        parent_module = modules[parent]
        child = name.rpartition(".")[2]
        try:
            setattr(parent_module, child, module)
        except AttributeError:
            msg = f"Cannot set an attribute on {parent!r} for child module {child!r}"
            logger.warning(msg)
    return module


def __safe_import__(
    base_dir: str,
    ALLOWED_MODULES: Sequence[str],
    modules,
    name: str,
    globals=None,
    locals=None,
    fromlist=(),
    level=0,
):
    parts = name.split(".")
    if level == 0:
        if name in modules:
            return modules[name] if fromlist else modules[parts[0]]
        if name in ALLOWED_MODULES:
            if len(parts) > 1:
                first = importlib.import_module(parts[0])
                first = ImmutableModule(parts[0], **vars(first))
                modules[parts[0]] = first
                last = importlib.import_module(name)
                last = ImmutableModule(name, **vars(last))
                modules[name] = last
                # we don't need to worry about _handle_fromlist here because we don't allow importing submodules
                return last if fromlist else first
            else:
                module = importlib.import_module(name)
                module = ImmutableModule(name, **vars(module))
                modules[name] = module
                return module
        elif parts[0] != "tosca_repositories":
            raise ImportError(
                "Import of " + name + " is not permitted", name=name
            )
    else:
        package = globals["__package__"] if globals else None
        importlib._bootstrap._sanity_check(name, package, level)
        name = importlib._bootstrap._resolve_name(name, package, level)

    module = load_private_module(base_dir, modules, name)
    # load user code in our restricted environment
    # see https://github.com/python/cpython/blob/3.11/Lib/importlib/_bootstrap.py#L1207
    importlib._bootstrap._handle_fromlist(
        module, fromlist, lambda name: load_private_module(base_dir, modules, name)
    )
    return module


loader_details = ToscaYamlLoader, [".yaml", ".yml"]
installed = False


def install():
    # insert the path hook ahead of other path hooks
    global installed
    if installed:
        return
    # sys.meta_path.insert(0, RepositoryFinder())
    # XXX needed? this breaks imports in local scope somehow:
    # sys.path_hooks.insert(0, FileFinder.path_hook(loader_details))
    installed = True
    # this break some imports:
    # clear any loaders that might already be in use by the FileFinder
    # sys.path_importer_cache.clear()
    # invalidate_caches()
