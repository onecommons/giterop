===============
Getting Started
===============

Once you've installed `Unfurl`, you are ready to configure it and create your first Unfurl project.

.. tip::
  Before diving in, it is recommended that you read this high-level overview of `How it works`_ section.


.. _configure:

1. Configure your home environment
===================================

Unfurl creates a "home" directory (by default at ``~/.unfurl_home``) that contains the configuration settings and tools that you want shared by all your Unfurl projects. This is a good place to keep an inventory of the various account settings you'll need to connect to your cloud resources. You can group these settings into `environments` and `Unfurl projects` will inherit them -- making it easy for different projects to switch between environments.

The home project can also be used to isolate the  Unfurl runs in, enabling you set different versions of tools, create Python virtual environment or run Unfurl in a Docker container.

Under the hood, Unfurl's home is just a standard `Unfurl` project modeling the local machine it is running on so you can customize it and deploy it like any other `Unfurl` project.


You have two options for setting up your Unfurl home:

A) Do nothing and use your system's current environment
-------------------------------------------------------

By default, the first time you create an Unfurl project it will automatically generate a home project if it is missing.
It will include configurations to the main cloud providers that will look for their standard environment variables
such as ``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY``.
Individual unfurl projects can filter or set the variables in its environment
through the `variables` subsection of the `environment` in the project file.

B) Explicitly create and edit your home project
-----------------------------------------------

Before you create your first Unfurl project, run ``unfurl home --init`` to create the home project.
It will be placed in ``~/.unfurl_home`` unless you specify otherwise using the ``--home`` option.

Or if you have an existing home project you can just clone it like any other project and then create a new runtime
using the ``runtime`` command, for example: ``unfurl runtime --init ~/.unfurl_home``.

Now you can customize your home project, for example:

1. Edit the default environment and create new environments in ``unfurl.yaml``

2. Add secrets or configure secret manager

3. Edit or add the connections in `unfurl.yaml`
   to use environment variables or using secrets or just hard code the connection's properties.

4. Bootstrap dev tools

5. Deploy local instances, like `Supervisor`.

6. Declare shared and default resources. For example, if several project will use a pre-existing Kubernetes cluster, you could add a template that models it here. You can use the :tosca_spec:`default directive<_Toc50125217>` to indicate that template should only be used if it wasn't already declared in the main project.

``~/.unfurl_home/ensemble.yaml`` contains a template (``asdfBootstrap``) that will install
local versions of the tools Unfurl's :ref:`configurators<configurators>` need (eg. asdf, Terraform, gcloud, and Helm)
It install these tools, run ``unfurl deploy ~/.unfurl_home``.

.. tip::
   Don't worry about making mistakes -- as a regular Unfurl project, your :ref:`.unfurl_home<configure>`
   contains a git repository so you can rollback to previous versions of your config files.


2. Create a project
===================

Now you are ready to create your first Unfurl project.

Run :cli:`unfurl init<unfurl-init>`

This will create a new project and commit it to new git repository unless the
``--existing`` flag is used. If specified, Unfurl will search the current directory and its parents looking for the nearest existing git repository. It will then add the new project to that repository if one is found.

:cli:`unfurl init<unfurl-init>` will also create an ensemble in the project unless the ``--empty`` flag used.
By default, a separate, local git repository will be created for the ensemble. Use the ``--mono`` flag to add the ensemble to the project's git repository or use the ``--submodule`` flag to add the ensemble's git repository as a submodule of the project's git repository.

Keeping the ensemble repository separate from the project repository is useful
if the resources the ensemble creates are transitory or if you want to restrict access to them.
Using the ``--submodule`` option allows those repositories to be easily packaged and shared with the project repository
but still maintain separate access control and git history for each ensemble.

Once created, the project directory will look like:

| ├── .git
| │   └── info/exclude
| │   └── ... (other git stuff)
| ├── .gitignore
| ├── .gitattributes
| ├── unfurl.yaml
| ├── secrets
| │   └── secrets.yaml
| ├── local
| │   └── unfurl.yaml
| ├── .unfurl-local-template.yaml
| ├── ensemble-template.yaml
| ├── ensemble
| │   └── ensemble.yaml

In the folder structure above:

- ``unfurl.yaml`` is the Unfurl project configuration file.
- Any files in ``secrets`` is automatically encrypted before committed.
- ``secrets/secrets.yaml`` is included by ``unfurl.yaml``.
- ``ensemble-template.yaml`` is a template that is shared across ensembles in this project.
- Any files in``local`` is excluded from the git repository
- ``local/unfurl.yaml`` is included by the parent ``unfurl.yaml``
  and is where you'll put local or private settings you don't want to commit.
- ``.unfurl-local-template.yaml`` is the template used generate a new ``local/unfurl.yaml`` when the project is cloned.
- ``ensemble`` is the folder that contains the default ensemble
  (use the ``--empty`` flag to skip creating this).
- ``ensemble/ensemble.yaml`` is the manifest file for this ensemble. It includes ``ensemble-template.yaml``.
- Private repository folders (like ``ensemble``) are listed in ``.git/info/exclude``

.. _create_servicetemplate:

3. Create a service template
============================

If you look at :ref:`ensemble_template.yaml<ensemble_yaml>`, you'll see that it contains a minimal template with one node template and one workflow.
Workflows are optional but defining one is the simplest way to get started,
because you just need to declare procedural steps instead of designing model of your topology.

Topology and Orchestration Specification for Cloud Applications (TOSCA) is an OASIS standard language to describe a topology of cloud based web services,
their components, relationships, and the processes that manage them.
The TOSCA standard includes specifications to describe processes that create or modify web services. You can read more about it on the OASIS website.

You can find examples

https://github.com/oasis-open/tosca-community-contributions/tree/master/examples/1.3/tutorial

.. tab-set-code::

  .. code-block:: YAML

    apiVersion: unfurl/v1alpha1
    kind: Ensemble
    spec:
        service_template:
          topology_template:
            node_templates:
              my_server:
                type: tosca.nodes.Compute
                capabilities:
                  # Host container properties
                  host:
                    properties:
                      num_cpus: 1
                      disk_size: 200GB
                      mem_size: 512MB
                  # Guest Operating System properties
                  os:
                    properties:
                      # host Operating System image properties
                      architecture: x86_64
                      type: linux
                      distribution: ubuntu
                      version: focal

  .. code-block:: python

    my_server = tosca.nodes.Compute(
    "my_server",
    host=tosca.capabilities.Compute(
        num_cpus=1,
        disk_size=200 * GB,
        mem_size=512 * MB,
      ),
    os=tosca.capabilities.OperatingSystem(
        architecture="x86_64",
        type="linux",
        distribution="ubuntu",
        version=tosca_version("focal"),
      ),
    )

    
4. Implementing an operation
============================

Of course, we don't have enough information "my_server" to actually create a compute instance -- it could be, for example, a physical machine, a virtual machine, a docker image or Kubernetes pod.
"my_server" are a set of abstract constraints that be applied to any number of

It is the implementation that create (or discover) instances that conforms to this specification.
Implementations are defined by specifying how to carry ouy operations that are applied to the node templates.
TOSCA defines a vocabulary of a few standard operations such as "create" or "delete" and you can define your own.
Their implementations can be a simple as the name of a shell script to invoke or yaml specification that is passed to a `configurator`,
which is Unfurl's plugin system for implementing operations.
Unfurl ships with several configurators, including ones for Ansible, Terraform and Kubernetes.

We can implement ``my_server`` in just few lines of YAML by Google Cloud Platform by calling the ``gcloud`` tool.
We'll start with "delete" to make the

.. tab-set-code::

  .. code-block:: YAML

    apiVersion: unfurl/v1alpha1
    kind: Ensemble
    spec:
      service_template:
      topology_template:
        node_templates:
          my_server:
            type: tosca.nodes.Compute
            # details omitted, see example above
            interfaces:
              Standard:
                delete:
                  implementation: gcloud compute instances delete {{ '.name' | eval }}
              # ... see example below for more operations

  .. code-block:: python

    @operation(name="delete")
    def my_server_delete(**kw):
        return unfurl.configurators.shell.ShellConfigurator(
            command=Eval("gcloud compute instances delete {{ '.name' | eval }}"),
        )

    my_server.delete = my_server_delete


Creates a little more verbose and illustrates how to pass input parameters and set attributes on the instance created from a template:

.. tab-set-code::

  .. code-block:: YAML

    apiVersion: unfurl/v1alpha1
    kind: Ensemble
    spec:
      service_template:
        topology_template:
          node_templates:
            my_server:
              type: tosca.nodes.Compute
              # details omitted, see example above
              interfaces:
                Standard:
                  delete:
                    implementation: gcloud compute instances delete {{ '.name' | eval }}
                  create:
                    implementation: |
                      gcloud compute instances create {{ '.name' | eval }}
                        --boot-disk-size={{ {"get_property": ["SELF", "host", "disk_size"]} | eval | regex_replace(" ") }}
                        --image=$(gcloud compute images list --filter=name:{{ {'get_property': ['SELF', 'os', 'distribution']} | eval }}
                              --filter=name:focal --limit=1 --uri)
                        --machine-type=e2-medium   > /dev/null
                      && gcloud compute instances describe {{ '.name' | eval }} --format=json
                    inputs:
                      resultTemplate:
                        # recursively merge the map with the yaml anchor "gcloudStatusMap"
                        +*gcloudStatusMap:
                        eval:
                          then:
                            attributes:
                              public_ip: "{{ result.networkInterfaces[0].accessConfigs[0].natIP }}"
                              private_ip: "{{ result.networkInterfaces[0].networkIP }}"
                              zone: "{{ result.zone | basename }}"
                              id:  "{{ result.selfLink }}"
                    # ...  see below

  .. code-block:: python

    @operation(name="delete")
    def my_server_delete(**kw):
        return unfurl.configurators.shell.ShellConfigurator(
            command=Eval("gcloud compute instances delete {{ '.name' | eval }}"),
        )


    @operation(name="create")
    def my_server_create(**kw):
        return unfurl.configurators.shell.ShellConfigurator(
            command=Eval(
                (
                    "gcloud compute instances create {{ '.name' | eval }}\n"
                    '--boot-disk-size={{ {"get_property": ["SELF", "host", "disk_size"]} | eval | '
                    'regex_replace(" ") }}\n'
                    "--image=$(gcloud compute images list --filter=name:{{ {'get_property': "
                    "['SELF', 'os', 'distribution']} | eval }}\n"
                    "      --filter=name:focal --limit=1 --uri)\n"
                    "--machine-type=e2-medium   > /dev/null\n"
                    "&& gcloud compute instances describe {{ '.name' | eval }} --format=json\n"
                )
            ),
            resultTemplate=Eval(
                {
                    "eval": {
                        "then": {
                            "attributes": {
                                "public_ip": "{{ "
                                "result.networkInterfaces[0].accessConfigs[0].natIP "
                                "}}",
                                "private_ip": "{{ "
                                "result.networkInterfaces[0].networkIP "
                                "}}",
                                "zone": "{{ result.zone | basename }}",
                                "id": "{{ result.selfLink }}",
                            }
                        }
                    }
                }
            ),
        )

    my_server.delete = my_server_delete
    my_server.create = my_server_create


This implementation calls ``gcloud compute instances create`` to create the instance
and then ``gcloud compute instances describe``. The ``resultTemplate`` parses that json and

One mysterious looking line is ``+*gcloudStatusMap:`` which is a merge directive
That's because its an anchor to a yaml map we haven't defined yet.
We'll see it when we finish off the implementation by defining the "check" operation:

.. tab-set-code::

  .. code-block:: YAML
    
    apiVersion: unfurl/v1alpha1
    kind: Ensemble
    spec:
      service_template:
        topology_template:
          node_templates:
            my_server:
              type: tosca.nodes.Compute
              # details omitted...
              interfaces:
                # other operations omitted, see example above
                Install:
                  check:
                    implementation: gcloud compute instances describe {{ '.name' | eval }}  --format=json
                    inputs:
                      resultTemplate: &gcloudStatusMap
                        eval:
                          if: $result
                          then:
                            readyState:
                              state: "{{ {'PROVISIONING': 'creating', 'STAGING': 'starting',
                                        'RUNNING': 'started', 'REPAIRING' 'error,'
                                        'SUSPENDING': 'stopping',  'SUSPENDED': 'stopped',
                                        'STOPPING': 'deleting', 'TERMINATED': 'deleted'}[result.status] }}"
                              local: "{{ {'PROVISIONING': 'pending', 'STAGING': 'pending',
                                        'RUNNING': 'ok', 'REPAIRING' 'error,'
                                        'SUSPENDING': 'error',  'SUSPENDED': 'error',
                                        'STOPPING': 'absent', 'TERMINATED': 'absent'}[result.status] }}"
                        vars:
                          result: "{%if success %}{{ stdout | from_json }}{% endif %}"

  .. code-block:: python

    @operation(name="check")
    def my_server_check(**kw):
        return unfurl.configurators.shell.ShellConfigurator(
            command=Eval(
                "gcloud compute instances describe {{ '.name' | eval }}  --format=json"
            ),
            resultTemplate=Eval(
                {
                    "eval": {
                        "if": "$result",
                        "then": {
                            "readyState": {
                                "state": "{{ {'PROVISIONING': "
                                "'creating', 'STAGING': "
                                "'starting', 'RUNNING': "
                                "'started', 'REPAIRING' "
                                "'error,' 'SUSPENDING': "
                                "'stopping',  'SUSPENDED': "
                                "'stopped', 'STOPPING': "
                                "'deleting', 'TERMINATED': "
                                "'deleted'}[result.status] }}",
                                "local": "{{ {'PROVISIONING': 'pending', "
                                "'STAGING': 'pending', "
                                "'RUNNING': 'ok', 'REPAIRING' "
                                "'error,' 'SUSPENDING': "
                                "'error',  'SUSPENDED': "
                                "'error', 'STOPPING': 'absent', "
                                "'TERMINATED': "
                                "'absent'}[result.status] }}",
                            }
                        },
                    },
                    "vars": {
                        "result": "{%if success %}{{ stdout | from_json }}{% endif %}"
                    },
                }
            ),
        )


    my_server.check = my_server_check


The "check" operation is part of the ``Install`` interface, an Unfurl specific TOSCA extention.
It defines a "check" operation for checking the status of an existing interface; a "discover" operation for discovering pre-existing instances
and a "revert" operation for reverting changes made by Unfurl on a pre-existing resource.

The ``resultTemplate`` (shared with ``create``) maps Google Compute ["status" enumeration](https://cloud.google.com/compute/docs/instances/instance-life-cycle) to TOSCA's node state and to Unfurl's operation status.
We can see that it uses TOSCA's functions with Ansible's Jinja2 expressions and filters, glued together using Unfurl's expression syntax (``eval``)
https://docs.ansible.com/ansible/latest/user_guide/playbooks_filters.html

All together we have created 

.. tab-set-code::

  .. code-block:: YAML

    apiVersion: unfurl/v1alpha1
    kind: Ensemble
    spec:
      service_template:
        topology_template:
          node_templates:
            my_server:
              type: tosca.nodes.Compute
              capabilities:
                # Host container properties
                host:
                  properties:
                    num_cpus: 1
                    disk_size: 200GB
                    mem_size: 512MB
                # Guest Operating System properties
                os:
                  properties:
                    # host Operating System image properties
                    architecture: x86_64
                    type: linux
                    distribution: ubuntu
                    version: focal
              interfaces:
                Standard:
                  delete:
                    implementation: gcloud compute instances delete {{ '.name' | eval }}        
                  create:
                    implementation: |
                      gcloud compute instances create {{ '.name' | eval }}
                      --boot-disk-size={{ {"get_property": ["SELF", "host", "disk_size"]} | eval | regex_replace(" ") }}
                      --image=$(gcloud compute images list --filter=name:{{ {'get_property': ['SELF', 'os', 'distribution']} | eval }}
                            --filter=name:focal --limit=1 --uri)
                      --machine-type=e2-medium   > /dev/null
                      && gcloud compute instances describe {{ '.name' | eval }} --format=json
                    inputs:
                      resultTemplate:
                        # recursively merge the map with the yaml anchor "gcloudStatusMap"
                        # +*gcloudStatusMap:
                        eval: 
                          then:
                            attributes:
                              public_ip: "{{ result.networkInterfaces[0].accessConfigs[0].natIP }}"
                              private_ip: "{{ result.networkInterfaces[0].networkIP }}"
                              zone: "{{ result.zone | basename }}"
                              id:  "{{ result.selfLink }}"
                Install:
                  check:
                    implementation: gcloud compute instances describe {{ '.name' | eval }}  --format=json
                    inputs:
                      resultTemplate: &gcloudStatusMap
                        eval:
                          if: $result
                          then:
                            readyState:
                              state: "{{ {'PROVISIONING': 'creating', 'STAGING': 'starting',
                                        'RUNNING': 'started', 'REPAIRING' 'error,'
                                        'SUSPENDING': 'stopping',  'SUSPENDED': 'stopped',
                                        'STOPPING': 'deleting', 'TERMINATED': 'deleted'}[result.status] }}"
                              local: "{{ {'PROVISIONING': 'pending', 'STAGING': 'pending',
                                        'RUNNING': 'ok', 'REPAIRING' 'error,'
                                        'SUSPENDING': 'error',  'SUSPENDED': 'error',
                                        'STOPPING': 'absent', 'TERMINATED': 'absent'}[result.status] }}"
                        vars:
                          result: "{%if success %}{{ stdout | from_json }}{% endif %}"

  .. literalinclude:: ./examples/get-started.py
    :language: python


5 Activate your ensemble
========================

1. Run deploy
2. Commit your changes

.. _publish_project:

6. Publish your project
=======================

You can publish and share your projects like any git repository.
If you want to publish local git repositories on a git hosting service like github.com
(e.g. ones created by ``unfurl init`` or ``unfurl clone``) follow these steps:

1. Create corresponding empty remote git repositories.
2. Set the new repositories as the remote origins for your local repositories
   with this command:

   ``git remote set-url origin <remote-url>``

   Or, if the repository is a git submodule set the URL use:

   ``git submodule set-url <path> <remote-url>``

3. Commit any needed changes in the repositories.
4. Running ``unfurl git push`` will push all the repositories in the project.
