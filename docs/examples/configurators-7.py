import unfurl
import tosca
from unfurl.configurators.templates.docker import (
    unfurl_nodes_Container_Application_Docker,
)
from unfurl.tosca_plugins.artifacts import *

hello_world_container = unfurl_nodes_Container_Application_Docker(
    "hello-world-container",
    image=tosca.artifacts.DeploymentImageContainerDocker(
        "image",
        file="busybox",
    ),
)
hello_world_container.host = tosca.find_node("compute")

configurator_artifacts = unfurl.nodes.LocalRepository(
    "configurator-artifacts",
    _directives=["default"],
)
configurator_artifacts.terraform = artifact_AsdfTool(
    "terraform",
    version="1.1.4",
    file="terraform",
)
configurator_artifacts.gcloud = artifact_AsdfTool(
    "gcloud",
    version="398.0.0",
    file="gcloud",
)
configurator_artifacts.kompose = artifact_AsdfTool(
    "kompose",
    version="1.26.1",
    file="kompose",
)
configurator_artifacts.google_auth = artifact_PythonPackage(
    "google-auth",
    file="google-auth",
)
configurator_artifacts.octodns = artifact_PythonPackage(
    "octodns",
    version="==0.9.14",
    file="octodns",
)
configurator_artifacts.kubernetes_core = artifact_AnsibleCollection(
    "kubernetes.core",
    version="2.4.0",
    file="kubernetes.core",
)
configurator_artifacts.community_docker = artifact_AnsibleCollection(
    "community.docker",
    version="1.10.2",
    file="community.docker",
)
configurator_artifacts.ansible_utils = artifact_AnsibleCollection(
    "ansible.utils",
    version="2.10.3",
    file="ansible.utils",
)
