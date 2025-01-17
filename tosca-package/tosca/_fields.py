from typing import (
    Any,
    Dict,
    Sequence,
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
    Callable,
    Literal,
)
from ._tosca import (
    DataConstraint,
    JsonType,
    ToscaFieldType,
    _Tosca_Field,
    MISSING,
    EvalData,
    Node,
    Relationship,
    CapabilityEntity,
)


class Options:
    """
    A utility class to enable structured and validated metadata on TOSCA fields.
    Options are passed to the field specifier functions and merged with unstructured metadata.
    The user can use the | operator to merge Options together.
    """

    def __init__(self, data: Dict[str, JsonType]):
        """
        Args:
            data (Dict[str, JsonType]): Metadata to be add to the field specifier.
        """
        self.data = data
        self.next: Optional[Options] = None

    def validate(self, field: "_Tosca_Field") -> Tuple[bool, str]:
        """
        This is called when initializing the field these options were passed to.
        The field's metadata will have already been set, including the data in this Options instance.

        Args:
            field (_Tosca_Field): The field that these Options has been assigned to.

        Returns:
            Tuple[bool, str]: Whether validation succeeded and an optional error message if it didn't.
        """
        return True, ""

    def set_options(self, field: "_Tosca_Field"):
        metadata = field.metadata.copy()
        option: Optional[Options] = self
        while option:
            metadata.update(option.data)
            option = option.next
        field.metadata = types.MappingProxyType(metadata)

        option = self
        while option:
            valid, msg = option.validate(field)
            if not valid:
                raise ValueError(
                    f'Invalid option for field "{field.name}": {option.data}. {msg}'
                )
            option = option.next

    def __or__(self, __value: Union["Options", dict]) -> "Options":
        if isinstance(__value, dict):
            __value = Options(__value)
        elif not isinstance(__value, Options):
            raise TypeError(f"Options | {type(__value)} not supported.")
        self.next = __value
        return self

    def __ror__(self, __value: Union["Options", dict]) -> "Options":
        if isinstance(__value, dict):
            __value = Options(__value)
        elif not isinstance(__value, Options):
            raise TypeError(f"Options | {type(__value)} not supported.")
        self.next = __value
        return self


class PropertyOptions(Options):
    def validate(self, field: "_Tosca_Field") -> Tuple[bool, str]:
        return (
            field.tosca_field_type == ToscaFieldType.property,
            "This option only works with properties.",
        )


class AttributeOptions(Options):
    def validate(self, field: "_Tosca_Field") -> Tuple[bool, str]:
        return (
            field.tosca_field_type == ToscaFieldType.attribute,
            "This option only works with attributes.",
        )


def _make_field_doc(func, status=False, extra: Sequence[str] = ()) -> None:
    name = func.__name__.lower()
    doc = f"""Field specifier for declaring a TOSCA {name}.

    Args:
        default (Any, optional): Default value. Set to None if the {name} isn't required. Defaults to MISSING.
        factory (Callable, optional): Factory function to initialize the {name} with a unique value per template. Defaults to MISSING.
        name (str, optional): TOSCA name of the field, overrides the {name}'s name when generating YAML. Defaults to "".
        metadata (Dict[str, JSON], optional): Dictionary of metadata to associate with the {name}.
        options (Options, optional): Additional typed metadata to merge into metadata.\n"""
    indent = "        "
    if status:
        doc += f"{indent}constraints (List[`DataConstraint`], optional): List of TOSCA property constraints to apply to the {name}.\n"
        doc += f"{indent}title (str, optional): Human-friendly alternative name of the {name}.\n"
        doc += f"{indent}status (str, optional): TOSCA status of the {name}.\n"
    for arg in extra:
        doc += f"{indent}{arg}\n"
    func.__doc__ = doc


# cf @overloads here: https://github.com/python/typeshed/blob/main/stdlib/dataclasses.pyi#L159

_T = TypeVar("_T")


@overload
def Attribute(
    *,
    default: _T,
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    # attributes are excluded from __init__,
    # this tricks the static checker, see pep 681:
    init: Literal[False] = False,
) -> _T: ...


@overload
def Attribute(
    *,
    factory: Callable[[], _T],
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    # attributes are excluded from __init__,
    # this tricks the static checker, see pep 681:
    init: Literal[False] = False,
) -> _T: ...


@overload
def Attribute(
    *,
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    # attributes are excluded from __init__,
    # this tricks the static checker, see pep 681:
    init: Literal[False] = False,
) -> Any: ...


def Attribute(
    *,
    default=None,
    factory=MISSING,
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    # attributes are excluded from __init__,
    # this tricks the static checker, see pep 681:
    init: Literal[False] = False,
) -> Any:
    return _Tosca_Field(
        ToscaFieldType.attribute,
        default,
        factory,
        name,
        metadata,
        title,
        status,
        constraints=constraints,
        options=options,
    )


_make_field_doc(Attribute, True)


@overload
def Property(
    *,
    default: _T,
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    attribute: bool = False,
) -> _T: ...


@overload
def Property(
    *,
    factory: Callable[[], _T],
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    attribute: bool = False,
) -> _T: ...


@overload
def Property(
    *,
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    attribute: bool = False,
) -> Any: ...


def Property(
    *,
    default=MISSING,
    factory=MISSING,
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title: str = "",
    status: str = "",
    options: Optional[Options] = None,
    attribute: bool = False,
) -> Any:
    return _Tosca_Field(
        ToscaFieldType.property,
        default=default,
        default_factory=factory,
        name=name,
        metadata=metadata,
        title=title,
        status=status,
        constraints=constraints,
        options=options,
        declare_attribute=attribute,
    )


_make_field_doc(
    Property,
    True,
    [
        "attribute (bool, optional): Indicate that the property is also a TOSCA attribute. Defaults to False."
    ],
)

RT = TypeVar("RT")


def Computed(
    name="",
    *,
    factory: Callable[..., RT],
    metadata: Optional[Dict[str, JsonType]] = None,
    title: str = "",
    status: str = "",
    options: Optional["Options"] = None,
    attribute: bool = False,
) -> RT:
    """Field specifier for declaring a TOSCA property whose value is computed by the factory function at runtime.

    Args:
        factory (function): function called at runtime every time the property is evaluated.
        name (str, optional): TOSCA name of the field, overrides the Python name when generating YAML.
        metadata (Dict[str, JSON], optional): Dictionary of metadata to associate with the property.
        title (str, optional): Human-friendly alternative name of the property.
        status (str, optional): TOSCA status of the property.
        options (Options, optional): Typed metadata to apply.
        attribute (bool, optional): Indicate that the property is also a TOSCA attribute.

    Return type:
        The return type of the factory function (should be compatible with the field type).
    """
    default = EvalData({
        "eval": dict(computed=f"{factory.__module__}:{factory.__qualname__}")
    })
    # casting this to the factory function's return type enables the type checker to check that the return type matches the field's type
    return cast(
        RT,
        _Tosca_Field(
            ToscaFieldType.property,
            default=default,
            name=name,
            metadata=metadata,
            title=title,
            status=status,
            options=options,
            declare_attribute=attribute,
        ),
    )


@overload
def Requirement(
    *,
    default: _T,
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
    relationship: Union[str, Type["Relationship"], None] = None,
    capability: Union[str, Type["CapabilityEntity"], None] = None,
    node: Union[str, Type["Node"], None] = None,
    node_filter: Optional[Dict[str, Any]] = None,
) -> _T: ...


@overload
def Requirement(
    *,
    factory: Callable[[], _T],
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
    relationship: Union[str, Type["Relationship"], None] = None,
    capability: Union[str, Type["CapabilityEntity"], None] = None,
    node: Union[str, Type["Node"], None] = None,
    node_filter: Optional[Dict[str, Any]] = None,
) -> _T: ...


@overload
def Requirement(
    *,
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
    relationship: Union[str, Type["Relationship"], None] = None,
    capability: Union[str, Type["CapabilityEntity"], None] = None,
    node: Union[str, Type["Node"], None] = None,
    node_filter: Optional[Dict[str, Any]] = None,
) -> Any: ...


def Requirement(
    *,
    default=MISSING,
    factory=MISSING,
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
    relationship: Union[str, Type["Relationship"], None] = None,
    capability: Union[str, Type["CapabilityEntity"], None] = None,
    node: Union[str, Type["Node"], None] = None,
    node_filter: Optional[Dict[str, Any]] = None,
) -> Any:
    field: Any = _Tosca_Field(
        ToscaFieldType.requirement,
        default,
        factory,
        name,
        metadata,
        options=options,
    )
    field.relationship = relationship
    field.capability = capability
    field.node = node
    field.node_filter = node_filter
    return field


_make_field_doc(
    Requirement,
    False,
    [
        "relationship (str | Type[Relationship], optional): The requirement's ``relationship`` specified by TOSCA type name or Relationship class.",
        "capability (str | Type[CapabilityEntity], optional): The requirement's ``capability`` specified by TOSCA type name or CapabilityEntity class.",
        "node (str, | Type[Node], optional): The requirement's ``node`` specified by TOSCA type name or Node class.",
        "node_filter (Dict[str, Any], optional): The TOSCA node_filter for this requirement.",
    ],
)


@overload
def Capability(
    *,
    default: _T,
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
    valid_source_types: Optional[List[str]] = None,
) -> _T: ...


@overload
def Capability(
    *,
    factory: Callable[[], _T],
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
    valid_source_types: Optional[List[str]] = None,
) -> _T: ...


@overload
def Capability(
    *,
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
    valid_source_types: Optional[List[str]] = None,
) -> Any: ...


def Capability(
    *,
    default=MISSING,
    factory=MISSING,
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
    valid_source_types: Optional[List[str]] = None,
) -> Any:
    field: Any = _Tosca_Field(
        ToscaFieldType.capability,
        default,
        factory,
        name,
        metadata,
        options=options,
    )
    field.valid_source_types = valid_source_types or []
    return field


_make_field_doc(
    Capability,
    False,
    [
        "valid_source_types (List[str], optional): List of TOSCA type names to set as the capability's valid_source_types"
    ],
)


@overload
def Artifact(
    *,
    default: _T,
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
) -> _T: ...


@overload
def Artifact(
    *,
    factory: Callable[[], _T],
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
) -> _T: ...


@overload
def Artifact(
    *,
    name: str = "",
    metadata: Optional[Dict[str, JsonType]] = None,
    options: Optional["Options"] = None,
) -> Any: ...


def Artifact(
    *,
    default=MISSING,
    factory=MISSING,
    name="",
    metadata=None,
    options: Optional["Options"] = None,
) -> Any:
    return _Tosca_Field(
        ToscaFieldType.artifact, default, factory, name, metadata, options=options
    )


_make_field_doc(Artifact)


@overload
def Output(
    *,
    default: _T,
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    mapping: Union[str, List[str]] = "",
) -> _T: ...


@overload
def Output(
    *,
    factory: Callable[[], _T],
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    mapping: Union[str, List[str]] = "",
) -> _T: ...


@overload
def Output(
    *,
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    mapping: Union[str, List[str]] = "",
) -> Any: ...


def Output(
    *,
    default=None,
    factory=MISSING,
    name: str = "",
    constraints: Optional[List[DataConstraint]] = None,
    metadata: Optional[Dict[str, JsonType]] = None,
    title="",
    status="",
    options: Optional[Options] = None,
    mapping: Union[str, List[str]] = "",
) -> Any:
    return _Tosca_Field(
        ToscaFieldType.attribute,
        default,
        factory,
        name,
        metadata,
        title,
        status,
        constraints=constraints,
        options=options,
    )