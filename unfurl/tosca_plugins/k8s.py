# Generated by tosca.yaml2python from unfurl/tosca_plugins/k8s.yaml at 2025-01-16T07:31:25 overwrite not modified (change to "overwrite ok" to allow)

import unfurl
from typing import List, Dict, Any, Tuple, Union, Sequence
import tosca
from tosca import (
    Attribute,
    Capability,
    CapabilityEntity,
    Eval,
    Node,
    Property,
    Relationship,
    pattern,
)
import typing_extensions
import unfurl.configurators.k8s
from unfurl.tosca_plugins.artifacts import *


class unfurl_capabilities_Endpoint_K8sCluster(tosca.capabilities.EndpointAdmin):
    """
    Capability to connect to a K8sCluster.
    """

    _type_name = "unfurl.capabilities.Endpoint.K8sCluster"
    protocol: str = "https"


class unfurl_relationships_ConnectsTo_K8sCluster(tosca.relationships.ConnectsTo):
    _type_name = "unfurl.relationships.ConnectsTo.K8sCluster"
    name: Union[str, None] = Property(
        title="Cluster name",
        metadata={"env_vars": ["KUBE_CTX_CLUSTER"]},
        default=Eval({"get_env": "KUBE_CTX_CLUSTER"}),
    )
    KUBECONFIG: Union[str, None] = Property(
        metadata={"user_settable": False}, default=Eval({"get_env": "KUBECONFIG"})
    )
    """
    Path to an existing Kubernetes config file. If not provided, and no other connection options are provided, and the KUBECONFIG environment variable is not set, the default location will be used (~/.kube/config.json).
    """

    context: Union[str, None] = Property(
        metadata={"env_vars": ["KUBE_CTX"]}, default=Eval({"get_env": "KUBE_CTX"})
    )
    """
    The name of a context found in the config file. If not set the current-context will be used.
    """

    cluster_ca_certificate: Union[str, None] = Property(
        title="CA certificate",
        metadata={
            "sensitive": True,
            "user_settable": True,
            "input_type": "textarea",
            "env_vars": ["KUBE_CLUSTER_CA_CERT_DATA"],
        },
        default=Eval({"get_env": "KUBE_CLUSTER_CA_CERT_DATA"}),
    )
    """
    PEM-encoded root certificates bundle for TLS authentication
    """

    cluster_ca_certificate_file: Union[str, None] = Eval(
        {
            "eval": {
                "if": ".::cluster_ca_certificate",
                "then": {
                    "eval": {
                        "tempfile": {"eval": ".::cluster_ca_certificate"},
                        "suffix": ".crt",
                    }
                },
                "else": None,
            }
        }
    )
    insecure: Union[bool, None] = Property(
        metadata={"user_settable": True, "env_vars": ["KUBE_INSECURE"]},
        default=Eval({"get_env": "KUBE_INSECURE"}),
    )
    """
    If true, the server's certificate will not be checked for validity. This will make your HTTPS connections insecure
    """

    token: Union[str, None] = Property(
        title="Authentication Token",
        metadata={"sensitive": True, "user_settable": True, "env_vars": ["KUBE_TOKEN"]},
        default=Eval({"get_env": "KUBE_TOKEN"}),
    )
    """Token of your service account."""

    credential: Union["tosca.datatypes.Credential", None] = Property(
        metadata={"sensitive": True, "user_settable": False}, default=None
    )
    """
    token_type is either "api_key" or "password" (default is "password") Its "keys" map can have the following values: "cert_file": Path to a cert file for the certificate authority "ca_cert": Path to a client certificate file for TLS "key_file": Path to a client key file for TLS
    """

    namespace: Union[str, None] = None
    """The default namespace scope to use"""

    api_server: Union[str, None] = Property(
        title="Kubernetes Cluster API Base URL",
        metadata={"user_settable": True, "env_vars": ["KUBE_HOST"]},
        default=Eval({"eval": ".target::api_server"}),
    )
    """The address and port of the Kubernetes API server"""

    protocol: str = "https"
    as_: Union[str, None] = Property(
        name="as", metadata={"user_settable": False}, default=None
    )
    """Username to impersonate for the operation"""

    as_group: Union[List[str], None] = Property(
        name="as-group", metadata={"user_settable": False}, default=None
    )
    """Groups to impersonate for the operation"""

    def check(self, **kw: Any) -> Any:
        return unfurl.configurators.k8s.ConnectionConfigurator()

    _valid_target_types = [unfurl_capabilities_Endpoint_K8sCluster]


class unfurl_nodes__K8sResourceHost(tosca.nodes.Root):
    _type_name = "unfurl.nodes._K8sResourceHost"


class unfurl_nodes_K8sCluster(unfurl_nodes__K8sResourceHost):
    _type_name = "unfurl.nodes.K8sCluster"
    name: Union[str, None] = Property(title="Cluster name", default=None)

    api_server: str = Attribute(metadata={"immutable": True})
    """The address and port of the cluster's API server"""

    host: "tosca.capabilities.Container" = Capability(
        factory=tosca.capabilities.Container,
        valid_source_types=["unfurl.nodes.K8sRawResource", "unfurl.nodes.K8sNamespace"],
    )

    endpoint: "unfurl_capabilities_Endpoint_K8sCluster" = Capability(
        factory=unfurl_capabilities_Endpoint_K8sCluster
    )

    def check(self, **kw: Any) -> Any:
        return unfurl.configurators.k8s.ClusterConfigurator()

    def discover(self, **kw: Any) -> Any:
        return unfurl.configurators.k8s.ClusterConfigurator()


class unfurl_nodes_K8sRawResource(tosca.nodes.Root):
    _type_name = "unfurl.nodes.K8sRawResource"
    definition: Union[object, None] = None
    """Inline resource definition (string or map)"""

    src: Union[str, None] = Property(metadata={"user_settable": False}, default=None)
    """File path to resource definition"""

    apiResource: Union[Dict[str, Any], None] = Attribute(default=None)
    name: str = Attribute(default=Eval({"eval": ".name"}))

    host: Union[
        Union["tosca.relationships.HostedOn", "unfurl_nodes__K8sResourceHost"], None
    ] = None

    def check(self, **kw: Any) -> Any:
        return unfurl.configurators.k8s.ResourceConfigurator()

    def discover(self, **kw: Any) -> Any:
        return unfurl.configurators.k8s.ResourceConfigurator()

    def configure(self, **kw: Any) -> Any:
        return unfurl.configurators.k8s.ResourceConfigurator()

    def delete(self, **kw: Any) -> Any:
        return unfurl.configurators.k8s.ResourceConfigurator()


class unfurl_nodes_K8sNamespace(
    unfurl_nodes__K8sResourceHost, unfurl_nodes_K8sRawResource
):
    _type_name = "unfurl.nodes.K8sNamespace"
    name: typing_extensions.Annotated[str, (pattern("^[\\w-]+$"),)] = Property(
        metadata={"immutable": True}, default="default"
    )


class unfurl_nodes_K8sResource(unfurl_nodes_K8sRawResource):  # type: ignore[override]  # ('host',)
    _type_name = "unfurl.nodes.K8sResource"
    name: str = Eval({"eval": ".name"})

    namespace: str = Attribute(
        default=Eval(
            {
                "eval": {
                    "if": {"is_function_defined": "kubernetes_current_namespace"},
                    "then": {"kubernetes_current_namespace": None},
                    "else": {"get_property": ["HOST", "name"]},
                }
            }
        )
    )

    host: Union[
        Union["tosca.relationships.HostedOn", "unfurl_nodes_K8sNamespace"], None
    ] = None


class unfurl_nodes_K8sSecretResource(unfurl_nodes_K8sResource):  # type: ignore[override]  # ('host',)
    _type_name = "unfurl.nodes.K8sSecretResource"
    definition: Union[object, None] = Property(
        metadata={"user_settable": False}, default=None
    )
    """Inline resource definition (string or map)"""

    type: str = "Opaque"
    data: Union[Dict[str, Any], None] = Property(
        metadata={"sensitive": True}, default=None
    )


kube_artifacts: Node = unfurl.nodes.LocalRepository(
    "kube-artifacts",
    _directives=["default"],
)
setattr(
    kube_artifacts,
    "kubectl",
    artifact_AsdfTool(
        "kubectl",
        version="1.25.3",
        file="kubectl",
    ),
)


__all__ = [
    "unfurl_capabilities_Endpoint_K8sCluster",
    "unfurl_relationships_ConnectsTo_K8sCluster",
    "unfurl_nodes__K8sResourceHost",
    "unfurl_nodes_K8sCluster",
    "unfurl_nodes_K8sRawResource",
    "unfurl_nodes_K8sNamespace",
    "unfurl_nodes_K8sResource",
    "unfurl_nodes_K8sSecretResource",
]

