import collections
import os.path
import hashlib
import json
from ruamel.yaml.comments import CommentedMap
from .tosca import ToscaSpec, TOSCA_VERSION, Artifact

from .support import ResourceChanges, AttributeManager, Status, Priority, NodeState
from .runtime import (
    OperationalInstance,
    NodeInstance,
    CapabilityInstance,
    RelationshipInstance,
)
from .util import UnfurlError, toEnum, sensitive_str, getBaseDir
from .repo import RevisionManager, findGitRepo
from .yamlloader import YamlConfig, loadYamlFromArtifact, yaml
from .job import ConfigChange

# from .configurator import Dependency
import logging

logger = logging.getLogger("unfurl")

_basepath = os.path.abspath(os.path.dirname(__file__))


class Manifest(AttributeManager):
    """
  Loads a model from dictionary representing the manifest
  """

    rootResource = None

    def __init__(self, path, localEnv=None):
        super(Manifest, self).__init__(yaml)
        self.localEnv = localEnv
        self.path = path
        self.repo = self._findRepo()
        self.currentCommitId = self.repo and self.repo.revision
        self.revisions = RevisionManager(self)
        self.changeSets = None
        self.tosca = None
        self.specDigest = None

    def _setSpec(self, spec):
        self._setRepositories(spec)
        self.tosca = self.loadSpec(spec, self.path)
        self.specDigest = self.getSpecDigest(spec)

    def _findRepo(self):
        # check if this path exists in the repo
        repo = self.localEnv and self.localEnv.instanceRepo
        if repo:
            path = repo.findPath(self.path)[0]
            if path and (path, 0) in repo.repo.index.entries:
                return repo
        return None

    def loadSpec(self, spec, path):
        if "service_template" in spec:
            toscaDef = spec["service_template"]
        elif "tosca" in spec:  # backward compat
            toscaDef = spec["tosca"]
        else:
            toscaDef = {}

        # make sure this is present
        toscaDef["tosca_definitions_version"] = TOSCA_VERSION

        if not isinstance(toscaDef, CommentedMap):
            toscaDef = CommentedMap(toscaDef.items())
        # hack so we can make the manifest accessible to the yamlloader:
        toscaDef.manifest = self
        if getattr(toscaDef, "baseDir", None) and (
            not path or toscaDef.baseDir != os.path.dirname(path)
        ):
            # note: we only recorded the baseDir not the name of the included file
            path = toscaDef.baseDir
        return ToscaSpec(toscaDef, spec.get("inputs"), spec, path)

    def getSpecDigest(self, spec):
        m = hashlib.sha1()  # use same digest function as git
        t = self.tosca.template
        for tpl in [spec, t.topology_template.custom_defs, t.nested_tosca_tpls]:
            m.update(json.dumps(tpl, sort_keys=True).encode("utf-8"))
        return m.hexdigest()

    def _ready(self, rootResource):
        self.rootResource = rootResource
        if rootResource:
            rootResource.attributeManager = self

    def getRootResource(self):
        return self.rootResource

    def getBaseDir(self):
        return "."

    @property
    def repositories(self):
        return self.tosca.template.repositories

    @property
    def loader(self):
        return (
            self.rootResource
            and self.rootResource.templar
            and self.rootResource.templar._loader
        )

    def saveJob(self, job):
        pass

    def loadTemplate(self, name, lastChange=None):
        if lastChange:
            try:
                return self.revisions.getRevision(lastChange).tosca.getTemplate(name)
            except:
                return None
        else:
            return self.tosca.getTemplate(name)

    #  load instances
    #    create a resource with the given template
    #  or generate a template setting interface with the referenced implementations

    @staticmethod
    def loadStatus(status, instance=None):
        if not instance:
            instance = OperationalInstance()
        if not status:
            return instance

        instance._priority = toEnum(Priority, status.get("priority"))
        instance._lastStateChange = status.get("lastStateChange")
        instance._lastConfigChange = status.get("lastConfigChange")

        readyState = status.get("readyState")
        if not isinstance(readyState, collections.Mapping):
            instance._localStatus = toEnum(Status, readyState)
        else:
            instance._localStatus = toEnum(Status, readyState.get("local"))
            instance._state = toEnum(NodeState, readyState.get("state"))

        return instance

    @staticmethod
    def loadResourceChanges(changes):
        resourceChanges = ResourceChanges()
        if changes:
            for k, change in changes.items():
                status = change.pop(".status", None)
                resourceChanges[k] = [
                    None if status is None else Manifest.loadStatus(status).localStatus,
                    change.pop(".added", None),
                    change,
                ]
        return resourceChanges

    # XXX unused
    # def loadConfigChange(self, changeId):
    #     """
    # Reconstruct the Configuration that was applied in the past
    # """
    #     changeSet = self.changeSets.get(changeId)
    #     if not changeSet:
    #         raise UnfurlError("can not find changeset for changeid %s" % changeId)
    #
    #     configChange = ConfigChange()
    #     Manifest.loadStatus(changeSet, configChange)
    #     configChange.changeId = changeSet.get("changeId", 0)
    #     configChange.parentId = changeSet.get("parentId")
    #
    #     configChange.inputs = changeSet.get("inputs")
    #
    #     configChange.dependencies = {}
    #     for val in changeSet.get("dependencies", []):
    #         key = val.get("name") or val["ref"]
    #         assert key not in configChange.dependencies
    #         configChange.dependencies[key] = Dependency(
    #             val["ref"],
    #             val.get("expected"),
    #             val.get("schema"),
    #             val.get("name"),
    #             val.get("required"),
    #             val.get("wantList", False),
    #         )
    #
    #     if "changes" in changeSet:
    #         configChange.resourceChanges = self.loadResourceChanges(
    #             changeSet["changes"]
    #         )
    #
    #     configChange.result = changeSet.get("result")
    #     configChange.messages = changeSet.get("messages", [])
    #
    #     # XXX
    #     # ('action', ''),
    #     # ('target', ''), # nodeinstance key
    #     # implementationType: configurator resource | artifact | configurator class
    #     # implementation: repo:key#commitid | className:version
    #     return configChange

    # XXX this is only used by commented out Taskview.createConfigurationSpec()
    # def loadConfigSpec(self, configName, spec):
    #  # XXX need to update configurationSpec in json schema:
    #  #"workflow": { "type": "string" },
    #  #"inputs": { "$ref": "#/definitions/attributes", "default": {} },
    #  #"preconditions": { "$ref": "#/definitions/schema", "default": {} }
    #     return ConfigurationSpec(
    #         configName,
    #         spec["operation"],
    #         spec["className"],
    #         spec.get("majorVersion"),
    #         spec.get("minorVersion", ""),
    #         workflow=spec.get("workflow", Defaults.workflow),
    #         inputs=spec.get("inputs"),
    #         inputSchema=spec.get("inputSchema"),
    #         preConditions=spec.get("preConditions"),
    #         postConditions=spec.get("postConditions"),
    #     )

    def createNodeInstance(self, rname, resourceSpec, parent=None):
        # if parent property is set it overrides the parent argument
        pname = resourceSpec.get("parent")
        if pname:
            parent = self.getRootResource().findResource(pname)
            if parent is None:
                raise UnfurlError("can not find parent instance %s" % pname)

        resource = self._createEntityInstance(NodeInstance, rname, resourceSpec, parent)

        resource._capabilities = []
        for key, val in resourceSpec.get("capabilities", {}).items():
            self._createEntityInstance(CapabilityInstance, key, val, resource)

        resource._requirements = []
        for key, val in resourceSpec.get("requirements", {}).items():
            # parent will be the capability, should have already been created
            capabilityId = val.get("capability")
            if not capabilityId:
                raise UnfurlError("requirement is missing capability %s" % key)
            capability = capabilityId and self.getRootResource().query(capabilityId)
            if not capability or not isinstance(capability, CapabilityInstance):
                raise UnfurlError("can not find capability %s" % capabilityId)
            if capability._relationships is None:
                capability._relationships = []
            requirement = self._createEntityInstance(
                RelationshipInstance, key, val, capability
            )
            requirement.source = resource
            resource.requirements.append(requirement)

        for key, val in resourceSpec.get("instances", {}).items():
            self.createNodeInstance(key, val, resource)

        return resource

    def _getLastChange(self, operational):
        if not operational.lastConfigChange:
            return None
        if not self.changeSets:  # XXX load changesets if None
            return None
        jobId = ConfigChange.getJobId(operational.lastConfigChange)
        return self.changeSets.get(jobId)

    def _createEntityInstance(self, ctor, name, status, parent):
        operational = self.loadStatus(status)
        templateName = status.get("template", name)

        imported = None
        importName = status.get("imported")
        if importName is not None:
            imported = self.imports.findImport(importName)
            if not imported:
                raise UnfurlError("missing import %s" % importName)
            if imported.shadow:
                raise UnfurlError("already imported %s" % importName)
            template = imported.template
        else:
            template = self.loadTemplate(templateName)

        if template is None:
            # not defined in the current model any more, try to retrieve the old version
            if operational.lastConfigChange:
                changerecord = self._getLastChange(operational)
                template = self.loadTemplate(templateName, changerecord)
        if template is None:
            raise UnfurlError("missing resource template %s" % templateName)
        # logger.debug("creating instance for template %s: %s", templateName, template)

        # omit keys that match <<REDACTED>> so can we use the computed property
        attributes = {
            k: v
            for k, v in status.get("attributes", {}).items()
            if v != sensitive_str.redacted_str
        }
        instance = ctor(name, attributes, parent, template, operational)
        if "created" in status:
            instance.created = status["created"]
        if imported:
            self.imports.setShadow(importName, instance)
        return instance

    def findRepoFromGitUrl(self, path, isFile=True, importLoader=None, willUse=False):
        # XXX this doesn't use the tosca template's repositories at all
        repoURL, filePath, revision = findGitRepo(path, isFile, importLoader)
        if not repoURL or not self.localEnv:
            return None, None, None, None
        # explicitRevision = revision
        basePath = importLoader.path  # XXX check if dir or not
        # if not explicitRevision:
        #   revision = self.repoStatus.get(repoURL)
        repo, revision, bare = self.localEnv.findOrCreateWorkingDir(
            repoURL, isFile, revision, basePath
        )
        # if willUse and (not explicitRevision or not bare):
        #   self.updateRepoStatus(repo, revision) # record that this revision is in use
        return repo, filePath, revision, bare

    def findPathInRepos(self, path, importLoader=None, willUse=False):
        """
    Check if the file path is inside a folder that is managed by a repository.
    If the revision is pinned and doesn't match the repo, it might be bare
    """
        if self.localEnv:
            return self.localEnv.findPathInRepos(path, importLoader)
        elif self.repo:
            repo = self.repo
            filePath = repo.findRepoPath(path)
            if filePath is not None:
                return repo, filePath, repo.revision, False
        # XXX
        # if willUse:
        #     self.updateRepoStatus(repo, revision)
        return None, None, None, None

    # def updateRepoStatus(self, repos):
    #   self.repoStatus.update({repo['url'] : repo['commit']
    #       for repo in repos.values() if repo.get('commit')})

    @staticmethod
    def _getRepositories(tpl):
        return tpl.get("spec", {}).get("service_template", {}).get("repositories", {})

    def _setRepositories(self, tpl):
        repositories = tpl.setdefault("service_template", CommentedMap()).setdefault(
            "repositories", CommentedMap()
        )
        if "unfurl" not in repositories:
            # add a repository that points to this package
            repositories["unfurl"] = dict(url="file:" + _basepath)
        if "self" not in repositories:
            # this is called too early to use self.getBaseDir()
            path = getBaseDir(self.path) if self.path else "."
            if self.repo:
                url = self.repo.getGitLocalUrl(path, "self")
            else:
                url = "file:" + path
            repositories["self"] = dict(url=url)
        if "spec" not in repositories:
            # if not found assume it points the project root or self if not in a project
            if (
                self.localEnv
                and self.localEnv.project
                and self.localEnv.project is not self.localEnv.homeProject
            ):
                if self.localEnv.project.projectRepo:
                    assert self.localEnv.project.projectRoot
                    url = self.localEnv.project.projectRepo.getGitLocalUrl(
                        self.localEnv.project.projectRoot, "spec"
                    )
                else:
                    url = "file:" + self.localEnv.project.projectRoot
                repositories["spec"] = dict(url=url)
            else:
                repositories["spec"] = repositories["self"]
        return repositories

    def loadYamlInclude(
        self, yamlConfig, templatePath, baseDir, warnWhenNotFound=False
    ):
        """
        This is called while the YAML config is being loaded.
        Returns (url or fullpath, parsed yaml)
        """

        # make a copy because we don't want to update the configuration while
        # in the middle of loading and merging
        repositories = self._getRepositories(yamlConfig.config)
        tmp = {"service_template": {"repositories": repositories.copy()}}
        repositories = self._setRepositories(tmp)

        if isinstance(templatePath, dict):
            artifactTpl = templatePath.copy()
            path = artifactTpl["file"]
            repo = artifactTpl.get("repository")
            if isinstance(repo, dict):
                # a full repository spec maybe part of the include
                reponame = repo.pop("name", os.path.basename(path))
                # replace spec with just its name
                artifactTpl["repository"] = reponame
                repositories[reponame] = repo
            artifact = Artifact(artifactTpl, path=baseDir)
        else:
            artifact = Artifact(dict(file=templatePath), path=baseDir)

        # loadYamlFromArtifact constructs a dummy TOSCA Import loader so we can invoke its URL resolution mechanism
        context = CommentedMap()
        context["repositories"] = repositories
        context.manifest = self
        context.ignoreFileNotFound = warnWhenNotFound
        return loadYamlFromArtifact(context, artifact, yaml)

    def statusSummary(self):
        def summary(instance, indent):
            print(" " * indent, instance)
            indent += 4
            for child in instance.instances:
                summary(child, indent)

        summary(self.rootResource, 0)


class SnapShotManifest(Manifest):
    def __init__(self, manifest, commitId):
        super(SnapShotManifest, self).__init__(manifest.path, self.localEnv)
        self.commitId = commitId
        oldManifest = manifest.repo.show(manifest.path, commitId)
        self.repo = manifest.repo
        self.localEnv = manifest.localEnv
        self.manifest = YamlConfig(
            oldManifest, manifest.path, loadHook=self.loadYamlInclude
        )
        expanded = self.manifest.expanded
        # just needs the spec, not root resource
        self._setSpec(expanded.get("spec", {}))
        self._ready(None)
