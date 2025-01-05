# Copyright (c) 2023 Adam Souzis
# SPDX-License-Identifier: MIT
import os.path
import sys
from typing import TYPE_CHECKING, Any

try:
    import toscaparser
except ImportError:
    vendor_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "vendor")
    sys.path.insert(0, vendor_dir)
    import toscaparser
from ._tosca import *
from ._fields import *

InputOption = PropertyOptions({ToscaInputs._metadata_key: True})
OutputOption = AttributeOptions({ToscaOutputs._metadata_key: True})

from .builtin_types import nodes
from .builtin_types import interfaces
from .builtin_types import relationships
from .builtin_types import capabilities
from .builtin_types import datatypes
from .builtin_types import artifacts
from .builtin_types import policies
from .builtin_types import groups

class WritePolicy(Enum):
    older = "older"
    never = "never"
    always = "always"
    auto = "auto"

    def deny_message(self, unchanged=False) -> str:
        if unchanged:
            return (
                f'overwrite policy is "{self.name}" but the contents have not changed'
            )
        if self == WritePolicy.auto:
            return 'overwrite policy is "auto" and the file was last modified by another process'
        if self == WritePolicy.never:
            return 'overwrite policy is "never" and the file already exists'
        elif self == WritePolicy.older:
            return (
                'overwrite policy is "older" and the file is newer than the source file'
            )
        else:
            return ""

    def generate_comment(self, processor: str, path: str) -> str:
        ts_stamp = datetime.datetime.now().isoformat("T", "seconds")
        return f'# Generated by {processor} from {os.path.relpath(path)} at {ts_stamp} overwrite not modified (change to "overwrite ok" to allow)\n'

    def can_overwrite(self, input_path: str, output_path: str) -> bool:
        return self.can_overwrite_compare(input_path, output_path)[0]

    def can_overwrite_compare(
        self, input_path: str, output_path: str, new_src: Optional[str] = None
    ) -> Tuple[bool, bool]:
        if self == WritePolicy.always:
            if new_src and os.path.exists(output_path):
                with open(output_path) as out:
                    contents = out.read()
                return True, self.has_contents_unchanged(new_src, contents)
            return True, False
        if self == WritePolicy.never:
            return not os.path.exists(output_path), False
        elif self == WritePolicy.older:
            # only overwrite if the output file is older than the input file
            return not is_newer_than(output_path, input_path), False
        else:  # auto
            # if this file is autogenerated, parse out the modified time and make sure it matches
            if not os.path.exists(output_path):
                return True, False
            with open(output_path) as out:
                contents = out.read()
                match = re.search(
                    r"# Generated by .+? at (\S+) overwrite (ok)?", contents
                )
                if not match:
                    return False, False
                if match.group(2):  # found "ok"
                    return True, self.has_contents_unchanged(new_src, contents)
                time = datetime.datetime.fromisoformat(match.group(1)).timestamp()
            if abs(time - os.stat(output_path).st_mtime) < 5:
                return True, self.has_contents_unchanged(new_src, contents)
            return False, False

    def has_contents_unchanged(self, new_src: Optional[str], old_src: str) -> bool:
        if new_src is None:
            return False
        new_lines = [
            l.strip()
            for l in new_src.splitlines()
            if l.strip() and not l.startswith("#")
        ]
        old_lines = [
            l.strip()
            for l in old_src.splitlines()
            if l.strip() and not l.startswith("#")
        ]
        if len(new_lines) == len(old_lines):
            return new_lines == old_lines
        return False


def is_newer_than(output_path, input_path):
    "Is output_path newer than input_path?"
    if not os.path.exists(input_path) or not os.path.exists(output_path):
        return True  # assume that if it doesn't exist yet its definitely newer
    if os.stat(output_path).st_mtime_ns > os.stat(input_path).st_mtime_ns:
        return True
    return False

__all__ = [
    "EvalData",
    "safe_mode",
    "global_state_mode",
    "global_state_context",
    "nodes",
    "capabilities",
    "relationships",
    "interfaces",
    "datatypes",
    "artifacts",
    "policies",
    "groups",
    "Artifact",
    "ArtifactEntity",
    "ArtifactType",
    "Attribute",
    "B",
    "BPS",
    "Bitrate",
    "Capability",
    "CapabilityEntity",
    "CapabilityType",
    "D",
    "DataEntity",
    "DataType",
    "DeploymentBlueprint",
    "Eval",
    "Frequency",
    "GB",
    "GBPS",
    "GHZ",
    "GHz",
    "GIB",
    "GIBPS",
    "Gbps",
    "GiB",
    "Gibps",
    "Group",
    "GroupType",
    "H",
    "HZ",
    "Hz",
    "InputOption",
    "Interface",
    "InterfaceType",
    "JsonType",
    "JsonObject",
    "KB",
    "KBPS",
    "KHZ",
    "KIB",
    "KIBPS",
    "Kbps",
    "KiB",
    "Kibps",
    "M",
    "MB",
    "MBPS",
    "MHZ",
    "MHz",
    "MIB",
    "MIBPS",
    "MS",
    "Mbps",
    "MiB",
    "Mibps",
    "NS",
    "AttributeOptions",
    "PropertyOptions",
    "Namespace",
    "NodeTemplateDirective",
    "Node",
    "NodeType",
    "OpenDataEntity",
    "Options",
    "OutputOption",
    "Policy",
    "PolicyType",
    "Property",
    "REQUIRED",
    "MISSING",
    "DEFAULT",
    "CONSTRAINED",
    "Relationship",
    "RelationshipType",
    "Requirement",
    "S",
    "select_node",
    "ServiceTemplate",
    "Size",
    "substitute_node",
    "T",
    "TB",
    "TBPS",
    "TIB",
    "TIBPS",
    "Tbps",
    "TiB",
    "Tibps",
    "Time",
    "TopologyInputs",
    "TopologyOutputs",
    "ToscaParserDataType",
    "ToscaType",
    "ToscaInputs",
    "ToscaOutputs",
    "US",
    "ValueType",
    "b",
    "bps",
    "Computed",
    "d",
    "equal",
    "field",
    "find_all_required_by",
    "find_configured_by",
    "find_hosted_on",
    "find_required_by",
    "find_relationship",
    "find_node",
    "gb",
    "gbps",
    "ghz",
    "gib",
    "gibps",
    "greater_or_equal",
    "greater_than",
    "h",
    "hz",
    "in_range",
    "kB",
    "kHz",
    "kb",
    "kbps",
    "khz",
    "kib",
    "kibps",
    "length",
    "less_or_equal",
    "less_than",
    "m",
    "max_length",
    "mb",
    "mbps",
    "metadata_to_yaml",
    "mhz",
    "mib",
    "mibps",
    "min_length",
    "ms",
    "ns",
    "operation",
    "pattern",
    "s",
    "scalar",
    "scalar_value",
    "tb",
    "tbps",
    "tib",
    "tibps",
    "tosca_timestamp",
    "tosca_version",
    "us",
    "unit",
    "valid_values",
]

__safe__ = __all__
