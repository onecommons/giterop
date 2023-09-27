# Copyright (c) 2023 Adam Souzis
# SPDX-License-Identifier: MIT
import importlib, importlib.util, importlib._bootstrap
import inspect
from types import ModuleType
import sys
import os.path
from typing import (
    Any,
    Dict,
    Set,
    List,
    Optional,
    Tuple,
    Union,
)
from pathlib import Path
from tosca import Namespace
from toscaparser import topology_template
from _ast import AnnAssign, Assign, ClassDef, Module, With, Expr
from ast import Str, Constant, Name
import ast
import builtins
from ._tosca import (
    _DataclassType,
    ToscaType,
    RelationshipType,
    NodeType,
    CapabilityType,
    global_state,
)
from RestrictedPython import compile_restricted_exec, CompileResult
from RestrictedPython import RestrictingNodeTransformer
from RestrictedPython import safe_builtins, PrintCollector
from RestrictedPython.transformer import ALLOWED_FUNC_NAMES, FORBIDDEN_FUNC_NAMES

# see https://restrictedpython.readthedocs.io/en/latest/usage/basic_usage.html#necessary-setup
# https://github.com/zopefoundation/RestrictedPython
# https://github.com/zopefoundation/zope.untrustedpython
# https://github.com/zopefoundation/AccessControl/blob/master/src/AccessControl/ZopeGuards.py


class PythonToYaml:
    def __init__(
        self,
        namespace: dict,
        yaml_cls=dict,
        docstrings=None,
        safe_mode=False,
        modules=None,
    ):
        self.globals = namespace
        self.imports: Set[Tuple[str, Path]] = set()
        self.repos: Dict[str, Path] = {}
        self.yaml_cls = yaml_cls
        self.sections: Dict[str, Any] = yaml_cls(topology_template=yaml_cls())
        self.docstrings = docstrings or {}
        self.safe_mode = safe_mode
        if modules is None:
            self.modules = {} if safe_mode else sys.modules
        else:
            self.modules = modules

    def find_yaml_import(self, module_name: str) -> Optional[Path]:
        module = self.modules.get(module_name)
        if not module:
            return None
        path = module.__file__
        assert path
        dirname, filename = os.path.split(path)
        before, sep, remainder = filename.rpartition(".")
        glob = before.replace("_", "?") + ".*"
        for p in Path(dirname).glob(glob):
            if p.suffix in [".yaml", ".yml"]:
                return p
        return None

    def find_repo(self, module: str, path: Path):
        parts = module.split(".")
        root_module = parts[0]
        root_path = self.modules[root_module].__file__
        assert root_path
        repo_path = Path(root_path).parent
        self.repos[root_module] = repo_path
        return root_module, path.relative_to(repo_path)

    def module2yaml(self) -> dict:
        mode = global_state.mode
        try:
            global_state.mode = "yaml"
            self._namespace2yaml(self.globals)
        finally:
            global_state.mode = mode
        self.add_repositories_and_imports()
        return self.sections

    def add_repositories_and_imports(self) -> None:
        imports = []
        repositories = {}
        for repo, p in self.imports:
            _import = dict(file=str(p))
            if repo:
                _import["repository"] = repo
                if repo != "unfurl":  # skip built-in repository
                    repositories[repo] = dict(url=self.repos[repo].as_uri())
            imports.append(_import)
        if repositories:
            self.sections.setdefault("repositories", {}).update(repositories)
        if imports:
            self.sections.setdefault("import", []).extend(imports)

    def add_template(self, obj: ToscaType, name: str = "") -> str:
        section = self.sections["topology_template"].setdefault(
            obj._template_section, self.yaml_cls()
        )
        name = obj._name or name
        if name not in section:
            section[name] = obj  # placeholder to prevent circular references
            section[name] = obj.to_template_yaml(self)
        return name

    def set_requirement_value(self, req: dict, value, default_node_name: str):
        node = None
        if isinstance(value, NodeType):
            node = value
        elif isinstance(value, CapabilityType):
            if value._local_name:
                node = value._node
                req["capability"] = value._local_name
            else:
                req["capability"] = value.tosca_type_name()
        elif isinstance(value, RelationshipType):
            if value._name:  # named, not inline
                rel: Union[str, dict] = self.add_template(value)
            else:  # inline
                rel = value.to_template_yaml(self)
            req = self.yaml_cls(relationship=rel)
            if isinstance(value._target, NodeType):
                node = value._target
            elif isinstance(value._target, CapabilityType):
                node = value._target._node
                req["capability"] = value._target._local_name
        if node:
            node_name = self.add_template(node, default_node_name)
            req["node"] = node_name
            if len(req) == 1:
                return node_name
        return None

    def _imported_module2yaml(self, module) -> Path:
        from unfurl.yamlloader import yaml  # XXX

        path = Path(module.__file__)
        base_dir = "/".join(path.parts[1 : -len(module.__name__.split("."))])
        yaml_dict = python_to_yaml(
            inspect.getsource(module),
            None,
            base_dir,
            module.__name__,
            self.yaml_cls,
            self.safe_mode,
            self.modules,
        )
        yaml_path = path.parent / (path.stem + ".yaml")
        with open(yaml_path, "w") as yo:
            yaml.dump(yaml_dict, yo)
        return yaml_path

    def _namespace2yaml(self, namespace):
        current_module = self.globals.get(
            "__name__", "builtins"
        )  # exec() adds to builtins
        path = self.globals.get("__file__")
        description = self.globals.get("__doc__")
        self.sections["tosca_definitions_version"] = "tosca_simple_unfurl_1_0_0"
        if description and description.strip():
            self.sections["description"] = description.strip()
        topology_sections: Dict[str, Any] = self.sections["topology_template"]

        if not isinstance(namespace, dict):
            names = getattr(namespace, "__all__", None)
            if names is None:
                names = dir(namespace)
            namespace = {name: getattr(namespace, name) for name in names}

        for name, obj in namespace.items():
            if isinstance(obj, ModuleType):
                continue
            if hasattr(obj, "get_defs"):  # class Namespace
                self._namespace2yaml(obj.get_defs())
                continue
            module_name: str = getattr(obj, "__module__", "")
            if isinstance(obj, _DataclassType):
                if module_name and module_name != current_module:
                    if not module_name.startswith("tosca."):
                        # this type was imported from another module
                        # instead of converting the type, add an import if missing
                        p = self.find_yaml_import(module_name)
                        if not p:
                            #  its a TOSCA object but no yaml file found, convert to yaml now
                            p = self._imported_module2yaml(self.modules[module_name])
                        if p and path:
                            try:
                                self.imports.add(("", p.relative_to(path)))
                            except ValueError:
                                # not a subpath of the current module, add a repository
                                self.imports.add(self.find_repo(module_name, p))
                    continue

                # this is a class not an instance
                section = obj._type_section  # type: ignore
                obj._globals = self.globals  # type: ignore
                _docstrings = self.docstrings.get(name)
                if isinstance(_docstrings, dict):
                    obj._docstrings = _docstrings  # type: ignore
                as_yaml = obj._cls_to_yaml(self)  # type: ignore
                self.sections.setdefault(section, self.yaml_cls()).update(as_yaml)
            elif isinstance(obj, ToscaType):
                # XXX this will render any templates that were imported into this namespace from another module
                self.add_template(obj, name)
            else:
                section = getattr(obj, "_template_section", None)
                to_yaml = getattr(obj, "to_yaml", None)
                if section:
                    assert to_yaml
                    parent = self.sections
                    if section in topology_template.SECTIONS:
                        parent = topology_sections
                    parent.setdefault(section, self.yaml_cls()).update(
                        to_yaml(self.yaml_cls)
                    )


def doc_str(node):
    if isinstance(node, Expr):
        if isinstance(node.value, Constant) and isinstance(node.value.value, str):
            return node.value.value
        elif isinstance(node.value, Str):
            return str(node.value.s)
    return None


def get_descriptions(body):
    doc_strings = {}
    current_name = None
    for node in body:
        if isinstance(node, AnnAssign) and isinstance(node.target, Name):
            current_name = node.target.id
            continue
        elif current_name and doc_str(node):
            doc_strings[current_name] = doc_str(node)
        current_name = None
    return doc_strings


# python standard library modules matches those added to utility_builtins
ALLOWED_MODULES = (
    "typing",
    "typing_extensions",
    "tosca",
    "unfurl",
    "random",
    "math",
    "string",
    "DateTime",
)


def default_guarded_getattr(ob, name):
    return getattr(ob, name)


def default_guarded_getitem(ob, index):
    # No restrictions.
    return ob[index]


def default_guarded_getiter(ob):
    # No restrictions.
    return ob


def default_guarded_write(ob):
    # No restrictions.
    return ob


def default_guarded_apply(func, args=(), kws={}):
    return func(*args, **kws)


def safe_guarded_write(ob):
    # don't allow objects in the allowlist of modules to be modified
    if getattr(ob, "__module__", "").partition(".")[0] in ALLOWED_MODULES:
        # classes, functions, and methods are all callable
        if not callable(ob) and not isinstance(ob, Namespace):
            return ob
        raise TypeError(
            f"Modifying objects in {ob.__module__} is not permitted: {ob}, {type(ob)}"
        )
    return ob


# XXX
# _inplacevar_
# _iter_unpack_sequence_
# _unpack_sequence_


class ToscaDslNodeTransformer(RestrictingNodeTransformer):
    def __init__(self, errors=None, warnings=None, used_names=None):
        super().__init__(errors, warnings, used_names)

    def _name_ok(self, node, name):
        return True

    def error(self, node, info):
        # visit_Attribute() checks names inline instead of calling check_name()
        # so we have to do the name check this way:
        if 'invalid attribute name because it starts with "_"' in info:
            if self._name_ok(node, node.attr):
                return
        super().error(node, info)

    def check_name(self, node, name, allow_magic_methods=False):
        if not self._name_ok(node, name):
            self.error(node, f'"{name}" is an invalid variable name"')

    def check_import_names(self, node):
        return self.node_contents_visit(node)

    def visit_AnnAssign(self, node: AnnAssign) -> Any:
        # missing in RestrictingNodeTransformer
        return self.node_contents_visit(node)

    def visit_ClassDef(self, node: ClassDef) -> Any:
        # find attribute docs in this class definition
        doc_strings = get_descriptions(node.body)
        self.used_names[node.name] = doc_strings
        return super().visit_ClassDef(node)


ALLOWED_FUNC_NAMES = ALLOWED_FUNC_NAMES | frozenset(["__name__"])
# from foo import *" uses __all__ not __safe__ so allow separately from ALLOWED_MODULES
ALLOWED_IMPORT_STAR_MODULES = ("typing", "typing_extensions", "tosca", "math")


class SafeToscaDslNodeTransformer(ToscaDslNodeTransformer):
    def _name_ok(self, node, name: str):
        if name in FORBIDDEN_FUNC_NAMES:
            return False
        # don't allow dundernames
        if (
            name.startswith("__")
            and name.endswith("__")
            and name not in ALLOWED_FUNC_NAMES
        ):
            return False
        return True

    def check_import_names(self, node):
        # import * is not allowed (to avoid rebinding attacks)
        # unless whitelisted by ALLOWED_IMPORT_STAR_MODULES
        if (
            isinstance(node, ast.ImportFrom)
            and node.module in ALLOWED_IMPORT_STAR_MODULES
            and node.level == 0
            and len(node.names) == 1
            and node.names[0].name == "*"
        ):
            return self.node_contents_visit(node)
        else:
            return RestrictingNodeTransformer.check_import_names(self, node)


def python_to_yaml(
    python_src: str,
    namespace: Optional[Dict[str, Any]] = None,
    base_dir: str = "",
    full_name: str = "service_template",
    yaml_cls=dict,
    safe_mode: bool = False,
    modules=None,
) -> dict:
    if modules is None:
        modules = {}
    if namespace is None:
        namespace = {}
    result = restricted_exec(
        python_src, namespace, base_dir, full_name, modules, safe_mode
    )
    doc_strings = result.used_names
    converter = PythonToYaml(namespace, yaml_cls, doc_strings, safe_mode, modules)
    yaml_dict = converter.module2yaml()
    return yaml_dict


def restricted_exec(
    python_src,
    namespace,
    base_dir,
    full_name="service_template",
    modules=None,
    safe_mode=False,
) -> CompileResult:
    from .loader import __safe_import__

    # package is the full name of module
    # path is base_dir to the root of the package
    package, sep, module_name = full_name.rpartition(".")
    if modules is None:
        modules = {} if safe_mode else sys.modules

    if namespace is None:
        namespace = {}
    tosca_builtins = safe_builtins.copy()
    # https://docs.python.org/3/library/functions.html?highlight=__import__#import__
    safe_import = lambda *args: __safe_import__(
        base_dir, ALLOWED_MODULES, modules, *args
    )
    tosca_builtins["__import__"] = safe_import if safe_mode else __import__
    # we don't restrict read access so add back the safe builtins
    # missing from safe_builtins, only exclude the following:
    # "aiter", "anext", "breakpoint", "compile", "delattr", "dir", "eval", exec, exit, quite, print
    # "globals", "locals", "open", input, setattr, vars, license, copyright, help, credits
    for name in [
        "all",
        "any",
        "ascii",
        "bin",
        "bytearray",
        "classmethod",
        "dict",
        "enumerate",
        "filter",
        "format",
        "frozenset",
        "getattr",
        "hasattr",
        "iter",
        "list",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "property",
        "reversed",
        "set",
        "staticmethod",
        "sum",
        "super",
        "type",
    ]:
        tosca_builtins[name] = getattr(builtins, name)
    namespace.update(
        {
            "_getattr_": default_guarded_getattr,
            "_getitem_": default_guarded_getitem,
            "_getiter_": default_guarded_getiter,
            "_apply_": default_guarded_apply,
            "_write_": safe_guarded_write if safe_mode else default_guarded_write,
            "_print_": PrintCollector,
            "__metaclass__": type,
        }
    )
    namespace["__builtins__"] = tosca_builtins
    namespace["__name__"] = full_name
    if base_dir:
        namespace["__file__"] = (
            os.path.join(base_dir, full_name.replace(".", "/")) + ".py"
        )
    if package:
        namespace["__package__"] = package
    policy = SafeToscaDslNodeTransformer if safe_mode else ToscaDslNodeTransformer
    result = compile_restricted_exec(python_src, policy=policy)
    if result.errors:
        raise SyntaxError("\n".join(result.errors))
    exec(result.code, namespace)
    return result
