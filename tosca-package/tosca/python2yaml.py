# Copyright (c) 2023 Adam Souzis
# SPDX-License-Identifier: MIT
import dataclasses
import inspect
import io
from types import ModuleType
import sys
import os.path
import types
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
)
from typing_extensions import Self
import logging
from pathlib import Path
from toscaparser import topology_template
from ._tosca import (
    ToscaFieldType,
    ToscaObject,
    metadata_to_yaml,
    to_tosca_value,
    ArtifactEntity,
    _DataclassType,
    _OwnedToscaType,
    _ToscaType,
    ToscaType,
    ToscaInputs,
    ToscaOutputs,
    Relationship,
    Node,
    CapabilityType,
    global_state,
    FieldProjection,
    InstanceProxy,
    ValueType,
    _Tosca_Field,
    EvalData,
    Namespace,
    Interface,
)
from .loader import _clear_private_modules, restricted_exec, get_module_path, get_allowed_modules
from . import WritePolicy

logger = logging.getLogger("tosca")


class PythonToYaml:
    def __init__(
        self,
        namespace: dict,
        yaml_cls=dict,
        docstrings=None,
        safe_mode=False,
        modules=None,
        write_policy=WritePolicy.never,
        import_resolver=None,
    ):
        self.globals = namespace
        # use dict because python sets don't preserve insertion order
        self.imports: Dict[Tuple[str, Path], bool] = {}
        self.repos: Dict[str, Path] = {}
        self.yaml_cls = yaml_cls
        self.topology_templates = [yaml_cls()]
        self.sections: Dict[str, Any] = yaml_cls(
            tosca_definitions_version="tosca_simple_unfurl_1_0_0",
            topology_template=self.topology_templates[0],
        )
        self.docstrings = docstrings or {}
        self.safe_mode = safe_mode
        if modules is None:
            self.modules = {} if safe_mode else sys.modules
        else:
            self.modules = modules
        self.write_policy = write_policy
        self.import_resolver = import_resolver
        self.templates: List[ToscaType] = []

    def find_yaml_import(
        self, module_name: str
    ) -> Tuple[Optional[ModuleType], Optional[Path]]:
        "Find the given Python module and corresponding yaml file path"
        # Python import should already have happened
        module = self.modules.get(module_name)
        if not module:
            return None, None
        path = module.__file__
        assert path
        dirname, filename = os.path.split(path)
        before, sep, remainder = filename.rpartition(".")
        glob = before.replace("_", "?") + ".*"
        for p in Path(dirname).glob(glob):
            if p.suffix in [".yaml", ".yml"]:
                return module, p
        return module, None

    def _set_repository_for_module(
        self, module_name: str, path: Path
    ) -> Tuple[str, Optional[Path]]:
        parts = module_name.split(".")
        if parts[0] == "tosca_repositories":
            root_package = parts[0] + "." + parts[1]
            repo_name = parts[1]
        else:
            root_package = parts[0]
            repo_name = parts[0]
        root_module = self.modules.get(root_package)
        if not root_module:
            return "", None
        root_path = root_module.__file__
        if not root_path:
            root_path = get_module_path(root_module)
            repo_path = Path(root_path)
        else:
            repo_path = Path(root_path).parent
        self.repos[repo_name] = repo_path
        try:
            return repo_name, path.relative_to(repo_path)
        except ValueError:
            return repo_name, None

    @staticmethod
    def _add_type(t, types_used):
        for b in t.__mro__:
            if b is ToscaType:
                break
            if not b.__module__.startswith("tosca."):
                types_used.setdefault(b.__module__, {})[b.__name__] = b

    def module2yaml(self, include_types=False) -> dict:
        # module contents will have been set to self.globals
        mode = global_state.mode
        try:
            global_state.mode = "yaml"
            self._namespace2yaml(self.globals)
            if include_types:
                types_used: dict = {}
                for t in self.templates:
                    self._add_type(t.__class__, types_used)
                    for name in t.__annotations__:
                        field = t.get_instance_field(name)
                        if isinstance(field, _Tosca_Field):
                            ti = field.get_type_info_checked()
                            if ti and ti.types and issubclass(ti.types[0], ToscaType):
                                self._add_type(ti.types[0], types_used)
                for module_name, classes in types_used.items():
                    self.globals["__name__"] = module_name
                    self._namespace2yaml(classes)
        finally:
            global_state.mode = mode
        self.add_repositories_and_imports()
        return self.sections

    def add_repositories_and_imports(self) -> None:
        imports = []
        repositories = {}
        for repo, p in self.imports:
            _import = dict(file=str(p))
            if repo and repo != "service_template":
                _import["repository"] = repo
                if not self.import_resolver:
                    repositories[repo] = dict(url=self.repos[repo].as_uri())
                elif not self.import_resolver.get_repository(repo, None):
                    # the repository wasn't found, but don't add it here (this is probably an error)
                    logger.warning(
                        f"Added an import in {repo} but could not find {repo} in {list(self.import_resolver.manifest.repositories)}."
                    )
            imports.append(_import)
        if repositories:
            self.sections.setdefault("repositories", {}).update(repositories)
        if imports:
            self.sections.setdefault("imports", []).extend(imports)

    def add_template(
        self,
        obj: ToscaType,
        name: str = "",
        module_name="",
    ) -> str:
        name = obj._name or name
        for topology_section in reversed(self.topology_templates):
            assert obj._template_section
            section = topology_section.get(obj._template_section)
            if section and name in section:
                return name
            if module_name:
                # only do a shallow search if not adding because of a requirement reference
                break
        section = self.topology_templates[-1].setdefault(
            obj._template_section, self.yaml_cls()
        )
        assert name
        section[name] = obj  # placeholder to prevent circular references
        section[name] = obj.to_template_yaml(self)
        if module_name:
            section[name].setdefault("metadata", {})["module"] = module_name
            obj.register_template(module_name, name)
            if obj.__class__.__module__ != module_name:
                if (
                    obj.__class__.__module__ != "unfurl.tosca_plugins.tosca_ext"
                    and not obj.__class__.__module__.startswith("tosca.")
                ):  # skip built-in modules, we don't need to generate import yaml
                    module_path = self.globals.get("__file__")
                    self._import_module(
                        module_name, module_path, obj.__class__.__module__
                    )
        self.templates.append(obj)
        return name

    def set_requirement_value(
        self, req: dict, field: _Tosca_Field, value, default_node_name: str
    ):
        node = None
        if isinstance(value, Node):
            node = value
        elif isinstance(value, CapabilityType):
            if value._local_name:
                node = value._node
                req["capability"] = value._local_name
            else:
                req["capability"] = value.tosca_type_name()
        elif isinstance(value, Relationship):
            if value._name:  # named, not inline
                rel: Union[str, dict] = self.add_template(value)
            else:  # inline
                rel = value.to_template_yaml(self)
            req["relationship"] = rel
            if isinstance(value._target, Node):
                node = value._target
            elif isinstance(value._target, CapabilityType):  # type: ignore  # XXX
                node = value._target._node
                req["capability"] = value._target._local_name
        elif isinstance(value, EvalData):
            field.add_node_filter(value)
        else:
            raise TypeError(
                f'Invalid value for requirement: "{value}" ({type(value)}) on {field.name}"'
            )
        if node:
            node_name = self.add_template(node, default_node_name)
            req["node"] = node_name
            if len(req) == 1:
                return node_name
        return None

    def _imported_module2yaml(self, module: ModuleType) -> Path:
        try:
            from unfurl.yamlloader import yaml
        except ImportError:
            import yaml

        path = Path(get_module_path(module))
        yaml_path = path.parent / (path.stem + ".yaml")
        if module.__name__.startswith("unfurl"):
            logger.debug(
                "skipping saving imported python module as YAML: "
                "not converting built-in module: %s",
                module.__name__,
            )
            return yaml_path
        if not self.write_policy.can_overwrite(str(path), str(yaml_path)):
            logger.info(
                "skipping saving imported python module as YAML %s: %s",
                yaml_path,
                self.write_policy.deny_message(),
            )
            return yaml_path

        # skip leading / and parts corresponding to the module name
        base_dir = "/".join(path.parts[1 : -len(module.__name__.split("."))])
        with open(path) as sf:
            src = sf.read()
        namespace: dict = dict(__file__=str(path))
        yaml_dict = python_src_to_yaml_obj(
            src,
            namespace,
            base_dir,
            module.__name__,
            self.yaml_cls,
            self.safe_mode,
            self.modules,
            self.write_policy,
            self.import_resolver,
        )
        with open(yaml_path, "w") as yo:
            logger.info("saving imported python module as YAML at %s", yaml_path)
            yaml.dump(yaml_dict, yo)
        return yaml_path

    def add_alias(self, name, type_obj: Type[ToscaType]):
        return {
            name: {
                "derived_from": type_obj.tosca_type_name(),
                "metadata": {"alias": True},
            }
        }

    def _namespace2yaml(self, namespace):
        current_module = self.globals.get(
            "__name__", "builtins"
        )  # exec() adds to builtins
        module_path = self.globals.get("__file__")
        description = self.globals.get("__doc__")
        if description and description.strip():
            self.sections["description"] = description.strip()
        topology_sections = self.topology_templates[-1]
        if not isinstance(namespace, dict):
            names = getattr(namespace, "__all__", None)
            if names is None:
                names = dir(namespace)
            namespace = {name: getattr(namespace, name) for name in names}

        seen = set()
        for name, obj in namespace.items():
            if isinstance(obj, ModuleType):
                continue
            if (
                not isinstance(obj, InstanceProxy)
                and isinstance(obj, Node)
                and not obj._name
            ):
                obj._name = name
        for name, obj in namespace.items():
            if isinstance(obj, ModuleType):
                continue
            if hasattr(obj, "get_defs") and issubclass(obj, Namespace):
                obj.to_yaml(self)
                continue
            module_name: str = getattr(obj, "__module__", "")
            if isinstance(obj, _DataclassType) and issubclass(obj, ToscaType):
                if module_name and module_name != current_module:
                    self._import_module(current_module, module_path, module_name)
                    continue
                # this is a class not an instance
                section = obj._type_section  # type: ignore
                if id(obj) in seen:
                    # name is a alias referenced, treat as subtype in TOSCA
                    as_yaml = self.add_alias(name, obj)
                else:
                    seen.add(id(obj))
                    obj._globals = self.globals  # type: ignore
                    _docstrings = self.docstrings.get(name)
                    if isinstance(_docstrings, dict):
                        obj._docstrings = _docstrings  # type: ignore
                    as_yaml = obj._cls_to_yaml(self)  # type: ignore
                self.sections.setdefault(section, self.yaml_cls()).update(as_yaml)
                if name == "__root__":
                    topology_sections.setdefault(
                        "substitution_mappings", self.yaml_cls()
                    ).update(dict(node_type=obj.tosca_type_name()))
            elif isinstance(obj, ToscaType):
                # XXX this will render any templates that were imported into this namespace from another module
                if (
                    (isinstance(obj, Node) or obj._name)
                    and not isinstance(obj, InstanceProxy)
                ):  # besides node templates, templates that are unnamed (e.g. relationship templates) are included inline where they are referenced
                    if not obj._template_section:
                        if not isinstance(obj, _OwnedToscaType) or not obj._node:
                            logger.warning(
                                "Can't add template '%s', to topology_template, %s isn't a standalone TOSCA type",
                                name,
                                obj._type_section,
                            )
                        continue
                    obj.__class__._globals = self.globals  # type: ignore
                    self.add_template(obj, name, current_module)
                    if name == "__root__":
                        topology_sections.setdefault(
                            "substitution_mappings", self.yaml_cls()
                        ).update(dict(node=obj._name or name))
            else:
                section = getattr(obj, "_template_section", "")
                to_yaml = getattr(obj, "to_yaml", None)
                if section:
                    if module_name and module_name != current_module:
                        self._import_module(current_module, module_path, module_name)
                        continue
                    if isinstance(obj, type) and issubclass(
                        obj, (ValueType, Interface)
                    ):
                        obj._globals = self.globals  # type: ignore
                        _docstrings = self.docstrings.get(name)
                        if isinstance(_docstrings, dict):
                            obj._docstrings = _docstrings  # type: ignore
                        as_yaml = obj._cls_to_yaml(self)
                        self.sections.setdefault(section, self.yaml_cls()).update(
                            as_yaml
                        )
                    else:
                        assert to_yaml
                        parent = self.sections
                        if section in topology_template.SECTIONS:
                            parent = topology_sections
                        parent.setdefault(section, self.yaml_cls()).update(
                            to_yaml(self.yaml_cls)
                        )

    def _import_module(
        self, current_module: str, module_path: Optional[str], module_name: str
    ) -> None:
        # note: should only be called for modules with tosca objects we need to convert to yaml
        if module_name.startswith("tosca."):
            return
        if not module_path:
            logger.warning(
                f"can't import {module_name}: current module {current_module} doesn't have a path"
            )
            return
        # logger.debug(
        #     f"adding import statement to {current_module} in {module_name} at {path}"
        # )
        # this type was imported from another module
        # instead of converting the type, add an import if missing
        module, yaml_path = self.find_yaml_import(module_name)
        if not yaml_path:
            if module:
                #  its a TOSCA object but no yaml file found, convert to yaml now
                yaml_path = self._imported_module2yaml(module)
            else:
                logger.warning(
                    f"Import of {module_name} in {current_module} failed, module wasn't loaded."
                )
                return
        if yaml_path:
            try:
                module_dir = Path(os.path.dirname(module_path))
                if yaml_path.is_absolute():
                    import_path = yaml_path.relative_to(module_dir)
                else:
                    import_path = module_dir / yaml_path
                _key = ("", import_path)
                if _key not in self.imports:
                    self.imports[_key] = True
                    logger.debug(
                        f'"{current_module}" is importing "{module_name}": located at "{import_path}", relative to "{module_path}"'
                    )
            except ValueError:
                # not a subpath of the current module, add a repository
                ns, repo_path = self._set_repository_for_module(module_name, yaml_path)
                if repo_path:
                    _key = (ns, repo_path)
                    if _key not in self.imports:
                        self.imports[_key] = True
                        logger.debug(
                            f'"{current_module}" is importing "{module_name}" in package "{ns}": located at "{repo_path}""'
                        )
                else:
                    if ns:
                        logger.warning(
                            f"import look up in {current_module} failed, {yaml_path} is not in the subpath of {module_path} for {module_name} in package {ns}",
                        )
                    else:
                        logger.warning(
                            f"import look up in {current_module} failed, can't find {module_name}",
                        )

    def _shared_cls_to_yaml(self, cls: Type[ToscaType]) -> dict:
        # XXX version
        dict_cls = self.yaml_cls
        body: Dict[str, Any] = dict_cls()
        super_fields = self.set_bases(cls, body)
        tosca_name = cls.tosca_type_name()
        doc = cls.__doc__ and cls.__doc__.strip()
        if doc:
            body["description"] = doc
        if cls._type_metadata:
            body["metadata"] = metadata_to_yaml(cls._type_metadata)

        for f_cls, field in cls._get_parameter_and_explicit_fields():
            assert field.name, field
            if f_cls._docstrings:
                field.description = f_cls._docstrings.get(field.name)
            item = field.to_yaml(self, super_fields.get(field.name))
            if item:
                if field.section == "requirements":
                    body.setdefault("requirements", []).append(item)
                elif not field.section:  # _target
                    body.update(item)
                else:  # properties, attribute, capabilities, artifacts
                    if f_cls is not cls and f_cls._metadata_key:
                        # its an inherited input or output, set metadata to will be treated as an input when invoking an operation
                        item[field.tosca_name].setdefault("metadata", {})[
                            f_cls._metadata_key
                        ] = f_cls.__name__
                    body.setdefault(field.section, {}).update(item)
                    if field.declare_attribute:
                        # a property that is also declared as an attribute
                        item = {field.tosca_name: field._to_attribute_yaml()}
                        body.setdefault("attributes", {}).update(item)
        interfaces = self._interfaces_yaml(None, cls)
        if interfaces:
            body["interfaces"] = interfaces

        if not body:  # skip this
            return {}
        tpl = dict_cls({tosca_name: body})
        return tpl

    @staticmethod
    def set_bases(t_cls: Type[ToscaObject], body):
        tosca_name = t_cls.tosca_type_name()
        bases: Union[list, str] = [
            b.tosca_type_name() for b in t_cls.tosca_bases() if b != tosca_name
        ]
        super_fields = {}
        if bases:
            if len(bases) == 1:
                bases = bases[0]
            body["derived_from"] = bases
            for b in t_cls.tosca_bases():
                if issubclass(b, ToscaType):
                    super_fields.update(b.__dataclass_fields__)
        return super_fields

    def _interfaces_yaml(
        self,
        obj: Optional["ToscaObject"],
        cls: Type["ToscaObject"],
    ) -> Dict[str, dict]:
        # interfaces are inherited
        cls_or_self = obj or cls
        dict_cls = self.yaml_cls
        interfaces = {}
        interface_ops = {}
        direct_bases: List[str] = []
        for c in cls.__mro__:
            if not issubclass(c, ToscaObject) or c._type_section != "interface_types":
                continue
            name = c.tosca_type_name()
            shortname = name.split(".")[-1]
            i_def: Dict[str, Any] = {}
            if shortname not in [
                "Standard",
                "Configure",
                "Install",
            ] or cls_or_self.tosca_type_name().startswith("tosca."):
                # built-in interfaces don't need their type declared
                i_def["type"] = name
            if cls_or_self._interface_requirements:
                i_def["requirements"] = cls_or_self._interface_requirements
            default_inputs = getattr(cls_or_self, f"_{shortname}_default_inputs", None)
            if default_inputs:
                i_def["inputs"] = to_tosca_value(default_inputs, dict_cls)
            interfaces[shortname] = i_def
            if c in cls.__bases__:
                direct_bases.append(shortname)
            for methodname in c.__dict__:
                if methodname[0] == "_":
                    continue
                interface_ops[methodname] = i_def
                interface_ops[shortname + "." + methodname] = i_def
        self._find_operations(obj, cls, interface_ops, interfaces)
        # filter out interfaces with no operations declared unless inheriting from Interface directly
        return dict_cls(
            (k, v)
            for k, v in interfaces.items()
            if k == "defaults" or k in direct_bases or v.get("operations")
        )

    @staticmethod
    def is_operation(operation) -> bool:
        # exclude Input and Output classes
        return callable(operation) and not isinstance(operation, _DataclassType)

    def _find_operations(
        self,
        obj: Optional["ToscaObject"],
        cls: Type["ToscaObject"],
        interface_ops,
        interfaces,
    ) -> None:
        cls_or_self = obj or cls
        for methodname, operation in cls_or_self.__dict__.items():
            if methodname[0] == "_":
                continue
            if self.is_operation(operation):
                apply_to = getattr(operation, "apply_to", None)
                if apply_to is not None:
                    for name in apply_to:
                        interface = interface_ops.get(name)
                        if interface is not None:
                            # set to null to so they use the default operation
                            interface.setdefault("operations", {})[
                                name.split(".")[-1]
                            ] = None
                    interfaces["defaults"] = self._operation2yaml(obj, cls, operation)
                else:
                    name = getattr(operation, "operation_name", methodname)
                    interface = interface_ops.get(name)
                    if interface is not None:
                        op_def = self._operation2yaml(obj, cls, operation)
                        if (
                            operation.__name__ == "decorator_operation"
                            and cls._docstrings
                        ):  # hack for op = operation() usage
                            description = cls._docstrings.get(methodname)
                            if (
                                description
                                and description.strip()
                                and isinstance(op_def, dict)
                            ):
                                op_def["description"] = description.strip()
                        interface.setdefault("operations", {})[name] = op_def or None

    def _operation2yaml(
        self,
        obj: Optional["ToscaObject"],
        cls: Type["ToscaObject"],
        operation,
    ):
        cls_or_self = obj or cls
        dict_cls = self.yaml_cls
        if self.safe_mode:
            # safe mode skips adding operation implementation because it executes operations to generate the yaml
            return dict_cls(implementation="safe_mode")
        op_def: Dict[str, Any] = dict_cls()
        args: Dict[str, Any] = {}
        kwargs: Dict[str, Any] = {}
        sig = inspect.signature(operation)
        try:
            args, kwargs = _get_parameters(obj, cls, sig)
            result = operation(*list(args.values()), **kwargs)
        except Exception:
            logger.debug(
                f"Couldn't execute {operation} on {cls_or_self} during conversion, falling back to runtime execution",
                exc_info=True,
            )
            className = f"{operation.__module__}:{operation.__qualname__}:render"
            implementation = dict_cls(className=className)
            result = None
        else:
            implementation = dict_cls()
        if result is NotImplemented:
            return "not_implemented"
        if isinstance(result, _ArtifactProxy):
            implementation = dict_cls(primary=result.name_or_tpl)
            ret_cls = _get_toscaoutputs(cls_or_self, sig.return_annotation)
            if ret_cls:
                _set_outputs(operation, self, op_def, ret_cls)
        elif isinstance(result, types.FunctionType):
            className = f"{result.__module__}:{result.__qualname__}:run"
            implementation = dict_cls(className=className)
        elif isinstance(result, ToscaOutputs):
            _set_outputs(operation, self, op_def, result)
            # set result for case where self is an artifact
            result = sig.parameters.get("self")
        elif result:  # with unfurl this should be a Configurator
            className = f"{result.__class__.__module__}.{result.__class__.__name__}"
            implementation = dict_cls(className=className)
        else:  # no return value
            ret_cls = _get_toscaoutputs(cls_or_self, sig.return_annotation)
            if ret_cls:
                _set_outputs(operation, self, op_def, ret_cls)
            # set result for case where self is an artifact
            result = sig.parameters.get("self")
        # XXX add to implementation: preConditions
        for key in ("operation_host", "environment", "timeout", "dependencies"):
            impl_val = getattr(operation, key, None)
            if impl_val is not None:
                implementation[key] = impl_val
        if implementation:
            op_def["implementation"] = to_tosca_value(implementation, dict_cls)
        if hasattr(result, "_inputs"):
            op_def["inputs"] = to_tosca_value(result._inputs, dict_cls)
        args.update(kwargs)
        if args:
            _set_input_defs(op_def, sig, args, self)
        description = getattr(operation, "__doc__", "")
        if description and description.strip():
            op_def["description"] = description.strip()
        for key in ("entry_state", "invoke"):
            impl_val = getattr(operation, key, None)
            if impl_val is not None:
                op_def[key] = to_tosca_value(impl_val, dict_cls)
        return op_def


def _get_toscaoutputs(cls_or_self, ret_val):
    if ret_val is not inspect.Parameter.empty:
        ret_cls = cls_or_self._resolve_class(ret_val)
        if isinstance(ret_cls, type) and issubclass(ret_cls, ToscaOutputs):
            return ret_cls
    return None


def _get_parameters(
    obj: Optional["ToscaObject"], cls: Type["ToscaObject"], sig: inspect.Signature
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cls_or_self = obj or cls
    vargs: Dict[str, Any] = {}
    kwargs: Dict[str, Any] = {}
    args = vargs
    for name, parameter in sig.parameters.items():
        if name == "self":
            args[name] = _ToscaTypeProxy(obj, cls)
            continue
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            args = kwargs
        if parameter.default is not inspect.Parameter.empty:
            if isinstance(parameter.default, _ToscaType):
                args[name] = _ToscaTypeProxy(parameter.default, type(parameter.default))
            else:
                args[name] = parameter.default
        elif parameter.annotation is not inspect.Parameter.empty:
            param_cls = cls_or_self._resolve_class(parameter.annotation)
            if isinstance(param_cls, type) and issubclass(param_cls, _ToscaType):
                args[name] = _ToscaTypeProxy(None, param_cls)
            else:
                args[name] = None
    return vargs, kwargs


def _set_output(outputs: dict, field: _Tosca_Field, converter):
    output_def = field.to_yaml(converter)
    if field.tosca_name in outputs:
        output_def[field.tosca_name]["mapping"] = outputs[field.tosca_name]
    outputs.update(output_def)


def _set_outputs(operation, converter, op_def, tosca_outputs):
    outputs = getattr(operation, "outputs", None) or {}
    op_def.setdefault("metadata", {}).setdefault(ToscaOutputs._metadata_key, []).append(
        tosca_outputs.tosca_type_name()
    )
    for d_field in dataclasses.fields(tosca_outputs):
        if isinstance(d_field, _Tosca_Field):
            _set_output(outputs, d_field, converter)
    if outputs:
        op_def["outputs"] = outputs


def _set_input_def(inputs: dict, field: _Tosca_Field, converter):
    input_def = field.to_yaml(converter)
    if field.tosca_name in inputs:
        input_def[field.tosca_name]["default"] = inputs[field.tosca_name]
    inputs.update(input_def)


def _set_input_defs(
    op_def: dict, sig: inspect.Signature, args: dict, converter
) -> None:
    inputs = op_def.setdefault("inputs", {})
    self = None
    for name, value in args.items():
        if name == "self":
            self = value
            continue
        if isinstance(value, ToscaInputs):
            # set metadata for matching properties
            op_def.setdefault("metadata", {}).setdefault(
                ToscaInputs._metadata_key, []
            ).append(value.tosca_type_name())
            for d_field in dataclasses.fields(value):
                if isinstance(d_field, _Tosca_Field):
                    _set_input_def(inputs, d_field, converter)
        else:
            parameter = sig.parameters[name]
            if parameter.default == inspect.Parameter.empty:
                default = dataclasses.MISSING
            else:
                default = parameter.default
            if parameter.annotation is not inspect.Parameter.empty:
                field: _Tosca_Field = _Tosca_Field(
                    ToscaFieldType.property, default, name=name, owner=self
                )
                field.type = parameter.annotation
            else:
                field = _Tosca_Field.infer_field(self, name, default)
            _set_input_def(inputs, field, converter)


class _ArtifactProxy:
    def __init__(self, name_or_tpl, named):
        self.name_or_tpl = name_or_tpl
        self.named = named

    def execute(self, *args: ToscaInputs, **kw) -> Self:
        self._inputs = ToscaInputs._get_inputs(*args, **kw)
        return self

    def set_inputs(self, *args: "ToscaInputs", **kw):
        self._inputs = ToscaInputs._get_inputs(*args, **kw)

    # XXX: create_configurator

    def to_yaml(self, dict_cls=dict) -> Optional[Dict]:
        if not self.named:
            return dict_cls(get_artifact=["ANON", self.name_or_tpl])
        return dict_cls(get_artifact=["SELF", self.name_or_tpl])


class _ToscaTypeProxy(InstanceProxy):
    """
    Stand-in for ToscaTypes when generating yaml
    """

    def __init__(self, obj: Optional["ToscaObject"], cls: Type["ToscaObject"]):
        self._cls_or_self = obj or cls
        self._cls = cls

    def __getattr__(self, name):
        attr = getattr(self._cls_or_self, name)
        if isinstance(attr, FieldProjection):
            # _FieldDescriptor.__get__ returns a FieldProjection
            if isinstance(attr.field.default, ArtifactEntity) or issubclass(
                attr.field.get_type_info().types[0], ArtifactEntity
            ):
                return _ArtifactProxy(name, True)
            else:
                # this is only called when defining an operation on a type so reset query to be relative
                attr._path = [".", attr.field.as_ref_expr()]
        elif isinstance(attr, ArtifactEntity):
            return _ArtifactProxy(name, True)
        return attr

    def find_artifact(self, name_or_tpl):
        return _ArtifactProxy(name_or_tpl, False)


def python_src_to_yaml_obj(
    python_src: str,
    namespace: Optional[Dict[str, Any]] = None,
    base_dir: str = "",
    full_name: str = "service_template",
    yaml_cls=dict,
    safe_mode: bool = False,
    modules=None,
    write_policy=WritePolicy.never,
    import_resolver=None,
) -> dict:
    if modules is None:
        if safe_mode:
            modules = get_allowed_modules()
        else:
            _clear_private_modules()
            modules = sys.modules
    global_state.modules = modules
    if namespace is None:
        namespace = {}
    result = restricted_exec(
        python_src, namespace, base_dir, full_name, modules, safe_mode
    )
    doc_strings = {
        n[: -len(":doc_strings")]: ds
        for n, ds in result.used_names.items()
        if n.endswith(":doc_strings")
    }
    converter = PythonToYaml(
        namespace,
        yaml_cls,
        doc_strings,
        safe_mode,
        modules,
        write_policy,
        import_resolver,
    )
    yaml_dict = converter.module2yaml()
    return yaml_dict


def python_to_yaml(
    src_path: str,
    dest_path: Optional[str] = None,
    overwrite="auto",
    safe_mode: bool = False,
) -> Optional[dict]:
    try:
        from unfurl.yamlloader import yaml
    except ImportError:
        import yaml
    write_policy = WritePolicy[overwrite]
    if dest_path and not write_policy.can_overwrite(src_path, dest_path):
        logger.info(
            "not saving YAML file at %s: %s", dest_path, write_policy.deny_message()
        )
        return None
    with open(src_path) as f:
        python_src = f.read()
    src_path = os.path.abspath(src_path)
    base_dir = os.path.dirname(src_path)
    # add to sys.path so relative imports work
    sys.path.insert(0, base_dir)
    try:
        namespace: dict = dict(__file__=src_path)
        tosca_tpl = python_src_to_yaml_obj(
            python_src,
            namespace,
            base_dir,
            write_policy=write_policy,
            safe_mode=safe_mode,
        )
    finally:
        try:
            sys.path.pop(sys.path.index(base_dir))
        except ValueError:
            pass
    prologue = write_policy.generate_comment("tosca.python2yaml", src_path)
    if dest_path:
        output = io.StringIO(prologue)
        yaml.dump(tosca_tpl, output)
        logger.info("converted Python to YAML at %s", dest_path)
        with open(dest_path, "w") as f:
            f.write(output.getvalue())
    else:
        print(prologue)
        yaml.dump(tosca_tpl, sys.stdout)
    return tosca_tpl
