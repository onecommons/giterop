# Generated by tosca.yaml2python from unfurl/configurators/templates/dns.yaml at 2024-02-18T10:26:42 overwrite not modified (change to "overwrite ok" to allow)

import unfurl
from typing import List, Dict, Any, Tuple, Union, Sequence
from typing_extensions import Annotated
from tosca import (
    Artifact,
    Attribute,
    AttributeOptions,
    CONSTRAINED,
    Capability,
    CapabilityType,
    Computed,
    DEFAULT,
    DataType,
    Eval,
    MISSING,
    NodeType,
    Property,
    PropertyOptions,
    RelationshipType,
    Requirement,
    ToscaInputs,
    ToscaOutputs,
    operation,
)
import tosca
import unfurl.configurators.dns


class unfurl_datatypes_DNSRecord(DataType):
    _type_name = "unfurl.datatypes.DNSRecord"
    _type_metadata = {"additionalProperties": True}
    type: str
    value: Union[str, None] = None
    values: Union[List[Any], None] = None


class unfurl_capabilities_DNSZone(tosca.capabilities.Root):
    _type_name = "unfurl.capabilities.DNSZone"


class unfurl_relationships_DNSRecords(tosca.relationships.Root):
    _type_name = "unfurl.relationships.DNSRecords"
    records: Dict[str, "unfurl_datatypes_DNSRecord"]

    _valid_target_types = [unfurl_capabilities_DNSZone]


class unfurl_nodes_DNSZone(tosca.nodes.Root):
    _type_name = "unfurl.nodes.DNSZone"
    _type_metadata = {"title": "DNS Zone"}
    name: str = Property(title="Domain Name")
    """Top level part of the DNS name (e.g. example.com)"""

    provider: Dict[str, Any] = Property(metadata={"sensitive": True})
    """OctoDNS provider configuration"""

    records: Dict[str, "unfurl_datatypes_DNSRecord"] = Property(
        metadata={"computed": True}, factory=lambda: ({})
    )
    """DNS records to add to the zone"""

    exclusive: bool = False
    """Zone exclusively managed by this instance (removes unrecognized records)"""

    default_ttl: int = Property(title="Default TTL", default=300)
    testing: bool = Property(title="Testing", default=False)
    """Is this DNS zone being used for testing? (If set, Let's Encrypt staging will be used.)"""

    zone: Dict[str, Any] = Attribute(metadata={"internal": True})
    """The records found in the zone"""

    managed_records: Union[Dict[str, Any], None] = Attribute(
        title="Managed Records", default=None
    )
    """The records in the zone that are managed by this instance"""

    resolve: "unfurl_capabilities_DNSZone" = Capability(
        factory=unfurl_capabilities_DNSZone
    )

    parent_zone: Union["unfurl_capabilities_DNSZone", None] = Requirement(
        default=None, metadata={"visibility": "hidden"}
    )

    @operation(
        apply_to=[
            "Install.check",
            "Install.connect",
            "Standard.delete",
            "Standard.configure",
            "Mock.configure",
        ]
    )
    def default(self, **kw: Any) -> Any:
        return unfurl.configurators.dns.DNSConfigurator()


__all__ = [
    "unfurl_datatypes_DNSRecord",
    "unfurl_capabilities_DNSZone",
    "unfurl_relationships_DNSRecords",
    "unfurl_nodes_DNSZone",
]

