:orphan:

.. _interfaces_spec:

Interfaces
==========

Interfaces provide a way to map logical tasks to executable operations.

Declaration
+++++++++++

Node Types and Interfaces
-----------------------------

.. code:: yaml

 node_types:
   some_type:
     ...
     interfaces:
       interface1:
         type: <interface type name>
         inputs:
           ..
         operations:
            op1:
              ...
            op2:
              ...
       interface2:
         ...


Each interface declaration under the ``interfaces`` section is a dictionary of operations.

Operation Declaration in Node Types Interfaces
----------------------------------------------------------------

.. code:: yaml

 node_types:
   some_type:
     interfaces:
       interface1:
         operations:
            op1:
              implementation: ...
              inputs:
                ...

Definition
++++++++++

.. list-table::
   :widths: 10 10 10 50
   :header-rows: 1

   * - Keyname
     - Required
     - Type
     - Description
   * - implementation
     - yes
     - string
     - The script or plugin task name to execute.
   * - inputs
     - no
     - dict
     - Schema of inputs that will be passed to the implementation.

Simple Mapping
++++++++++++++

.. code:: yaml

 node_types:

   some_type:
     interfaces:
       interface1:
         op1: plugin_name.path.to.module.task

When mapping an operation to an implementation, if there is no need to
pass inputs the full mapping structure can be
avoided and the implementation can be written directly.

Operation Input Schema Definition
+++++++++++++++++++++++++++++++++

.. code:: yaml

 node_types:
   some_type:
     interfaces:
       interface1:
         op1:
           implementation: ...
           inputs:
             input1:
               description: ...
               type: ...
               default: ...


.. list-table::
   :widths: 10 10 10 50
   :header-rows: 1

   * - Keyname
     - Required
     - Type
     - Description
   * - description
     - no
     - string
     - Description for the input.
   * - type
     - no
     - string
     - Input type. Not specifying a data type means the type can be anything (also types not listed in the valid types). Valid types: string, integer, boolean
   * - default
     - no
     - <any>
     - An optional default value for the input.


Node Templates Interface Definition
++++++++++++++++++++++++++++++++++++


Operation Inputs in Node Templates Interfaces
---------------------------------------------

.. code:: yaml

 node_types:
   some_type:
     interfaces:
       interface1:
         op1:
           implementation: myArtifact
           inputs:
             input1:
               description: some mandatory input
             input2:
               description: some optional input with default
               default: 1000

 node_templates:
   some_node:
     interfaces:
       interface1:
         op1:
           inputs:
             input1: mandatory_input_value
             input3: some_additional_input



When an operation in a node template interface is inherited from a node type or a requirement interface:

* All inputs that were declared in the operation inputs schema must be provided.
* Additional inputs, which were not specified in the operation inputs schema, may be passed as well.

Examples
++++++++

In the following examples, we will declare an interface which will allow us to:

* Configure a master deployment server using a plugin.
* Deploy code on the hosts using a plugin.
* Verify that the deployment succeeded using a shell script.
* Start the application after the deployment ended.

For the sake of simplicity, we will not refer to :ref:`requirements<requirements>` in these examples.

Configuring Interfaces in Node Types
------------------------------------

Configuring the master server:

.. code:: yaml

 node_types:
   nodejs_app:
     derived_from: tosca.nodes.ApplicationModule
     properties:
       ...
     interfaces:
       my_deployment_interface:
         configure:
           implementation: deployer.config_in_master.configure

 node_templates:
   nodejs:
     type: nodejs_app

In this example, we’ve:

* Declared a ``deployer`` plugin which, `by default <#overriding-the-executor>`__, should execute its operations on the TOSCA manager.
* Declared a :ref:`node type<node_types>` with a ``my_deployment_interface`` interface that has a single ``configure`` operation which is mapped to the ``deployer.config_in_master.configure`` task.
* Declared a ``nodejs`` node template of type ``nodejs_app``.


.. code:: yaml

 node_types:
   nodejs_app:
     derived_from: tosca.nodes.ApplicationModule
     properties:
       ...
     interfaces:
       my_deployment_interface:
         configure:
           implementation: deployer.config_in_master.configure
         deploy:
           implementation: deployer.deploy_framework.deploy

 node_templates:
   vm:
     type: tosca.openstack.nodes.Server
   nodejs:
     type: nodejs_app


Here we added a ``deploy`` operation to our ``my_deployment_interface``
interface.

Declaring an operation implementation within the node
-----------------------------------------------------

You can specify a full operation definition within the node’s interface under the node template itself.

.. code:: yaml

 node_types:
   nodejs_app:
     derived_from: tosca.nodes.ApplicationModule
     properties:
       ...
     interfaces:
       my_deployment_interface:
         ...

 node_templates:
   vm:
     type: tosca.openstack.nodes.Server
   nodejs:
     type: nodejs_app
     interfaces:
       my_deployment_interface:
         ...
         start: scripts/start_app.sh


Let’s say that we use our ``my_deployment_interface`` on more than the ``nodejs`` node. While on all other nodes, a ``start`` operation is not mapped to anything, we’d like to have a ``start`` operation for the ``nodejs`` node specifically, which will run our application after it is deployed.

Here, we’ve declared a ``start`` operation and mapped it to execute a script specifically on the ``nodejs`` node.

This comes to show that you can define your interfaces either in ``node_types`` or in ``node_templates`` depending on whether you want to reuse the declared interfaces in diffrent nodes or declare them in specific nodes.

Operation Inputs
----------------

Operations can specify inputs that will be passed to the implementation.

.. code:: yaml

 plugins:
   deployer:
     executor: central_deployment_agent

 node_types:
   nodejs_app:
     derived_from: tosca.nodes.ApplicationModule
     properties:
       ...
     interfaces:
       my_deployment_interface:
         configure:
           ...
         deploy:
           implementation: deployer.deploy_framework.deploy
           inputs:
             source:
               description: deployment source
               type: string
               default: git
         verify:
           implementation: scripts/deployment_verifier.py

 node_templates:
   vm:
     type: tosca.openstack.nodes.Server
   nodejs_app:
     type: tosca.nodes.WebServer
     interfaces:
       my_deployment_interface:
         ...
         start:
           implementation: scripts/start_app.sh
           inputs:
             app: my_web_app
             validate: true


Here, we added an input to the ``deploy`` operation under the ``my_deployment_interface`` interface in our ``nodejs_app`` node type and two inputs to the ``start`` operation in the ``nodejs`` node’s interface.

.. seealso:: For more information, refer to :tosca_spec2:`TOSCA Interface Section <_Toc50125307>`
