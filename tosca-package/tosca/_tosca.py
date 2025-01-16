# Copyright (c) 2023 Adam Souzis
# SPDX-License-Identifier: MIT
import collections.abc
from contextlib import contextmanager
import copy
import dataclasses
from enum import Enum
import functools
import inspect
import threading
import typing
import os.path
import datetime
import re
from typing import (
    Any,
    ClassVar,
    Dict,
    ForwardRef,
    Generic,
    Iterator,
    Mapping,
    MutableMapping,
    NamedTuple,
    NoReturn,
    Sequence,
    Set,
    Union,
    List,
    Optional,
    Type,
    TypeVar,
    Tuple,
    cast,
    overload,
)
import types
from typing_extensions import (
    Protocol,
    Callable,
    Concatenate,
    dataclass_transform,
    get_args,
    get_origin,
    Annotated,
    Literal,
    Self,
    TypeAlias,
)

import sys
import logging


logger = logging.getLogger("tosca")

from toscaparser.elements.datatype import DataType as ToscaParserDataType
from toscaparser import functions
from .scalars import *

if typing.TYPE_CHECKING:
    from .python2yaml import PythonToYaml
    from ._fields import (
        Property,
        Options,
        Attribute,
        Capability,
        Requirement,
        Artifact,
        Computed,
        Output,
    )
else:
    Property = Attribute = Capability = Requirement = Artifact = Computed = Output = (
        None
    )


class _LocalState(threading.local):
    def __init__(self, **kw):
        self.mode = "parse"  # "yaml", "runtime"
        self._in_process_class = False
        self.safe_mode = False
        self.context: Any = None  # orchestrator specific runtime state
        self.modules = {}
        self._type_proxy = None
        self.__dict__.update(kw)


global_state = _LocalState()


def safe_mode() -> bool:
    """This function returns True if running within the Python safe mode sandbox."""
    return global_state.safe_mode


def global_state_mode() -> str:
    """
    This function returns the execution state (either "parse" or "runtime") that the current thread is in.

    Returns "parse" or "runtime"
    """
    return global_state.mode


def global_state_context() -> Any:
    """
    This function returns orchestrator-specific runtime state for the current thread (or None).
    """
    if global_state.safe_mode:
        return None
    else:
        return global_state.context


yaml_cls = dict

JsonObject: TypeAlias = Dict[str, "JsonType"]
JsonType: TypeAlias = Union[
    None, int, float, str, bool, Sequence["JsonType"], Dict[str, "JsonType"]
]


@contextmanager
def set_evaluation_mode(mode: str):
    """
    A context manager that sets the global (per-thread) tosca evaluation mode and restores the previous mode upon exit.
    This is only needed for testing or other special contexts.

    Args:
        mode (str):  "parse" or  "runtime"

    Yields:
        the previous mode

    .. code-block:: python

      with set_evaluation_mode("parse"):
          assert tosca.global_state_mode() == "parse"
    """
    saved = global_state.mode
    try:
        global_state.mode = mode
        yield saved
    finally:
        global_state.mode = saved


class ToscaObject:
    _tosca_name: str = ""
    _interface_requirements: ClassVar[Optional[List[str]]] = None
    _globals: Optional[Dict[str, Any]] = None
    _namespace: Optional[Dict[str, Any]] = None
    _type_section: ClassVar[str] = ""
    _docstrings: ClassVar[Optional[Dict[str, str]]] = None

    @classmethod
    def tosca_type_name(cls) -> str:
        _tosca_name = cls.__dict__.get("_type_name")
        return _tosca_name if _tosca_name else cls.__name__

    def to_yaml(self, dict_cls=dict) -> Optional[Dict]:
        return None

    @classmethod
    def tosca_bases(cls, section=None) -> Iterator[Type["ToscaObject"]]:
        for c in cls.__bases__:
            # only include classes of the same tosca type as this class
            # and exclude the base class defined in this module
            if issubclass(c, ToscaObject):
                if (
                    c._type_section == (section or cls._type_section)
                    and c.__module__ != __name__
                ):
                    yield c

    @classmethod
    def _resolve_class(cls, _type) -> type:
        origin = get_origin(_type)
        if origin:
            if origin is Union:  # also true if origin is Optional
                _type = [a for a in get_args(_type) if a is not type(None)][0]
            elif origin in [Annotated, list, collections.abc.Sequence]:
                _type = get_args(_type)[0]
            else:
                _type = origin
        if isinstance(_type, str):
            if "[" in _type:
                # XXX nested type annotations not supported (note the \w)
                match = re.search(r"\[(\w+)\]", _type)
                if match and match.group(1):
                    _type = match.group(1)
                else:
                    raise NameError(f"invalid type annotation: {_type}")
            return cls._lookup_class(_type)
        elif isinstance(_type, ForwardRef):
            return cls._resolve_class(_type.__forward_arg__)
        else:
            return _type

    @classmethod
    def _lookup_class(cls, qname: str) -> type:
        names = qname.split(".")
        name = names.pop(0)
        if cls._globals:
            globals = cls._globals
        else:
            globals = {}
        # global_state.modules get priority
        if cls.__module__ in global_state.modules:
            mod_globals = global_state.modules[cls.__module__].__dict__
        elif cls.__module__ in sys.modules and cls.__module__ != "builtins":
            mod_globals = sys.modules[cls.__module__].__dict__
        else:
            mod_globals = {}
        locals = cls._namespace or {}
        obj = locals.get(name, globals.get(name, mod_globals.get(name)))
        if obj is None:
            if name == cls.__name__:
                obj = cls
            elif name in sys.modules:
                if not names:
                    raise TypeError(f"{qname} is a module, not a class")
                obj = sys.modules[name]
            else:
                raise NameError(f"{qname} not found in {cls.__name__}'s scope")
        while names:
            name = names.pop(0)
            ns = obj
            obj = getattr(obj, name, None)
            if obj is None:
                raise AttributeError(f"can't find {name} in {qname}")
        return cls._resolve_class(obj)


T = TypeVar("T")


class DataConstraint(ToscaObject, Generic[T]):
    """
    Base class for :tosca_spec:`TOSCA property constraints <_Toc50125233>`. A subclass exists for each of those constraints.

    These can be passed as `Property` and `Attribute` field specifiers or as a Python type annotations.
    """

    def __init__(self, constraint: T):
        self.constraint = constraint

    def to_yaml(self, dict_cls=dict) -> Optional[Dict]:
        return {self.__class__.__name__: to_tosca_value(self.constraint)}

    def apply_constraint(self, val: T) -> bool:
        assert isinstance(val, FieldProjection), val
        val.apply_constraint(self)
        return True


class equal(DataConstraint[T]):
    pass


class greater_than(DataConstraint[T]):
    pass


class greater_or_equal(DataConstraint[T]):
    pass


class less_than(DataConstraint[T]):
    pass


class less_or_equal(DataConstraint[T]):
    pass


class in_range(DataConstraint[T]):
    def __init__(self, min: T, max: T):
        self.constraint = [min, max]  # type: ignore


class valid_values(DataConstraint[T]):
    pass


class length(DataConstraint[T]):
    pass


class min_length(DataConstraint[T]):
    pass


class max_length(DataConstraint[T]):
    pass


class pattern(DataConstraint[T]):
    pass


class schema(DataConstraint[T]):
    pass


class Namespace(types.SimpleNamespace):
    @classmethod
    def get_defs(cls) -> Dict[str, Any]:
        ignore = ("__doc__", "__module__", "__dict__", "__weakref__", "_tosca_name")
        return {k: v for k, v in cls.__dict__.items() if k not in ignore}

    @classmethod
    def set_name(cls, obj, name):
        parent_name = getattr(cls, "_tosca_name", cls.__name__)
        obj._name = parent_name + "." + name

    @classmethod
    def to_yaml(cls, converter: "PythonToYaml") -> None:
        if __name__ != cls.__module__:  # must be subclass
            converter._namespace2yaml(cls.get_defs())


class ServiceTemplate(Namespace):
    @classmethod
    def set_name(cls, obj, name):
        obj._name = name


class DeploymentBlueprint(Namespace):
    _fields = ("_cloud", "_title", "_description", "_visibility")

    @classmethod
    def set_name(cls, obj, name):
        obj._name = name

    @classmethod
    def get_defs(cls) -> Dict[str, Any]:
        ignore = (
            "to_yaml",
            "_fields",
            "get_defs",
            "__doc__",
            "__module__",
            "__dict__",
            "__weakref__",
            "_tosca_name",
        )
        return {k: v for k, v in cls.__dict__.items() if k not in ignore + cls._fields}

    @classmethod
    def to_yaml(cls, converter: "PythonToYaml") -> None:
        name = cls.__dict__.get("_tosca_name", cls.__name__)
        if name == "DeploymentBlueprint":  # must be subclass
            return
        blueprints = converter.sections.setdefault(
            "deployment_blueprints", converter.yaml_cls()
        )
        blueprint = blueprints[name] = converter.yaml_cls()
        for fieldname in cls._fields:
            field = cls.__dict__.get(fieldname)
            if field:
                if fieldname == "_cloud" and hasattr(field, "tosca_type_name"):
                    field = field.tosca_type_name()
                blueprint[fieldname[1:]] = field
        converter.topology_templates.append(blueprint)
        converter._namespace2yaml(cls.get_defs())
        converter.topology_templates.pop()


F = TypeVar("F", bound=Callable[..., Any], covariant=False)


class OperationFunc(Protocol):
    __name__: str
    operation_name: str
    apply_to: Optional[Sequence[str]]
    timeout: Optional[float]
    operation_host: Optional[str]
    environment: Optional[Dict[str, str]]
    dependencies: Optional[List[Union[str, Dict[str, Any]]]]
    outputs: Optional[Dict[str, Optional[str]]]
    entry_state: Optional[str]
    invoke: Optional[str]
    metadata: Optional[Dict[str, JsonType]]


@overload
def operation(
    func: None = None,
    *,
    name="",
    apply_to: Optional[Sequence[str]] = None,
    timeout: Optional[float] = None,
    operation_host: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    dependencies: Optional[List[Union[str, Dict[str, Any]]]] = None,
    outputs: Optional[Dict[str, Optional[str]]] = None,
    entry_state: Optional[str] = None,
    invoke: Optional[str] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
) -> Callable: ...


@overload
def operation(func: F) -> F: ...


def operation(
    func: Optional[F] = None,
    *,
    name="",
    apply_to: Optional[Sequence[str]] = None,
    timeout: Optional[float] = None,
    operation_host: Optional[str] = None,
    environment: Optional[Dict[str, str]] = None,
    dependencies: Optional[List[Union[str, Dict[str, Any]]]] = None,
    outputs: Optional[Dict[str, Optional[str]]] = None,
    entry_state: Optional[str] = None,
    invoke: Optional[str] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
) -> Union[F, Callable[[F], F]]:
    """Function decorator that marks a function or methods as a TOSCA operation.

    Args:
        name (str, optional): Name of the TOSCA operation. Defaults to the name of the method.
        apply_to (Sequence[str], optional): List of TOSCA operations to apply this method to. If omitted, match by the operation name.
        timeout (float, optional): Timeout for the operation (in seconds). Defaults to None.
        operation_host (str, optional): The name of host where this operation will be executed. Defaults to None.
        environment (Dict[str, str], optional): A map of environment variables to use while executing the operation. Defaults to None.
        dependencies (List[Union[str, Dict[str, Any]]], optional): List of artifacts this operation depends on. Defaults to None.
        outputs (Dict[str, str], optional): TOSCA outputs mapping. Defaults to None.
        entry_state (str, optional): Node state required to invoke this operation. Defaults to None.
        invoke (str, optional): Name of operation to delegate this operation to. Defaults to None.
        metadata (Dict[str, JSON], optional): Dictionary of metadata to associate with the operation. Defaults to None.

    This example marks a method a implementing the ``create`` and ``delete`` operations on the ``Standard`` TOSCA interface.

    .. code-block:: python

        @operation(apply_to=["Standard.create", "Standard.delete"])
        def default(self):
            return self.my_artifact.execute()

    If you wish to declare an abstract operation on a custom interface without specifying its signature, assign ``operation()`` directly, for example:

    .. code-block:: python

        class MyInterface(tosca.interfaces.Root):
            my_operation = operation()
            "Invoke this method to perform my_operation"

    This will avoid static type-check errors when subclasses declare a method implementing the operation.
    """

    def decorator_operation(func_: F) -> F:
        func = cast(OperationFunc, func_)
        func.operation_name = name or func.__name__
        func.apply_to = apply_to
        func.timeout = timeout
        func.operation_host = operation_host
        func.environment = environment
        func.dependencies = dependencies
        func.outputs = outputs
        func.entry_state = entry_state
        func.invoke = invoke
        func.metadata = metadata
        return func_

    if func:  # when used as decorator without "()", i.e. @operation
        return decorator_operation(func)
    # when used as @operation() or op = operation():
    return decorator_operation


class NodeTemplateDirective(str, Enum):
    "Node Template :tosca_spec:`directives<_Toc50125217>`."

    select = "select"
    "Match with instance in external ensemble"

    substitute = "substitute"
    "Create a nested topology"

    default = "default"
    "Only use this template if one with the same name isn't already defined in the root topology."

    dependent = "dependent"
    "Exclude from plan generation"

    virtual = "virtual"
    "Don't instantiate"

    check = "check"
    "Run check operation before deploying"

    discover = "discover"
    "Discover (instead of create)"

    protected = "protected"
    "Don't delete."

    def __str__(self) -> str:
        return self.value


class tosca_timestamp(str):
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({super().__repr__()})"


class tosca_version(str):
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({super().__repr__()})"


#  see constraints.PROPERTY_TYPES
TOSCA_SIMPLE_TYPES: Dict[str, str] = dict(
    integer="int",
    string="str",
    boolean="bool",
    float="float",
    number="float",
    timestamp="tosca_timestamp",
    map="Dict",
    list="List",
    version="tosca_version",
    any="object",
    range="Tuple[int, int]",
)
TOSCA_SIMPLE_TYPES.update({
    "scalar-unit.size": "Size",
    "scalar-unit.frequency": "Frequency",
    "scalar-unit.time": "Time",
    "scalar-unit.bitrate": "Bitrate",
})

PYTHON_TO_TOSCA_TYPES = {v: k for k, v in TOSCA_SIMPLE_TYPES.items()}
PYTHON_TO_TOSCA_TYPES.update({
    "Tuple": "range",
    "tuple": "range",
    "dict": "map",
    "list": "list",
    "EvalData": "any",
})

TOSCA_SHORT_NAMES = {
    "PortDef": "tosca.datatypes.network.PortDef",
    "PortSpec": "tosca.datatypes.network.PortSpec",
}


def _to_union(types):
    if len(types) == 3:
        return Union[types[0], types[1], types[2]]
    elif len(types) == 2:
        return Union[types[0], types[1]]
    else:
        return ForwardRef(types[0])


def _get_type_name(_type):
    # work-around _SpecialType limitations in older Python versions
    return getattr(_type, "__name__", getattr(_type, "_name", ""))


def get_optional_type(_type) -> Tuple[bool, Any]:
    # if not optional return false, type
    # else return true, type or type
    if isinstance(_type, ForwardRef):
        _type = _type.__forward_arg__
    if isinstance(_type, str):
        union = [t.strip() for t in _type.split("|")]
        try:
            union.remove("None")
            return True, _to_union(union)
        except ValueError:
            return False, _to_union(union)
    args = get_args(_type)
    origin = get_origin(_type)
    if (
        origin
        and _get_type_name(origin) in ["Union", "UnionType"]
        and type(None) in args
    ):
        _types = [arg for arg in args if arg is not type(None)]
        if not _types:
            return True, type(None)
        elif len(_types) > 1:  # return origin type
            return True, _type
        else:
            return True, _types[0]
    return False, _type


Collection_Types = (list, collections.abc.Sequence, dict)


class TypeInfo(NamedTuple):
    optional: bool
    # keep in sync with collection_types:
    collection: Optional[Union[Type[tuple], Type[list], Type[dict]]]
    types: tuple
    metadata: Any

    def is_sequence(self):
        return self.collection in (tuple, list)

    @property
    def simple_types(self) -> tuple:
        return tuple(
            (t.simple_type() if issubclass(t, ValueType) else t) for t in self.types
        )

    def instance_check(self, value: Any) -> bool:
        if self.optional and value is None:
            return True
        if self.collection:
            if isinstance(value, Collection_Types):
                for item in value:
                    if not isinstance(item, self.simple_types):
                        return False
                return True
            return False
        elif isinstance(value, self.types):
            return True
        return False


def pytype_to_tosca_type(_type, as_str=False) -> TypeInfo:
    optional, _type = get_optional_type(_type)
    origin = get_origin(_type)
    if origin is Annotated:
        metadata = _type.__metadata__[0]
        _type = get_args(_type)[0]
    else:
        metadata = None
    origin = get_origin(_type)
    collection = None
    if origin == collections.abc.Sequence:
        collection = list
    elif origin in Collection_Types:
        collection = origin
    if collection:
        args = get_args(_type)
        if args:
            _type = get_args(_type)[1 if origin is dict else 0]
        else:
            _type = Any
        origin = get_origin(_type)

    if isinstance(_type, ForwardRef):
        types: tuple = tuple(
            ForwardRef(t.strip()) for t in _type.__forward_arg__.split("|")
        )
    elif _get_type_name(origin) in ["Union", "UnionType"]:
        types = get_args(_type)
    else:
        types = (_type,)

    def to_str(_type):
        if isinstance(_type, ForwardRef):
            return _type.__forward_arg__
        elif not isinstance(_type, str):
            return _type.__name__
        else:
            return _type

    if as_str:
        types = tuple(to_str(t) for t in types)
    else:
        types = tuple(object if t == Any else t for t in types)
    return TypeInfo(optional, collection, types, metadata)


def to_tosca_value(obj, dict_cls=dict):
    if isinstance(obj, dict):
        return dict_cls((k, to_tosca_value(v, dict_cls)) for k, v in obj.items())
    elif isinstance(obj, list):
        return [to_tosca_value(v, dict_cls) for v in obj]
    else:
        to_yaml = getattr(obj, "to_yaml", None)
        if to_yaml:  # e.g. datatypes, _Scalar
            return to_yaml(dict_cls)
        else:
            # XXX coerce to compatible json type or raise error
            return obj


def metadata_to_yaml(metadata: Mapping):
    return yaml_cls(metadata)


class ToscaFieldType(Enum):
    # value corresponds to its section in a node template
    property = "properties"
    attribute = "attributes"
    capability = "capabilities"
    requirement = "requirements"
    artifact = "artifacts"
    builtin = ""


class _REQUIRED_TYPE:
    "sentinel object"


REQUIRED = _REQUIRED_TYPE()


MISSING = dataclasses.MISSING


class _DEFAULT_TYPE:
    pass


DEFAULT: Any = _DEFAULT_TYPE()


class _CONSTRAINED_TYPE:
    pass


CONSTRAINED: Any = _CONSTRAINED_TYPE()


_T = TypeVar("_T")


def placeholder(cls: Type[_T]) -> _T:
    "Returns None but makes the type checker happy."
    return cast(_T, None)


class _Tosca_Field(dataclasses.Field, Generic[_T]):
    title = None
    relationship: Union[str, Type["Relationship"], None] = None
    capability: Union[str, Type["CapabilityEntity"], None] = None
    node: Union[str, Type["Node"], None] = None
    node_filter: Optional[Dict[str, Any]] = None
    valid_source_types: Optional[List[str]] = None

    def __init__(
        self,
        field_type: Optional[ToscaFieldType],
        default=dataclasses.MISSING,
        default_factory=dataclasses.MISSING,
        name: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        title: str = "",
        status: str = "",
        constraints: Optional[List[DataConstraint]] = None,
        options: Optional["Options"] = None,
        declare_attribute: bool = False,
        owner: Optional[Type["_ToscaType"]] = None,
        mapping: Union[None, str, List[str]] = None,
    ):
        if metadata is None:
            metadata = {}
        args = [
            self,
            default,
            default_factory,
            field_type != ToscaFieldType.attribute,  # init
            True,  # repr
            None,  # hash
            True,  # compare
            metadata or {},
        ]
        _has_default = (
            default is not dataclasses.MISSING
            or default_factory is not dataclasses.MISSING
        )
        if sys.version_info.minor > 9:
            args.append(True)  # kw_only
        elif not _has_default:
            # we have to have all fields have a default value
            # because the ToscaType base classes have init fields with default values
            # and python < 3.10 dataclasses will raise an error
            args[1] = REQUIRED  # set default
        dataclasses.Field.__init__(*args)
        self.owner = owner
        self._tosca_field_type = field_type
        self._tosca_name = name
        # note self.name and self.type are set later (in dataclasses._get_field)
        self.description: Optional[str] = None  # set in _shared_cls_to_yaml
        self.title = title
        self.status = status
        self.declare_attribute = declare_attribute
        self.constraints: List[DataConstraint] = constraints or []
        if options:
            options.set_options(self)
        self.deferred_property_assignments: Dict[str, Any] = {}
        self._type_info: Optional[TypeInfo] = None
        self.owner_type: Optional[Type["_ToscaType"]] = None
        self.mapping = mapping

    def set_constraint(self, val):
        # this called via _class_init
        if isinstance(val, EvalData):
            if self._tosca_field_type in [
                ToscaFieldType.capability,
                ToscaFieldType.artifact,
            ]:
                raise AttributeError(
                    "can not set {val} on {self}: {self._tosca_field_type} attributes can't be references"
                )
            if self._tosca_field_type == ToscaFieldType.requirement:
                # if requirement and value is a Ref, set a node filter
                self.add_node_filter(val)
                return
        # val is a concrete value or self is a property or attribute
        # either way, set the default
        self._set_default(val)

    def set_property_constraint(self, name: str, val: Any):
        # this called via _class_init
        # if self is a requirement, name is a property on the target node or the relationship
        if self._tosca_field_type == ToscaFieldType.requirement:
            # if requirement, set a node filter (val can be Ref or concrete value)
            self.add_node_filter(val, name)
            return

        # if self is a capability or artifact, name is a property on the capability or artifact
        # if self is property or attribute, name is a field on the value (which must be a datatype or map)
        if (self.default is MISSING and self.default_factory is MISSING) or isinstance(
            self.default, EvalData
        ):
            # there's no value to set the attribute on!
            raise AttributeError(
                "can not set value for {name} on {self}: property constraints require a concrete default value"
            )
        elif self.default_factory is not MISSING:
            # default exists but not created until object initialization
            self.deferred_property_assignments[name] = val
        else:
            # XXX validate name is valid property and that val is compatible type
            # XXX mark default as a constraint
            # XXX default is shared across template instances and subtypes -- what about mutable values like dicts and basically all Toscatypes?
            setattr(self.default, name, val)

    def _validate_name_is_property(self, name):
        # ensure that the give field name refers to a property or attribute
        assert self._tosca_field_type == ToscaFieldType.requirement
        ti = self.get_type_info_checked()
        if not ti:
            logger.warning(
                f"Could't check property {name} on {self.name}, unable to resolve type."
            )
            return
        # XXX ti.types[0] might not be a node type!
        field = ti.types[0].__dataclass_fields__.get(name)
        if not field:
            # in _cls_init_() __dataclass_fields__ aren't set yet and the class attribute is the field
            field = getattr(ti.types[0], name, None)
            if not isinstance(field, _Tosca_Field):
                logger.warning(f"Property {name} is not present on {ti.types[0]}")
                return
        if field.tosca_field_type not in [
            ToscaFieldType.property,
            ToscaFieldType.attribute,
        ]:
            raise ValueError(
                f'{ti.types} Can not set "{name}" on {self}: "{name}" is a {field.tosca_field_type.name}, not a TOSCA property'
            )

    def add_node_filter(
        self, val, prop_name: Optional[str] = None, capability: Optional[str] = None
    ):
        assert self._tosca_field_type == ToscaFieldType.requirement
        if self.node_filter is None:
            self.node_filter = {}
        self._set_node_filter_constraint(self.node_filter, val, prop_name, capability)

    def _set_node_filter_constraint(
        self,
        root_node_filter: dict,
        val,
        prop_name: Optional[str] = None,
        capability: Optional[str] = None,
    ):
        if capability:
            assert prop_name
            cap_filters = root_node_filter.setdefault("capabilities", [])
            for cap_filter in cap_filters:
                if list(cap_filter)[0] == capability:
                    node_filter = cap_filter[capability]
                    break
            else:
                node_filter = {}
                cap_filters.append({capability: node_filter})
        else:
            node_filter = root_node_filter
        if prop_name is not None:
            if not capability:
                self._validate_name_is_property(prop_name)
            prop_filters = node_filter.setdefault("properties", [])
            if isinstance(val, EvalData):
                val = val.set_start("$SOURCE")
            elif isinstance(val, DataConstraint):
                val = val.to_yaml()
            else:
                # XXX validate that val is compatible type
                val = {
                    "q": val
                }  # quote the value to distinguish from built-in tosca node_filters
            prop_filters.append({prop_name: val})
        else:
            match_filters = root_node_filter.setdefault("match", [])
            if isinstance(val, _DataclassType) and issubclass(val, ToscaType):
                # its a DataType class
                val = dict(get_nodes_of_type=val.tosca_type_name())
            else:
                # XXX if val is a node, create ref:
                # val = EvalData({"eval": "::"+ val._name})
                assert isinstance(val, EvalData), val
            if val not in match_filters:
                match_filters.append(val)

    def _set_default(self, val):
        if isinstance(val, DataConstraint):
            if self.tosca_field_type not in [
                ToscaFieldType.property,
                ToscaFieldType.attribute,
            ]:
                raise ValueError(
                    "Value constraints can not be assigned to a TOSCA "
                    + self.tosca_field_type.name
                )
            else:
                self.constraints.append(val)
        else:
            # XXX we can be smarter based on val type, e.g. node or relationship template and merge with the existing default values
            # XXX validate that val is compatible type
            # XXX mark default as a constraint
            # XXX default is shared across template instances and subtypes -- what about mutable values like dicts and basically all Toscatypes?
            self.default = val

    def as_ref_expr(self) -> str:
        if self.tosca_field_type in [
            ToscaFieldType.property,
            ToscaFieldType.attribute,
        ]:
            return self.tosca_name
        elif self.tosca_field_type == ToscaFieldType.requirement:
            return ".targets::" + self.tosca_name
            # but if accessing the relationship template, need to use the form below
        elif self.name == "_target":  # special case
            return ".target"
        else:
            assert self.tosca_field_type
            return f".{self.tosca_field_type.value}::[.name={self.tosca_name}]"

    def _resolve_class(self, _type):
        assert self.owner, (self, _type)
        return self.owner._resolve_class(_type)

    def get_type_info(self) -> TypeInfo:
        if not self._type_info:
            type_info = pytype_to_tosca_type(self.type)
            types = tuple(self._resolve_class(t) for t in type_info.types)
            self._type_info = type_info._replace(types=types)
        return self._type_info

    def get_type_info_checked(self) -> Optional[TypeInfo]:
        try:
            return self.get_type_info()
        except NameError as e:
            logger.warning("error while converting python to yaml: " + str(e))
            return None

    def guess_field_type(self) -> ToscaFieldType:
        type_info = self.get_type_info()
        has_capability = False
        field_type = ToscaFieldType.property
        for _type in type_info.types:
            if not isinstance(_type, type):
                continue
            if issubclass(_type, Node) or issubclass(_type, Relationship):
                field_type = ToscaFieldType.requirement
                break
            elif issubclass(_type, ArtifactEntity):
                field_type = ToscaFieldType.artifact
                break
            elif issubclass(_type, CapabilityEntity):
                has_capability = True
        else:
            if has_capability:
                field_type = ToscaFieldType.capability
        return field_type

    @property
    def tosca_field_type(self) -> ToscaFieldType:
        if self._tosca_field_type is None:
            self._tosca_field_type = self.guess_field_type()
        return self._tosca_field_type

    @property
    def tosca_name(self) -> str:
        return self._tosca_name or self.name

    @property
    def section(self) -> str:
        return self.tosca_field_type.value

    def make_default(self) -> Any:
        return lambda: (
            self.get_type_info().collection or self.get_type_info().types[0]
        )()

    def to_yaml(
        self,
        converter: Optional["PythonToYaml"],
        super_field: Optional["_Tosca_Field"] = None,
    ) -> dict:
        if self.tosca_field_type == ToscaFieldType.property:
            field_def = self._to_property_yaml()
        elif self.tosca_field_type == ToscaFieldType.attribute:
            field_def = self._to_attribute_yaml()
        elif self.tosca_field_type == ToscaFieldType.requirement:
            field_def = self._to_requirement_yaml(converter, super_field)
        elif self.tosca_field_type == ToscaFieldType.capability:
            field_def = self._to_capability_yaml(super_field)
        elif self.tosca_field_type == ToscaFieldType.artifact:
            field_def = self._to_artifact_yaml(converter)
        elif self.name == "_target":  # _target handled in _to_requirement_yaml
            return {}
        else:
            assert False
        # note: description needs to be set when parsing ast
        if self.description:
            field_def["description"] = self.description
        if self.metadata:
            field_def.setdefault("metadata", {}).update(metadata_to_yaml(self.metadata))
        return {self.tosca_name: field_def}

    def _get_occurrences(self):
        occurrences = [1, 1]
        info = self.get_type_info_checked()
        if not info:
            return occurrences
        if info.optional or self.default == ():
            occurrences[0] = 0
        if info.collection is list:
            occurrences[1] = "UNBOUNDED"  # type: ignore
        return occurrences

    def _add_occurrences(self, field_def: dict, default=(1, 1)) -> None:
        occurrences = self._get_occurrences()
        if occurrences != list(default):
            field_def["occurrences"] = occurrences

    def _resolve_toscaname(self, candidate) -> str:
        if isinstance(candidate, str):
            try:
                candidate = self._resolve_class(candidate)
            except NameError as e:
                return candidate
        return candidate.tosca_type_name()

    def _to_requirement_yaml(
        self, converter: Optional["PythonToYaml"], super_field: Optional["_Tosca_Field"]
    ) -> Dict[str, Any]:
        req_def: Dict[str, Any] = yaml_cls()
        if self.node:
            req_def["node"] = self._resolve_toscaname(self.node)
        if self.capability:
            req_def["capability"] = self._resolve_toscaname(self.capability)
        if self.relationship:
            req_def["relationship"] = self._resolve_toscaname(self.relationship)
        info = self.get_type_info_checked()
        if not info:
            return req_def
        target_typeinfo = None
        for _type in info.types:
            if issubclass(_type, Relationship):
                req_def["relationship"] = _type.tosca_type_name()
                target_field = _type.__dataclass_fields__.get("_target")
                target_typeinfo = cast(
                    _Tosca_Field, target_field
                ).get_type_info_checked()
            elif issubclass(_type, CapabilityEntity):
                req_def["capability"] = _type.tosca_type_name()
            elif issubclass(_type, Node):
                req_def["node"] = _type.tosca_type_name()
        if "node" not in req_def and target_typeinfo:
            req_def["node"] = target_typeinfo.types[0].tosca_type_name()
        if self.node_filter:
            req_def["node_filter"] = to_tosca_value(self.node_filter)
        if converter:
            # set node or relationship name if default value is a node or relationship template
            if self.default_factory and self.default_factory is not dataclasses.MISSING:
                default = self.default_factory()
            else:
                default = self.default  # type: ignore
            if default is CONSTRAINED:
                if not self.node_filter:
                    raise ValueError(
                        f'"{self.name}" on "{self.owner}" was marked as CONSTRAINED but no constraint was set in "_class_init()".'
                    )
            elif default and default not in [MISSING, REQUIRED]:
                converter.set_requirement_value(req_def, self, default, self.name)
        if super_field:
            default_occurrences = super_field._get_occurrences()
        else:
            default_occurrences = [1, 1]
        self._add_occurrences(req_def, default_occurrences)
        return req_def

    def _to_capability_yaml(
        self, super_field: Optional["_Tosca_Field"]
    ) -> Dict[str, Any]:
        info = self.get_type_info_checked()
        if not info:
            return yaml_cls()
        assert len(info.types) == 1
        _type = info.types[0]
        assert issubclass(_type, _ToscaType), (self, _type)
        cap_def: dict = yaml_cls(type=_type.tosca_type_name())
        if super_field:
            default_occurrences = super_field._get_occurrences()
        else:
            default_occurrences = [1, 1]
        self._add_occurrences(cap_def, default_occurrences)
        # XXX if self.default or self.default_factory: save properties
        if self.valid_source_types:  # is not None: XXX only set to [] if declared
            cap_def["valid_source_types"] = self.valid_source_types
        return cap_def

    def _to_artifact_yaml(self, converter: Optional["PythonToYaml"]) -> Dict[str, Any]:
        if (
            self.default
            and self.default is not MISSING
            and self.default is not CONSTRAINED
            and self.default is not REQUIRED
        ):
            return self.default.to_template_yaml(converter)
        elif self.default_factory and self.default_factory is not dataclasses.MISSING:
            return self.default_factory().to_template_yaml(converter)
        info = self.get_type_info_checked()
        if not info:
            return yaml_cls()
        assert len(info.types) == 1
        _type = info.types[0]
        assert issubclass(_type, _ToscaType), (self, _type)
        type_only_def: dict = yaml_cls(type=_type.tosca_type_name())
        return type_only_def

    def pytype_to_tosca_schema(self, _type) -> Tuple[dict, bool]:
        # dict[str, list[int, constraint], constraint]
        info = pytype_to_tosca_type(_type)
        assert len(info.types) == 1, info
        _type = info.types[0]
        schema: Dict[str, Any] = {}
        if info.collection is dict:
            tosca_type = "map"
        elif info.collection is list:
            tosca_type = "list"
        else:
            _type = self._resolve_class(_type)
            tosca_type = PYTHON_TO_TOSCA_TYPES.get(_get_type_name(_type), "")
            if not tosca_type:  # it must be a datatype
                if not issubclass(_type, _BaseDataType):
                    raise TypeError(f"unrecognized value type: {_type}")
                tosca_type = _type.tosca_type_name()
                metadata = _type._get_property_metadata()
                if metadata:
                    schema["metadata"] = metadata
        schema["type"] = tosca_type
        if info.collection:
            entry_schema = self.pytype_to_tosca_schema(_type)[0]
            if len(entry_schema) > 1 or entry_schema["type"] != "any":
                schema["entry_schema"] = entry_schema
        if info.metadata:
            schema["constraints"] = [
                c.to_yaml() for c in info.metadata if isinstance(c, DataConstraint)
            ]
        return schema, info.optional

    def _to_attribute_yaml(self) -> dict:
        # self.type is from __annotations__
        prop_def, optional = self.pytype_to_tosca_schema(self.type)
        if self.constraints:
            prop_def.setdefault("constraints", []).extend(
                c.to_yaml() for c in self.constraints
            )
        if self.default_factory and self.default_factory is not dataclasses.MISSING:
            prop_def["default"] = to_tosca_value(self.default_factory())
        elif (
            self.default is not dataclasses.MISSING
            and self.default is not REQUIRED
            and self.default is not CONSTRAINED
            and self.default is not None
        ):
            # only set the default to null if required (not optional)
            prop_def["default"] = to_tosca_value(self.default)
        if self.title:
            prop_def["title"] = self.title
        if self.status:
            prop_def["status"] = self.status
        if self.mapping:  # outputs only
            prop_def["mapping"] = self.mapping
        return prop_def

    def _to_property_yaml(self) -> dict:
        # self.type is from __annotations__
        prop_def, optional = self.pytype_to_tosca_schema(self.type)
        if optional:  # omit if required is True (the default)
            prop_def["required"] = False
        if self.constraints:
            prop_def.setdefault("constraints", []).extend(
                c.to_yaml() for c in self.constraints
            )
        default_field = self.owner._default_key if self.owner else "default"
        if self.default_factory and self.default_factory is not dataclasses.MISSING:
            prop_def[default_field] = to_tosca_value(self.default_factory())
        elif (
            self.default is not dataclasses.MISSING
            and self.default is not REQUIRED
            and self.default is not CONSTRAINED
        ):
            if self.default is not None or not optional:
                # only set the default to null when if property is required
                prop_def[default_field] = to_tosca_value(self.default)
        if self.title:
            prop_def["title"] = self.title
        if self.status:
            prop_def["status"] = self.status
        return prop_def

    @staticmethod
    def infer_field(owner_class, name, value):
        if isinstance(value, _Tosca_Field):
            value.name = name
            if (
                not value.type
                and value.default
                not in [dataclasses.MISSING, REQUIRED, DEFAULT, CONSTRAINED]
                and not isinstance(value.default, (EvalData, _TemplateRef))
            ):
                value.type = type(value.default)
            return value
        field = _Tosca_Field[_T](None, owner=owner_class, default=value)
        field.name = name
        if isinstance(value, FieldProjection):
            field.type = value.field.type
            field._tosca_field_type = value.field._tosca_field_type
        else:
            field.type = type(value)
        return field


_EvalDataExpr = Union[str, None, Dict[str, Any], List[Any]]


class _GetName:
    # use this to lazily evaluate a template's name because might not be set correctly until yaml generation time.
    def __init__(self, obj: Union["ToscaType", Type["ToscaType"]]):
        self.obj = obj

    def __str__(self) -> str:
        if self.obj._type_name in ("inputs", "outputs"):
            return f"root::{self.obj._type_name}"
        if isinstance(self.obj, _OwnedToscaType):
            return self.obj.get_embedded_name() or self.obj._name
        return self.obj._name


class EvalData:
    "An internal wrapper around JSON/YAML data that may contain TOSCA functions or eval expressions and will be evaluated at runtime."

    def __init__(
        self,
        expr: Union["EvalData", _EvalDataExpr],
        path: Optional[List[Union[str, _GetName]]] = None,
    ):
        if isinstance(expr, EvalData):
            expr = expr.expr
        elif callable(expr):
            expr = {"eval": dict(computed=f"{expr.__module__}:{expr.__qualname__}")}
        self._expr: _EvalDataExpr = expr
        self._path = path
        self._foreach = None
        # NB: need to update FieldProjection.__setattr__ if adding an attribute here

    @property
    def as_expr(self) -> _EvalDataExpr:
        if not self._path:
            expr = self._expr
        else:
            expr = {"eval": "::".join([str(segment) for segment in self._path])}
        if self._foreach is not None:
            if isinstance(expr, dict):
                expr["foreach"] = self._foreach
            else:
                raise ValueError(f"cannot set foreach on {expr}")
        return expr

    def to_yaml(self, dict_cls=None):
        return to_tosca_value(self.expr, dict_cls or yaml_cls)

    @property
    def expr(self) -> _EvalDataExpr:
        try:
            from unfurl.result import serialize_value

            return serialize_value(self.as_expr)
        except ImportError:
            return self.as_expr  # in case this package is used outside of unfurl

    def as_ref(self, options=None):
        from unfurl.result import serialize_value

        if options:
            return serialize_value(self.expr, **options)
        return serialize_value(self.expr)

    def set_start(self, root) -> Self:
        # set source if expr is relative
        if self._path:
            # leading empty string means absolute path ("::".join(_path))
            if self._path[0] != "":
                new = copy.copy(self)
                new._path = copy.copy(new._path)
                assert new._path
                new._path.insert(0, root)
                return new
        elif isinstance(self._expr, dict):
            expr = self._expr.get("eval")
            if expr and isinstance(expr, str) and expr[0] not in ["$", ":"]:
                new = copy.copy(self)
                new._expr = copy.copy(self._expr)
                new._expr["eval"] = root + "::" + expr
                return new
        return self

    def set_foreach(self, foreach):
        self._foreach = foreach

    def map(self, func: "EvalData") -> "EvalData":
        # return a copy of self with a  "foreach" clause added
        # that applies ``func`` to each item.
        # assumes ``func`` is an expression function that takes one argument and sets that argument to ``$item``.
        if (
            isinstance(func.expr, dict)
            and functions.is_function(func.expr)
            and isinstance(self.expr, dict)
            and functions.is_function(self.expr)
        ):
            ref = copy.deepcopy(self.expr)
            map_expr = copy.deepcopy(func.expr)
            inner = map_expr and map_expr["eval"]
            if isinstance(inner, dict):
                name = next(iter(inner))  # assume first key is the function name
                inner[name] = {"eval": "$item"}
                ref["foreach"] = map_expr
                return EvalData(ref)
        raise ValueError(f"cannot map {self.expr} with {func.expr}")

    def __getattr__(self, name) -> NoReturn:
        e = AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'"
        )
        if sys.version_info >= (3, 11):
            e.add_note(
                "To handle EvalData here, move this logic to a function decorated with @unfurl.tosca_plugins.expr.runtime_func"
            )
        raise e

    def _op(self, op, other) -> "EvalData":
        return EvalData({"eval": {op: [self, other]}})

    def __add__(self, other) -> "EvalData":
        return self._op("add", other)

    def __sub__(self, other) -> "EvalData":
        return self._op("sub", other)

    def __mul__(self, other) -> "EvalData":
        return self._op("mul", other)

    def __truediv__(self, other) -> "EvalData":
        return self._op("truediv", other)

    def __floordiv__(self, other) -> "EvalData":
        return self._op("floordiv", other)

    def __mod__(self, other) -> "EvalData":
        return self._op("mod", other)

    def __str__(self) -> str:
        """Represent this as a jinja2 expression so we can embed expressions in f-strings"""
        expr = self.expr
        if isinstance(expr, dict):
            expr = expr.get("eval")
            if isinstance(expr, str):
                jinja = f"'{expr}' | eval"
            else:
                jinja = f"{self.expr} | map_value"
            return "{{ " + jinja + " }}"
        elif isinstance(expr, list):
            return "{{ " + str(expr) + "| map_value }}"
        return str(expr or "")

    def __repr__(self):
        return f"EvalData({self.expr})"

    # note: we need this to prevent dataclasses error on 3.11+: mutable default for field
    def __hash__(self) -> int:
        return hash(str(self.expr))

    def __eq__(self, __value: object) -> bool:
        expr = self.expr
        if isinstance(__value, type(expr)):
            return expr == __value
        elif isinstance(__value, EvalData):
            return expr == __value.expr
        return False


_Ref = EvalData


def Eval(value: Any) -> Any:
    "Use this function to specify that a value is or contains a TOSCA function or eval expressions. For example, for property default values."
    # Field specifier for declaring a TOSCA {name}.
    if global_state.mode == "runtime":
        return value
    else:
        return EvalData(value)


# XXX class RefList(Ref)


class _TemplateRef:
    def __init__(self, name: str):
        self.name = name

    def to_yaml(self, *ignore) -> str:
        # assume this will be used in contexts where the template is referenced by name
        return self.name


class NodeTemplateRef(_TemplateRef):
    "Use this to refer to TOSCA node templates that are not visible to your Python code."


class RelationshipTemplateRef(_TemplateRef):
    "Use this to refer to TOSCA relationship templates are not visible to your Python code"


def find_node(name: str) -> Any:
    return NodeTemplateRef(name)


def find_relationship(name: str) -> Any:
    return RelationshipTemplateRef(name)


class FieldProjection(EvalData):
    "A reference to a tosca field or projection off a tosca field"

    # created by _DataclassTypeProxy, invoked via _class_init

    def __init__(self, field: _Tosca_Field, parent: Optional["FieldProjection"] = None):
        # currently don't support projections that are requirements
        expr = field.as_ref_expr()
        if parent and isinstance(parent.expr, dict) and "eval" in parent.expr:
            # XXX map to tosca name but we can't do this now because it might be too early to resolve the attribute's type
            expr = parent.expr["eval"] + "::" + expr
        super().__init__(dict(eval=expr))
        self.field = field
        self.parent = parent

    def __getattr__(self, name):
        # unfortunately _class_init is called during class construction type
        # so _resolve_class might not work with forward references defined in the same module
        field = object.__getattribute__(self, "field")
        try:
            ti = self.field.get_type_info()
        except NameError:
            # couldn't resolve the type
            # XXX if current field type isn't a requirement we can assume name is property
            raise AttributeError(
                f'Can\'t project "{name}" from "{self}": Could not resolve {self.type}'
            )
        if not issubclass(ti.types[0], ToscaType):
            # we're a regular value, can't project field (raise AttributeError with a note)
            return super().__getattr__(name)
        cls = ti.types[0]
        if global_state._type_proxy:
            proxied = global_state._type_proxy.handleattr(self, name)
            if proxied is not MISSING:  # handled
                return proxied
        field = cls.__dataclass_fields__.get(name)
        if not field:
            # __dataclass_fields__ might not be updated yet, do a regular getattr
            field = getattr(cls, name)
            if not isinstance(field, _Tosca_Field):
                raise AttributeError(f"{cls} has no field '{name}'")
        return FieldProjection(field, self)

    def __getitem__(self, key):
        indexed = FieldProjection(self.field, self.parent)
        if isinstance(indexed.expr, dict):
            expr = indexed.expr.get("eval")
            if expr and isinstance(expr, str):
                indexed.expr["eval"] = f"{expr}::{key}"
        return indexed

    def get_requirement_filter(self, tosca_name: str):
        """
        node_filter:
            requirements:
              - host:
                  description: A compute instance with at least 2000 MB of RAM memory.
        """
        if self.parent:
            if self.parent.field.tosca_field_type == ToscaFieldType.requirement:
                node_filter = self.parent.get_requirement_filter(self.field.tosca_name)
            else:
                raise ValueError(
                    f"Can't create a requirement_filter on {self}: Only one level of field projection currently supported"
                )
        else:
            if self.field.node_filter is None:
                self.field.node_filter = {}
            node_filter = self.field.node_filter
        req_filters = node_filter.setdefault("requirements", [])
        for req_filter in req_filters:
            if tosca_name in req_filter:
                return req_filter[tosca_name].setdefault("node_filter", {})
        req_filter = {}
        req_filters.append({tosca_name: {"node_filter": req_filter}})
        return req_filter

    @property
    def tosca_name(self):
        return self.field.tosca_name

    def __setattr__(self, name, val):
        if name in ["_expr", "_path", "_foreach", "field", "parent", "tosca_name"]:
            object.__setattr__(self, name, val)
            return

        if self.parent:
            if self.parent.field.tosca_field_type == ToscaFieldType.requirement:
                self.set_requirement_constraint(val, name, None)
            else:
                raise ValueError(
                    f"Can't set {name} on {self}: Only one level of field projection currently supported"
                )
        else:
            self.field.set_property_constraint(name, val)

    def __delattr__(self, name):
        raise AttributeError(name)

    def set_requirement_constraint(self, val, name, capability):
        assert self.field.tosca_field_type == ToscaFieldType.requirement
        if self.parent:
            node_filter = self.parent.get_requirement_filter(self.field.tosca_name)
            self.field._set_node_filter_constraint(node_filter, val, name)
        else:
            self.field.add_node_filter(val, name, capability)

    def apply_constraint(self, c: DataConstraint):
        if self.field.tosca_field_type in [
            ToscaFieldType.property,
            ToscaFieldType.attribute,
        ]:
            if (
                self.parent
                and self.parent.field.tosca_field_type == ToscaFieldType.capability
            ):
                capability = self.parent.field.tosca_name
                parent = self.parent.parent
                if (
                    not parent
                    or parent.field.tosca_field_type != ToscaFieldType.requirement
                ):
                    raise ValueError(
                        "Can not create node filter on capability '{capability}', expression doesn't reference a requirement."
                    )
            else:
                parent = self.parent
                capability = None
            if parent and parent.field.tosca_field_type == ToscaFieldType.requirement:
                parent.set_requirement_constraint(c, self.field.tosca_name, capability)
            else:
                self.field.constraints.append(c)
        else:
            raise ValueError(
                "Value constraints can not be assigned to a TOSCA "
                + self.field.tosca_field_type.name
            )


def get_annotations(o):
    # return __annotations__ (but not on base classes)
    # see https://docs.python.org/3/howto/annotations.html
    if hasattr(inspect, "get_annotations"):
        # this calls eval
        return inspect.get_annotations(o)  # 3.10 and later
    if isinstance(o, type):  # < 3.10
        return o.__dict__.get("__annotations__", None)
    else:
        return getattr(o, "__annotations__", None)


class _DataclassTypeProxy:
    # this is wraps the data type class passed to _class_init
    # we need this to because __setattr__ and __set__ descriptors don't work on cls attributes
    # (and __set_name__ isn't called after class initialization)

    def __init__(self, cls):
        self.cls = cls

    def __getattr__(self, name):
        # we need to check the base class's __dataclass_fields__ first
        fields = getattr(self.cls, "__dataclass_fields__", {})
        val = fields.get(name)
        if not val:
            # but our __dataclass_fields__ isn't updated yet, do a regular getattr
            val = getattr(self.cls, name)
        if isinstance(val, _Tosca_Field):
            return FieldProjection(val, None)
        return val

    def __setattr__(self, name, val):
        if name == "cls":
            object.__setattr__(self, name, val)
        elif not hasattr(self.cls, name):
            setattr(self.cls, name, val)
        else:
            attr = getattr(self.cls, name)
            if isinstance(attr, _Tosca_Field):
                attr.set_constraint(val)
            else:
                setattr(self.cls, name, val)


def is_data_field(obj) -> bool:
    # exclude Input and Output classes
    return (
        not callable(obj)
        and not inspect.ismethoddescriptor(obj)
        and not inspect.isdatadescriptor(obj)
    )


def _make_dataclass(cls):
    kw = dict(
        init=True,
        repr=True,
        eq=True,
        order=False,
        unsafe_hash=True,
        frozen=False,
    )
    if sys.version_info.minor > 9:
        kw["match_args"] = True
        kw["kw_only"] = True
        kw["slots"] = False
    if sys.version_info.minor > 10:
        kw["weakref_slot"] = False
    # we need _Tosca_Fields not dataclasses.Fields
    # so for any declarations of tosca fields (properties, requirements, etc)
    # missing a _Tosca_Fields, set one before calling _process_class()
    global_state.mode = "parse"
    global_state._in_process_class = True
    try:
        annotations = cls.__dict__.get("__annotations__")
        if annotations:
            for name, annotation in annotations.items():
                if annotation is Callable or annotation == "Callable":
                    continue
                if name[0] != "_" or name in ["_target"]:
                    field = None
                    default = getattr(cls, name, REQUIRED)
                    if not isinstance(default, dataclasses.Field):
                        base_field = cls.__dataclass_fields__.get(name)
                        if isinstance(base_field, _Tosca_Field):
                            field = _Tosca_Field(
                                base_field._tosca_field_type, default, owner=cls
                            )
                        else:
                            if default is not REQUIRED and name not in cls.__dict__:
                                # attribute is defined on a base class but its not a tosca field
                                # XXX maybe allow default if its compatible with the annotation?
                                default = REQUIRED  # so don't use it as the default
                                # (and the type checker should flag this if the types aren't compatible)
                            # XXX or not InitVar or ClassVar
                            field = _Tosca_Field(None, default, owner=cls)
                        setattr(cls, name, field)
                    elif isinstance(default, _Tosca_Field):
                        default.owner = cls
                        field = default
                    if field:
                        field.name = name
                        field.type = annotation
                        if default is DEFAULT:
                            field.default = MISSING
                            field.default_factory = field.make_default()
                        cls._post_field_init(field)
        else:
            annotations = {}
            cls.__annotations__ = annotations
        if (
            cls.__module__ != __name__
        ):  # if class is in a different module than this file
            for name, value in cls.__dict__.items():
                if name[0] != "_" and name not in annotations and is_data_field(value):
                    base_field = cls.__dataclass_fields__.get(name)
                    if base_field:
                        field = _Tosca_Field(
                            getattr(
                                base_field, "_tosca_field_type", ToscaFieldType.property
                            ),
                            value,
                            owner=cls,
                        )
                        # avoid type(None) or type(())
                        field.type = base_field.type if not value else type(value)
                    else:
                        # for unannotated class attributes try to infer if they are TOSCA fields
                        field = _Tosca_Field.infer_field(cls, name, value)
                    if field:
                        annotations[name] = field.type
                        cls._post_field_init(field)
                        setattr(cls, name, field)

        _class_init = cls.__dict__.get("_class_init")
        if _class_init:
            global_state._in_process_class = False
            # _class_init should be a classmethod descriptor
            _class_init.__get__(None, _DataclassTypeProxy(cls))()
            global_state._in_process_class = True
        if not getattr(cls, "__doc__"):
            cls.__doc__ = " "  # suppress dataclass doc string generation
        assert cls.__module__ in sys.modules, (
            cls.__module__
        )  # _process_class checks this
        cls = dataclasses._process_class(cls, **kw)  # type: ignore
        # note: _process_class replaces each field with its default value (or deletes the attribute)
        # replace those with _FieldDescriptors to allow class level attribute access to be customized
        for name in annotations:
            if name[0] != "_":
                field = cls.__dataclass_fields__.get(name)
                if field and isinstance(field, _Tosca_Field):
                    setattr(cls, name, _FieldDescriptor(field))
    finally:
        global_state._in_process_class = False
    return cls


_PT = TypeVar("_PT", bound="ToscaType")


class InstanceProxy(Generic[_PT]):
    """
    Base class for integrating with an TOSCA orchestrator.
    Subclasses of this class can impersonate ToscaTypes and proxy values from the equivalent instances managed by the orchestrator.
    """

    _cls: Type[_PT]

    def __str__(self):
        return f"<{self.__class__.__name__} of {self._cls} at {hex(id(self))}>"

    def __repr__(self):
        return f"<{self.__class__.__name__} of {self._cls} at {hex(id(self))}>"


class _DataclassType(type):
    def __set_name__(self, owner, name):
        if issubclass(owner, Namespace):
            # this will set the class attribute on the class being declared in the Namespace
            self._namespace = owner.get_defs()

    def __new__(cls, name, bases, dct):
        x = super().__new__(cls, name, bases, dct)
        x = _make_dataclass(x)
        if not global_state.safe_mode:
            x.register_type(dct.get("_type_name", name))  # type: ignore
        return x

    def __instancecheck__(cls, inst):
        """Implement isinstance(inst, cls)."""
        if isinstance(inst, InstanceProxy):
            return issubclass(inst._cls, cls)
        return type.__instancecheck__(cls, inst)

    def __subclasscheck__(cls, sub):
        """Implement issubclass(sub, cls)."""
        try:
            if issubclass(sub, InstanceProxy):
                sub = sub._cls
        except:  # sub is not a class
            logging.error(f"subclasscheck {sub} for {cls} failed: {type(sub)}")
            return False
        return type.__subclasscheck__(cls, sub)


class _Tosca_Fields_Getter:
    def __get__(self, obj, objtype=None) -> List[_Tosca_Field]:
        # only get the fields explicitly declared on the obj or class
        target = obj or objtype
        annotations = target.__dict__.get("__annotations__", {})
        return [
            f
            for f in dataclasses.fields(target)
            if isinstance(f, _Tosca_Field) and f.name in annotations
        ]


class _FieldDescriptor:
    """Set on _ToscaTypes to allow class level attribute access to be customized"""

    def __init__(self, field: _Tosca_Field):
        self.field = field
        if callable(self.field.default):
            raise ValueError(f"bad default for {self.field.name}")

    def __get__(self, obj, obj_type):
        if obj or global_state._in_process_class:
            return self.field.default
        else:  # attribute access on the class
            projection = FieldProjection(self.field, None)
            # XXX add validation key to eval to assert one result only
            if obj_type._type_name == "inputs":
                projection._expr = dict(get_input=self.field.tosca_name)
            else:
                if obj_type._type_name == "outputs":
                    selector = "root::outputs"
                else:
                    selector = f"[.type={obj_type.tosca_type_name()}]"
                projection._path = [
                    "",
                    selector,
                    self.field.as_ref_expr(),
                ]
            return projection


def field(
    *,
    default=dataclasses.MISSING,
    default_factory=dataclasses.MISSING,
    kw_only=dataclasses.MISSING,
    builtin=False,
) -> Any:
    kw: Dict[str, Any] = dict(default=default, default_factory=default_factory)
    if sys.version_info.minor > 9:
        kw["kw_only"] = kw_only
        if default is REQUIRED:
            # we don't need this default placeholder set if Python supports kw_only fields
            kw["default"] = dataclasses.MISSING
    elif default == MISSING and default_factory == MISSING:
        # we need this dummy value because this argument can't be marked as keyword only on older Pythons
        # and this parameter probably will come after one without a default value
        kw["default"] = REQUIRED
    if builtin:
        return _Tosca_Field(ToscaFieldType.builtin, default, default_factory)
    return dataclasses.field(**kw)


@dataclass_transform(
    kw_only_default=True,
    field_specifiers=(
        Attribute,
        Property,
        Capability,
        Requirement,
        Artifact,
        field,
        dataclasses.field,
        Computed,
        Output,
    ),
)
class _ToscaType(ToscaObject, metaclass=_DataclassType):
    # we need this intermediate type because the base class with the @dataclass_transform can't specify fields
    # NB: _name needs to come first for python < 3.10, so we can't set any non-classvars here
    explicit_tosca_fields = _Tosca_Fields_Getter()  # list of _ToscaFields

    # see RestrictedPython/Guards.py
    _guarded_writes: ClassVar[bool] = True

    # subtypes can registry themselves, so we can map TOSCA type names to a class
    _all_types: ClassVar[Dict[str, Type["_ToscaType"]]] = {}
    # subtypes can registry themselves, so we can map TOSCA template names to instances
    # section_name => Map((module_name, template_name) => instance)
    _all_templates: ClassVar[Dict[str, Dict[Tuple[str, str], "_ToscaType"]]] = {}
    _metadata_key: ClassVar[str] = ""

    @classmethod
    def register_type(cls, type_name):
        cls._all_types[type_name] = cls

    @classmethod
    def get_field_from_tosca_name(
        cls, tosca_name, tosca_type: ToscaFieldType
    ) -> Optional[_Tosca_Field]:
        for field in cls.__dataclass_fields__.values():  # type: ignore
            if (
                isinstance(field, _Tosca_Field)
                and field.tosca_name == tosca_name
                and field.tosca_field_type == tosca_type
            ):
                return field
        return None

    @classmethod
    def _post_field_init(cls, field: _Tosca_Field) -> _Tosca_Field:
        return field

    if not typing.TYPE_CHECKING:

        def __getattribute__(self, name: str):
            # the only times we want the actual value of a tosca field returned
            # is during yaml generation or directly executing a plan
            # but when constructing a topology return absolute Refs to fields instead
            if global_state._in_process_class:
                expr = _field_as_eval(self, name, False)
                if expr:
                    return expr
            elif global_state._type_proxy:
                proxied = global_state._type_proxy.handleattr(self, name)
                if proxied is not MISSING:  # not handled
                    return proxied
            val = object.__getattribute__(self, name)
            if global_state.mode == "parse" or global_state.mode == "spec":
                # return field expr if val is an expr or if field is a TOSCA attribute
                expr = _field_as_eval(self, name, not isinstance(val, EvalData))
                if expr:
                    return expr
            if isinstance(val, _ToscaType):
                val._set_parent(self, name)
            return val

    # XXX to enable this check, need to work-around internal attributes are being set
    # def __setattr__(self, __name: str, __value: Any) -> None:
    #     if (
    #         global_state.mode == "runtime"
    #         and getattr(self, '_initialized', False)
    #         and __name != "_instance_fields"
    #     ):
    #         # in runtime mode, the proxy will set the attribute on the instance, so this shouldn't happen
    #         raise dataclasses.FrozenInstanceError(
    #             f"Templates can not be modified at runtime ({__name})."
    #         )
    #     return super().__setattr__(__name, __value)

    def __delattr__(self, __name: str) -> None:
        if global_state.mode == "runtime" and getattr(self, "_initialized", False):
            # in runtime mode, the proxy will delete the attribute on the instance, so this shouldn't happen
            raise dataclasses.FrozenInstanceError(
                f"Templates can not be modified at runtime."
            )
        return super().__delattr__(__name)

    @classmethod
    def _class_set_config_spec(cls, kw: dict, target) -> dict:
        return kw

    def _set_config_spec_(self, kw: dict, target) -> dict:
        return self._class_set_config_spec(kw, target)

    def _set_parent(self, parent: "_ToscaType", name: str):
        pass

    _default_key: ClassVar[str] = "default"


def _field_as_eval(
    tt: "ToscaType", name: str, attribute_only: bool
) -> Optional[EvalData]:
    fields = object.__getattribute__(tt, "__dataclass_fields__")
    field = fields.get(name)
    if field and isinstance(field, _Tosca_Field):
        if not attribute_only or field.tosca_field_type == ToscaFieldType.attribute:
            return EvalData(None, ["", _GetName(tt), field.as_ref_expr()])
    return None


class ToscaInputs(_ToscaType):
    _metadata_key: ClassVar[str] = "input_match"

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        dict_cls = converter and converter.yaml_cls or yaml_cls
        body: Dict[str, Any] = dict_cls()
        for field in cls.explicit_tosca_fields:
            assert field.name, field
            item = field.to_yaml(converter)
            body.update(item)
        return body

    @classmethod
    def _post_field_init(cls, field: _Tosca_Field) -> _Tosca_Field:
        field.owner_type = ToscaInputs
        return field

    @staticmethod
    def _get_inputs(*args: "ToscaInputs", **kw):
        inputs = yaml_cls()
        for arg in args:
            assert isinstance(arg, ToscaInputs), arg
            # XXX only get fields on tosca input classes
            for field in arg.__dataclass_fields__.values():
                # only include fields declared on a ToscaInput subtype, not inherited
                if (
                    isinstance(field, _Tosca_Field)
                    and field.owner
                    and field.owner_type is ToscaInputs
                ):
                    val = getattr(arg, field.name, dataclasses.MISSING)
                    if val != dataclasses.MISSING and val != REQUIRED:
                        if val is not None or field.default is REQUIRED:
                            # only set field with None if the field is required
                            if val != field.default:
                                # don't set if matches default
                                inputs[field.tosca_name] = val
        inputs.update(kw)
        return inputs

    def to_yaml(self, dict_cls=yaml_cls):
        body = dict_cls()
        for field in dataclasses.fields(self):
            if isinstance(field, _Tosca_Field):
                input_def = field.to_yaml(None)
                input_def[field.tosca_name]["default"] = getattr(self, field.name)
                body.update(input_def)
        return body


class ToscaOutputs(_ToscaType):
    _metadata_key: ClassVar[str] = "output_match"

    @classmethod
    def _post_field_init(cls, field: _Tosca_Field) -> _Tosca_Field:
        field.owner_type = ToscaOutputs
        field._tosca_field_type = ToscaFieldType.attribute
        return field


class anymethod:
    def __init__(self, func: Callable, keyword=None):
        self.func = func
        self.keyword = keyword

    def __get__(self, obj, objtype) -> Callable:
        if self.keyword:
            return functools.partial(self.func, **{self.keyword: obj or objtype})
        else:
            return functools.partial(self.func, obj or objtype)


def _get_field(cls_or_obj, name):
    if not isinstance(cls_or_obj, type):
        return cls_or_obj.get_instance_field(name)
    else:
        return cls_or_obj.__dataclass_fields__.get(name)  # type: ignore


def _search(
    prop_ref: Any,
    axis: str,
    cls_or_obj=None,
) -> EvalData:
    field, req_name = _get_field_from_prop_ref(prop_ref)
    if field:
        key = field.as_ref_expr()
    else:
        key = req_name  # no field was provided, assume its just a regular property
    prefix = _get_expr_prefix(cls_or_obj)
    expr = prefix + [axis, key]
    if field:
        ref = FieldProjection(field)
        ref._path = expr
        return ref
    else:
        return EvalData(None, expr)


def find_configured_by(
    field_name: _T,
    cls_or_obj=None,
) -> _T:
    """
    find_configured_by(field_name: str | FieldProjection)

    Transitively search for ``field_name`` along the ``.configured_by`` axis (see `Special keys`) and return the first match.

    For example:

    .. code-block:: python

        class A(Node):
          pass

        class B(Node):
          url: str
          connects_to: A = tosca.Requirement(relationship=unfurl.relationships.Configures)

        a = A()
        b = B(connects_to=a, url="https://example.com")

        >>> a.find_configured_by(B.url)
        "https://example.com"

    If called during class definition this will return an eval expression.
    If called as a classmethod or as a free function it will evaluate in the current context.

    Args:
        field_name (str | FieldProjection): Either the name of the field, or for a more type safety, a reference to the field (e.g. ``B.url`` in the example above).

    Returns:
        Any: The value of the referenced field
    """
    return cast(_T, _search(field_name, ".configured_by", cls_or_obj))


def find_hosted_on(
    field_name: _T,
    cls_or_obj=None,
) -> _T:
    """
    find_hosted_on(field_name: str | FieldProjection)

    Transitively search for ``field_name`` along the ``.hosted_on`` axis (see `Special keys`) and return the first match.

    .. code-block:: python

        class A(Node):
          url: str

        class B(Node):
          host: A = tosca.Requirement(relationship=tosca.relationships.HostedOn)

        a = A(url="https://example.com")
        b = B(host=a)

        >>> b.find_hosted_on(A.url)
        "https://example.com"

    If called during class definition this will return an eval expression.
    If called as a classmethod or as a free function it will evaluate in the current context.

    Args:
        field_name (str | FieldProjection): Either the name of the field, or for a more type safety, a reference to the field (e.g. ``A.url`` in the example above).

    Returns:
        Any: The value of the referenced field
    """
    return cast(_T, _search(field_name, ".hosted_on", cls_or_obj))


# XXX make unconditional when type_extensions 4.13 is released (and use Self)
if sys.version_info >= (3, 11):
    _OperationFunc = Callable[Concatenate["ToscaType", ...], Any]
else:
    _OperationFunc = Callable


class ToscaType(_ToscaType):
    "Base class for TOSCA type definitions."

    # NB: _name needs to come first for python < 3.10
    _name: str = field(default="", kw_only=False)
    _type_name: ClassVar[str] = ""
    _template_section: ClassVar[str] = ""

    _type_metadata: ClassVar[Optional[Dict[str, JsonType]]] = None
    _metadata: Dict[str, JsonType] = dataclasses.field(default_factory=dict)

    @classmethod
    def _post_field_init(cls, field: _Tosca_Field) -> _Tosca_Field:
        # declare this so ToscaInput and ToscaOutput._post_field_init is not called on ToscaType subclasses
        return field

    def __post_init__(self):
        # internal bookkeeping
        self._instance_fields: Optional[Dict[str, Tuple[_Tosca_Field, Any]]] = None
        fields = object.__getattribute__(self, "__dataclass_fields__")
        for field in fields.values():
            val = object.__getattribute__(self, field.name)
            if val is REQUIRED:
                if self._enforce_required_fields():
                    # on Python < 3.10 we set this to workaround the lack of keyword only fields
                    raise ValueError(
                        f'Keyword argument was missing: {field.name} on "{self}".'
                    )
            elif getattr(field, "deferred_property_assignments", None):
                for name, value in field.deferred_property_assignments.items():
                    setattr(val, name, value)
            if isinstance(val, _ToscaType):
                val._set_parent(self, field.name)
            elif (
                isinstance(val, FieldProjection)
                and isinstance(self, _OwnedToscaType)
                and val.field.owner
                and issubclass(val.field.owner, Node)
            ):
                # if a relative field projection from a node template, assume its the parent
                setattr(self, field.name, val.set_start(".parent"))
        self._initialized = True

    def _enforce_required_fields(self) -> bool:
        return True

    # XXX version (type and template?)

    def register_template(self, current_module, name) -> None:
        self._all_templates.setdefault(self._template_section, {})[
            (current_module, self._name or name)
        ] = self

    def set_operation(
        self,
        op: _OperationFunc,  # Callable[Concatenate[Self, ...], Any],
        name: Optional[Union[str, _OperationFunc]] = None,
    ) -> None:
        """
        Assign the given :std:ref:`TOSCA operation<operation>` to this TOSCA object.
        TOSCA allows operations to be defined directly on templates.

        Args:
          op: A function implements the operation. It should looks like a method, i.e. accepts ``Self`` as the first argument.
              Using the `tosca.operation` function decorator is recommended but not required.
          name: The TOSCA operation name. If omitted, ``op``'s :py:func:`operation_name<tosca.operation>` or the op's function name is used.
        """
        # for type safety, ``name`` can be a ref to a method e.g my_node_type.configure
        if callable(name):
            name = cast(str, getattr(name, "operation_name", op.__name__))
        elif not name:
            name = cast(str, getattr(op, "operation_name", op.__name__))
        # we invoke methods through a proxy during yaml generation and at runtime so we don't need to worry
        # that this function will not receive self because are assigning it directly to the object here.
        setattr(self, name, op)

    def __set_name__(self, owner, name):
        # called when a template is declared as a default value or inside a Namespace (owner will be class)
        if not self._name:
            if issubclass(owner, Namespace):
                owner.set_name(self, name)
            else:
                self._name = name

    @classmethod
    def set_to_property_source(cls, requirement: Any, property: Any) -> None:
        """
        Sets the given requirement to the TOSCA template that provided the value of "property".

        For example, if ``A.property = B.property``
        then ``A.set_to_property_source("requirement", "property")``
        will create a node filter for ``A.requirement`` that selects ``B``.

        The requirement and property have to be defined on the same class.
        The method should be called from ``_class_init(cls)``.

        Args:
            requirement (FieldProjection or str): name of the requirement field
            property (FieldProjection or str): name of the property field

        Raises:
            TypeError: If ``requirement`` or ``property`` are missing from ``cls``.

        The requirement and property names can also be strings, e.g.:

        ``cls.set_to_property_source("requirement", "property")``

        Note that ``cls.set_to_property_source(cls.requirement, cls.property)``

        is equivalent to:

        ``cls.requirement = cls.property``  if called within ``_class_init(cls)``,

        but using this method will avoid static type checker complaints.
        """
        if isinstance(requirement, str):
            requirement = getattr(_DataclassTypeProxy(cls), requirement)
        if isinstance(requirement, FieldProjection):
            requirement = requirement.field
        if isinstance(requirement, _Tosca_Field):
            if requirement.owner != cls:
                raise ValueError(f"Field {requirement} isn't owned by {cls}")
            if isinstance(property, str):
                property = getattr(_DataclassTypeProxy(cls), property)
            if not isinstance(property, FieldProjection):
                raise TypeError(
                    f"{property} isn't a TOSCA field -- this method should be called from _class_init()"
                )
            return requirement.set_constraint(property)
        raise TypeError(
            f"{requirement} isn't a TOSCA field -- this method should be called from _class_init()"
        )

    if typing.TYPE_CHECKING:
        # trick the type checker to support both class and instance method calls
        @classmethod
        def find_configured_by(
            cls,
            prop_ref: _T,
        ) -> _T:
            return cast(_T, None)

    else:
        find_configured_by = anymethod(find_configured_by, keyword="cls_or_obj")

    if typing.TYPE_CHECKING:
        # trick the type checker to support both class and instance method calls
        @classmethod
        def find_hosted_on(
            cls,
            prop_ref: _T,
        ) -> _T:
            return cast(_T, None)

    else:
        find_hosted_on = anymethod(find_hosted_on, keyword="cls_or_obj")

    @classmethod
    def _get_parameter_and_explicit_fields(cls):
        for b in cls.__bases__:
            if issubclass(b, _ToscaType) and not issubclass(b, ToscaType):
                # include fields from directly inherited input and output classes
                # because they are not inherited at the tosca level
                for f in dataclasses.fields(b):  # type: ignore
                    if isinstance(f, _Tosca_Field):
                        yield b, f
        # include directly inherited parameters fields
        for f in cls.explicit_tosca_fields:
            yield cls, f

    def to_yaml(self, dict_cls=dict) -> Any:
        return self._name

    if typing.TYPE_CHECKING:

        @classmethod
        def get_field(cls, name) -> Optional[dataclasses.Field]:
            return None

    else:
        get_field = anymethod(_get_field)

    def get_instance_field(self, name) -> Optional[dataclasses.Field]:
        field = object.__getattribute__(self, "__dataclass_fields__").get(name)
        if field:
            return field
        field_and_value = self.get_instance_fields().get(name)
        if field_and_value:
            return field_and_value[0]
        return None

    def get_instance_fields(self) -> Dict[str, Tuple[_Tosca_Field, Any]]:
        if self._instance_fields is None:
            # only do this once and save any generated values
            self._instance_fields = dict(self._get_instance_fields())
        return self._instance_fields

    def _get_instance_fields(self) -> Iterator[Tuple[str, Tuple[_Tosca_Field, Any]]]:
        fields = object.__getattribute__(self, "__dataclass_fields__")
        __dict__ = object.__getattribute__(self, "__dict__")
        for name, value in __dict__.items():
            field = fields.get(name)
            if isinstance(field, _Tosca_Field):
                t_field = field
            else:
                t_field = None
            if (
                isinstance(value, FieldProjection)
                and value.field.owner == self.__class__
            ):
                yield name, (t_field or value.field, value)
            elif isinstance(value, _Tosca_Field):
                # field assigned directly to the object
                field = value
                if field.default is not dataclasses.MISSING:
                    value = field.default
                elif field.default_factory is not dataclasses.MISSING:
                    value = field.default_factory()
                else:
                    continue
                yield name, (field, value)
            # skip inference for methods and attributes starting with "_"
            elif not field and name[0] != "_" and is_data_field(value):
                # attribute is not part of class definition, try to deduce from the value's type
                field = _Tosca_Field.infer_field(self.__class__, name, value)
                field.default = MISSING  # this whole field was missing
                yield name, (field, value)
            elif t_field:
                yield name, (t_field, value)
            # otherwise skip the field

    def to_template_yaml(self, converter: "PythonToYaml") -> dict:
        # TOSCA templates can add requirements, capabilities and operations that are not defined on the type
        # so we need to look for _ToscaFields and operation function in the object's __dict__ and generate yaml for them too
        dict_cls = converter.yaml_cls
        body = dict_cls(type=self.tosca_type_name())
        if self._metadata:
            body["metadata"] = metadata_to_yaml(self._metadata)
        for field, value in self.get_instance_fields().values():
            if field.section == "requirements":
                if value and value is not CONSTRAINED:
                    # XXX handle case where value is a type not an instance
                    if not isinstance(value, (list, tuple)):
                        value = [value]
                    for i, item in enumerate(value):
                        req = dict_cls()
                        shorthand = converter.set_requirement_value(
                            req,
                            field,
                            item,
                            self._name + "_" + field.name + (str(i) if i else ""),
                        )
                        if shorthand or req:
                            body.setdefault("requirements", []).append({
                                field.tosca_name: shorthand or req
                            })
            elif field.section in ["capabilities", "artifacts"]:
                if value:
                    assert isinstance(value, (CapabilityEntity, ArtifactEntity))
                    if (
                        field.default_factory
                        and field.default_factory is not dataclasses.MISSING
                    ):
                        default_value = field.default_factory()
                    else:
                        default_value = field.default
                    if value._local_name:
                        compare = dataclasses.replace(
                            value,
                            _local_name=None,
                            _node=None,  # type: ignore
                        )
                    else:
                        compare = value
                    if compare != default_value:
                        tpl = value.to_template_yaml(converter)
                        body.setdefault(field.section, {})[field.tosca_name] = tpl
            elif field.section in ["properties", "attributes"]:
                if field.default == value:
                    # XXX datatype values don't compare properly, should have logic like CapabilityEntity above
                    continue
                if not isinstance(
                    value, EvalData
                ) and not field.get_type_info().instance_check(value):
                    raise TypeError(
                        f"{field.tosca_field_type.name} \"{field.name}\"'s value has wrong type: it's a {type(value)}, not a {field.type}."
                    )
                body.setdefault(field.section, {})[field.tosca_name] = to_tosca_value(
                    value, dict_cls
                )
            elif field.section:
                assert False, "unexpected section in {field}"

        # this only adds interfaces defined directly on this object
        interfaces = converter._interfaces_yaml(self, self.__class__)
        if interfaces:
            body["interfaces"] = interfaces

        return body

    def load_class(self, module_path: str, class_name: str):
        from unfurl.util import load_module

        current_mod = sys.modules[self.__class__.__module__]
        assert current_mod.__file__
        path = os.path.join(os.path.dirname(current_mod.__file__), module_path)
        loaded = load_module(path)
        return getattr(loaded, class_name)


class _TopologyParameter(ToscaType):
    _type_section: ClassVar[str] = "topology_template"

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        dict_cls = converter and converter.yaml_cls or yaml_cls
        body: Dict[str, Any] = dict_cls()
        for f_cls, field in cls._get_parameter_and_explicit_fields():
            assert field.name, field
            if f_cls._docstrings:
                field.description = f_cls._docstrings.get(field.name)
            item = field.to_yaml(converter)
            body.update(item)
        return {cls._type_name: body}


class TopologyInputs(_TopologyParameter):
    "Base class for defining topology template inputs."

    _type_name: ClassVar[str] = "inputs"


class TopologyOutputs(_TopologyParameter):
    "Base class for defining topology template outputs."

    _type_name: ClassVar[str] = "outputs"
    _default_key: ClassVar[str] = "value"


_TT = TypeVar("_TT", bound="Node")


def substitute_node(node_type: Type[_TT], _name: str = "", **kw) -> _TT:
    directives = kw.pop("_directives", [])
    if NodeTemplateDirective.substitute not in directives:
        directives.append(NodeTemplateDirective.substitute)
    return node_type(_name, _directives=directives, **kw)


def select_node(node_type: Type[_TT], _name: str = "", **kw) -> _TT:
    directives = kw.pop("_directives", [])
    if NodeTemplateDirective.select not in directives:
        directives.append(NodeTemplateDirective.select)
    return node_type(_name, _directives=directives, **kw)


# set requirement_name to the types the type checker will see,
# e.g. Foo.my_requirement: T
def find_required_by(
    requirement_name: Union[str, "CapabilityEntity", "Node", "Relationship"],
    expected_type: Union[Type[_TT], None] = None,
    cls_or_obj=None,
) -> _TT:
    """
    find_required_by(requirement_name: str | FieldProjection, expected_type: Type[Node] | None = None)

    Finds the node template with a requirement named ``requirement_name`` whose value is this template.

    For example:

    .. code-block:: python

        class A(Node):
          pass

        class B(Node):
          connects_to: A

        a = A()
        b = B(connects_to=a)

        >>> a.find_required_by(B.connects_to, B)
        b

    If no match is found, or more than one match is found, an error is raised.
    If 0 or more matches are expected, use `find_all_required_by`.

    If called during class definition this will return an eval expression.
    If called as a classmethod or as a free function it will evaluate in the current context.

    For example, to expand on the example above:

    .. code-block:: python

      class A(Node):
        parent: B = find_required_by(B.connects_to, B)

    ``parent`` will default to an eval expression.

    Args:
        requirement_name (str | FieldProjection): Either the name of the req, or for a more type safety, a reference to the requirement (e.g. ``B.connects_to`` in the example above).
        expected_type (Node, optional): The expected type of the node template will be returned. If provided, enables static typing and runtime validation of the return value.

    Returns:
        Node: The node template that is targeting this template via the requirement.
    """

    source_field, req_name = _get_field_from_prop_ref(requirement_name)
    if not source_field and expected_type:
        field = expected_type.get_field(req_name)
        if not field:
            raise TypeError(f"{expected_type} doesn't have a field named {req_name}")
        if isinstance(field, _Tosca_Field):
            source_field = field
        else:
            raise TypeError(f"Field {req_name} on {expected_type} is not a requirement")
        req_name = source_field.tosca_name
    cls = None
    if source_field:
        if source_field.tosca_field_type != ToscaFieldType.requirement:
            raise TypeError(f"Field {req_name} is not a requirement")
        if (
            expected_type
            and source_field.owner
            and not issubclass(source_field.owner, expected_type)
        ):
            raise TypeError(
                f"{expected_type} doesn't match the requirement's owner {source_field.owner}"
            )
        if cls_or_obj:
            if not isinstance(cls_or_obj, type):
                cls = cls_or_obj.__class__
            else:
                cls = cls_or_obj
            if not issubclass(cls, source_field.get_type_info().types):
                raise TypeError(
                    f"{req_name}'s type is incompatible with {cls} -- wrong requirement?"
                )
    prefix = _get_expr_prefix(cls_or_obj)
    # XXX elif Relationship
    expr = prefix + [".sources", req_name]
    if not expected_type:
        ref = EvalData(None, expr)
    else:
        dummy: _Tosca_Field = _Tosca_Field(
            ToscaFieldType.requirement, name="_required_by", owner=cls
        )
        dummy.type = expected_type
        ref = FieldProjection(dummy)
        ref._path = expr
    return cast(_TT, ref)


def _get_field_from_prop_ref(requirement_name) -> Tuple[Optional[_Tosca_Field], str]:
    if isinstance(requirement_name, FieldProjection):
        source_field = requirement_name.field
        req_name = requirement_name.field.tosca_name
    elif isinstance(requirement_name, str):
        req_name = requirement_name
        source_field = None
    elif isinstance(requirement_name, _Tosca_Field):
        req_name = requirement_name.tosca_name
        source_field = requirement_name
    else:
        raise TypeError(
            f"{property} isn't a TOSCA field -- this method should be called from _class_init()"
        )
    return source_field, req_name


def _get_expr_prefix(
    cls_or_obj: Union[None, ToscaType, Type[ToscaType]],
) -> List[Union[str, _GetName]]:
    if cls_or_obj:
        if cls_or_obj._name:
            return ["", _GetName(cls_or_obj)]
        elif isinstance(cls_or_obj, ToscaType):
            return ["", _GetName(cls_or_obj)]
    # XXX elif isinstance(cls_or_obj, type):  return f"*[.type={cls_or_obj._tosca_typename}]::"
    return []


def find_all_required_by(
    requirement_name: Union[str, "CapabilityEntity", "Node", "Relationship"],
    expected_type: Union[Type[_TT], None] = None,
    cls_or_obj=None,
) -> List[_TT]:
    """
    find_all_required_by(requirement_name: str | FieldProjection, expected_type: Type[Node] | None = None)

    Behaves the same as `find_required_by` but returns a list of all the matches found.
    If no match is found, return an empty list.

    Args:
        requirement_name (str | FieldProjection): Either the name of the req, or for a more type safety, a reference to the requirement (e.g. ``B.connects_to`` in the example above).
        expected_type (Node, optional): The expected type of the node template will be returned. If provided, enables static typing and runtime validation of the return value.

    Returns:
        List[tosca.Node]:
    """
    ref = cast(EvalData, find_required_by(requirement_name, expected_type, cls_or_obj))
    ref.set_foreach("$true")
    return cast(List[_TT], ref)


class Node(ToscaType):
    "A TOSCA node template."

    _type_section: ClassVar[str] = "node_types"
    _template_section: ClassVar[str] = "node_templates"

    _directives: List[str] = field(default_factory=list)
    "List of this node template's TOSCA directives"

    _node_filter: Optional[Dict[str, Any]] = None
    "Optional node_filter to use with 'select' directive"

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        yaml = converter._shared_cls_to_yaml(cls)
        return yaml

    def _enforce_required_fields(self):
        for directive in self._directives:
            for name in ("select", "substitute"):
                return False
        return True

    def to_template_yaml(self, converter: "PythonToYaml") -> dict:
        tpl = super().to_template_yaml(converter)
        if self._directives:
            tpl["directives"] = self._directives
        if self._node_filter:
            tpl["node_filter"] = to_tosca_value(self._node_filter)
        return tpl

    def find_artifact(self, name_or_tpl) -> Optional["ArtifactEntity"]:
        if isinstance(name_or_tpl, str):
            field = self.get_field_from_tosca_name(name_or_tpl, ToscaFieldType.artifact)
            if field:
                return getattr(self, field.name)
        return None  # XXX

    def substitute(self, _name: str = "", **overrides) -> Self:
        """
        Create a new node template with a "substitute" directive.

        For example:

        .. code-block:: python

          from tosca_repositories import nested

          substitution_node = nested.__root__.substitute(property="override", db=DB())
        """
        return substitute_node(type(self), _name=_name, **overrides)

    if typing.TYPE_CHECKING:
        # trick the type checker to support both class and instance method calls
        @classmethod
        def find_required_by(
            cls,
            source_attr: Union[str, "Node", FieldProjection, None],
            expected_type: Union[Type[_T], None] = None,
        ) -> _T:
            return cast(_T, None)

    else:
        find_required_by = anymethod(find_required_by, keyword="cls_or_obj")

    if typing.TYPE_CHECKING:
        # trick the type checker to support both class and instance method calls
        @classmethod
        def find_all_required_by(
            cls,
            source_attr: Union[str, "Node", FieldProjection, None],
            expected_type: Union[Type[_T], None] = None,
        ) -> List[_T]:
            return cast(List[_T], None)

    else:
        find_all_required_by = anymethod(find_all_required_by, keyword="cls_or_obj")


NodeType = Node


class _OwnedToscaType(ToscaType):
    _local_name: Optional[str] = field(default=None)
    _node: Optional[Node] = field(default=None)

    def _set_parent(self, parent: "_ToscaType", name: str):
        # only set once
        if not self._local_name and isinstance(parent, Node):
            self._node = parent
            self._local_name = name

    def get_embedded_name(self) -> str:
        if self._node:
            return f"{self._node._name}::{self._local_name}"
        return self._name


class _BaseDataType(ToscaObject):
    @classmethod
    def _get_property_metadata(cls) -> Optional[Dict[str, Any]]:
        return None

    @classmethod
    def get_tosca_datatype(cls):
        from .python2yaml import PythonToYaml
        custom_defs = cls._cls_to_yaml(PythonToYaml({}))
        return ToscaParserDataType(cls.tosca_type_name(), custom_defs)

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        return {}


class ValueType(_BaseDataType):
    "ValueTypes are user-defined TOSCA data types that are derived from simple TOSCA datatypes, as opposed to complex TOSCA data types."

    # we need this because this class isn't derived from ToscaType:
    _template_section: ClassVar[str] = "data_types"
    _type_section: ClassVar[str] = "data_types"
    _constraints: ClassVar[Optional[List[dict]]] = None

    @classmethod
    def simple_type(cls) -> type:
        "The Python type that this data types is derived from."
        for c in cls.__mro__:
            if c.__name__ in PYTHON_TO_TOSCA_TYPES:
                return c
        raise TypeError("ValueType must be derived from a simple type.")

    @classmethod
    def simple_tosca_type(cls) -> str:
        "The TOSCA simple type that this data types is derived from."
        return PYTHON_TO_TOSCA_TYPES[cls.simple_type().__name__]

    def to_yaml(self, dict_cls=dict):
        # find the simple type this is derived from and convert value to that type
        return self.simple_type()(self)

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        dict_cls = converter and converter.yaml_cls or yaml_cls
        body: Dict[str, Any] = dict_cls()
        body[cls.tosca_type_name()] = dict_cls()
        doc = cls.__doc__ and cls.__doc__.strip()
        if doc:
            body[cls.tosca_type_name()]["description"] = doc
        body[cls.tosca_type_name()]["type"] = cls.simple_tosca_type()
        if cls._constraints:
            body[cls.tosca_type_name()]["constraints"] = to_tosca_value(
                cls._constraints
            )
        return body


class DataEntity(_BaseDataType, _OwnedToscaType):
    _type_section: ClassVar[str] = "data_types"

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        yaml = converter._shared_cls_to_yaml(cls)
        return yaml

    def to_yaml(self, dict_cls=dict):
        body = dict_cls()
        for field, value in self.get_instance_fields().values():
            body[field.tosca_name] = to_tosca_value(value, dict_cls)
        return body


DataType = DataEntity  # deprecated


class OpenDataEntity(DataEntity):
    "Properties don't need to be declared with TOSCA data types derived from this class."

    _type_metadata = dict(additionalProperties=True)

    def __init__(self, _name="", **kw):
        for k in list(kw):
            if k[0] != "_":
                self.__dict__[k] = kw.pop(k)
        super().__init__(_name, **kw)

    def extend(self, **kw) -> Self:
        "Add undeclared properties to the data type."
        self.__dict__.update(kw)
        return self


OpenDataType = OpenDataEntity  # deprecated


class CapabilityEntity(_OwnedToscaType):
    _type_section: ClassVar[str] = "capability_types"

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        return converter._shared_cls_to_yaml(cls)

    def to_template_yaml(self, converter: "PythonToYaml") -> dict:
        tpl = super().to_template_yaml(converter)
        del tpl["type"]
        return tpl

    def get_embedded_name(self) -> str:
        if self._node:
            return f"{self._node._name}::.capabilities[.name={self._local_name}]"
        return self._name


CapabilityType = CapabilityEntity


class Relationship(_OwnedToscaType):
    # the "owner" of the relationship is its source node
    _type_section: ClassVar[str] = "relationship_types"
    _template_section: ClassVar[str] = "relationship_templates"
    _valid_target_types: ClassVar[Optional[List[Type[CapabilityEntity]]]] = None
    _default_for: Optional[str] = field(default=None)
    _target: Node = field(default=None, builtin=True)

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        yaml = converter._shared_cls_to_yaml(cls)
        # only use _valid_target_types if declared directly
        _valid_target_types = cls.__dict__.get("_valid_target_types")
        if _valid_target_types:
            # a derived class declared concrete types
            target_types = [t.tosca_type_name() for t in _valid_target_types]
            yaml[cls.tosca_type_name()]["valid_target_types"] = target_types
        return yaml

    def __set_name__(self, owner, name):
        pass  # override super implementation -- we don't want to set the name, an empty name indicates template is inline

    def to_template_yaml(self, converter: "PythonToYaml") -> dict:
        tpl = super().to_template_yaml(converter)
        if self._default_for:
            tpl["default_for"] = self._default_for
        return tpl

    def __getitem__(self, target: Node) -> Self:
        if self._target:
            return dataclasses.replace(self, _target=target)  # type: ignore
        self._target = target
        return self

    def get_embedded_name(self) -> str:
        if self._node:
            return f"{self._node._name}::.requirements[.name={self._local_name}]"
        return self._name


RelationshipType = Relationship  # deprecated


class Interface(ToscaObject):
    # "Note: Interface types are not derived from ToscaType"
    _type_name: ClassVar[str] = ""
    _type_section: ClassVar[str] = "interface_types"
    _template_section: ClassVar[str] = "interface_types"
    _type_metadata: ClassVar[Optional[Dict[str, JsonType]]] = None

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        body: Dict[str, Any] = converter.yaml_cls()
        tosca_type_name = cls.tosca_type_name()
        doc = cls.__doc__ and cls.__doc__.strip()
        if doc:
            body["description"] = doc
        if cls._type_metadata:
            body["metadata"] = metadata_to_yaml(cls._type_metadata)

        for name, obj in cls.__dict__.items():
            # add empty operations
            if name[0] != "_" and converter.is_operation(obj):
                doc = obj.__doc__ and obj.__doc__.strip()
                if doc:
                    op = converter.yaml_cls(description=doc)
                else:
                    op = None
                op_name = getattr(obj, "operation_name", name)
                body.setdefault("operations", converter.yaml_cls())[op_name] = op
            elif isinstance(obj, _DataclassType) and issubclass(obj, ToscaInputs):
                body["inputs"] = obj._cls_to_yaml(converter)
        # _interfaces_yaml returns {short name: body} for the interface:
        implementation_yaml = converter._interfaces_yaml(None, cls)
        if not implementation_yaml:
            if not body:
                return implementation_yaml  # return empty dict to skip
            return {tosca_type_name: body}
        else:
            implementation_body = next(iter(implementation_yaml.values()))
            implementation_body.pop("type", None)
            converter.set_bases(cls, body)
            body.update(implementation_body)
            return {tosca_type_name: body}


InterfaceType = Interface  # deprecated


class ArtifactEntity(_OwnedToscaType):
    _type_section: ClassVar[str] = "artifact_types"
    _mime_type: ClassVar[Optional[str]] = None
    _file_ext: ClassVar[Optional[List[str]]] = None
    _builtin_fields: ClassVar[Sequence[str]] = (
        "file",
        "repository",
        "deploy_path",
        "version",
        "checksum",
        "checksum_algorithm",
        "permissions",
        "intent",
        "target",
        "order",
        "contents",
        "dependencies",
    )
    file: str = field()
    repository: Optional[str] = field(default=None)
    deploy_path: Optional[str] = field(default=None)
    version: Optional[str] = field(default=None)
    checksum: Optional[str] = field(default=None)
    checksum_algorithm: Optional[str] = field(default=None)
    permissions: Optional[str] = field(default=None)
    intent: Optional[str] = field(default=None)
    target: Optional[str] = field(default=None)
    order: Optional[int] = field(default=None)
    contents: Optional[str] = field(default=None)
    dependencies: Optional[List[Union[str, Dict[str, str]]]] = field(default=None)

    def execute(self, *args, **kwargs) -> Optional["ToscaOutputs"]:
        self.set_inputs(*args)
        return None

    def set_inputs(self, *args: "ToscaInputs", **kw):
        self._inputs = ToscaInputs._get_inputs(*args, **kw)

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        yaml = converter._shared_cls_to_yaml(cls)
        if cls._mime_type:
            yaml[cls.tosca_type_name()]["mime_type"] = cls._mime_type
        if cls._file_ext:
            yaml[cls.tosca_type_name()]["file_ext"] = cls._file_ext
        return yaml

    def to_template_yaml(self, converter: "PythonToYaml") -> dict:
        tpl = super().to_template_yaml(converter)
        for field in self._builtin_fields:
            val = getattr(self, field, None)
            if val is not None:
                tpl[field] = to_tosca_value(val)
        return tpl

    def to_yaml(self, dict_cls=dict) -> Optional[Dict]:
        return dict_cls(get_artifact=["SELF", self._name or self._local_name])

    def get_embedded_name(self) -> str:
        if self._node:
            return f"{self._node._name}::.artifacts::{self._local_name}"
        return self._name


ArtifactType = ArtifactEntity  # deprecated


class Policy(ToscaType):
    _type_section: ClassVar[str] = "policy_types"
    _template_section: ClassVar[str] = "policies"

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        return converter._shared_cls_to_yaml(cls)


PolicyType = Policy  # deprecated


class Group(ToscaType):
    _type_section: ClassVar[str] = "group_types"
    _template_section: ClassVar[str] = "groups"

    @classmethod
    def _cls_to_yaml(cls, converter: "PythonToYaml") -> dict:
        return converter._shared_cls_to_yaml(cls)


GroupType = Group  # deprecated
