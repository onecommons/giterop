# Copyright (c) 2020 Adam Souzis
# SPDX-License-Identifier: MIT

"""
An ensemble can several directories related generated by deployment jobs:

:artifacts: Artifacts required for deployment (e.g. Terraform state files).
:secrets: sensitive artifacts (e.g. certificates). They are vault encrypted in the repository.
:local: Artifacts specific to this installation and so excluded from the repository (e.g. a pidfile)
:tasks: The most recently generated configuration files for each instance (for informational purposes only -- excluded from repository and safe to delete).

Each of these directories will contain subdirectories named after each instance in the ensemble; their contents are populated as those instances are deployed.

When a plan is being generated, a directory named "planned" will be created which will have the same directory structure as above. When the job executes the plan those files will be moved to the corresponding directory if the task successfully deployed, otherwise it will be moved to a directory named ``failed.<changeid>``.

When a task runs, its configurator has access to these directories that it can use
to store artifacts in the ensemble's repository or for generating local configuration files.
For this, each deployed instance can have its own set of directories (see `_get_base_dir()`).

Because generating a plan should not impact what is currently deployed, during the
during planning and rendering phase, a configurator can use the `WorkFolder` interface
to read and write from temporary copies of those folders the "planned" directory that will be discarded or committed if the task fails or succeeds.

This also enables the files generated by the plan to be manually examined
 -- useful for development, error diagnosis and user intervention, or as part of a git-based approval process.
"""

# goals:
# persistent folders never / not left in error state (not overwritten until success)
# relatives paths to files outside of work folder still work after job runs (i.e. planned and active same level deep)
# separate, disposable directory for plan

# start job: remove "planned"
# during job:
# creating a Workfolder creates "planned", copy from "active" if requested
# if Workfolder.apply() is called, move "planned" to "active"

# when job ends:
# call apply() on successful tasks
#  (failed task remain in planned)

import os.path
import os
import stat
import shutil
import codecs
from collections.abc import MutableSequence
import typing
from typing import Optional

from .eval import RefContext, set_eval_func, map_value
from .result import ExternalValue
from .util import (
    UnfurlError,
    wrap_sensitive_value,
    save_to_tempfile,
    save_to_file,
)
from .yamlloader import cleartext_yaml
import logging

logger = logging.getLogger("unfurl")

if typing.TYPE_CHECKING:
    from .manifest import Manifest


class Folders:
    artifacts = "artifacts"
    secrets = "secrets"
    local = "local"
    tasks = "tasks"
    operation = "operation"
    workflow = "workflow"
    Persistent = ("artifacts", "secrets", "local")
    Job = ("tasks", "operation", "workflow")
    Planned = "planned"
    Active = "active"


for r in Folders.Persistent + Folders.Job:
    setattr(Folders, r, r)


def rename_dir(src, dst, logger=logger):
    try:
        dir = os.path.dirname(dst)
        if not os.path.exists(dir):
            os.makedirs(dir)
        elif os.path.exists(dst):
            logger.error("failed to rename %s to %s: dst already exists", src, dst)
            return
        os.rename(src, dst)
    except OSError as e:
        logger.error("failed to rename %s to %s: %s", src, dst, str(e))
    else:
        logger.trace("renamed %s to %s", src, dst)


def rmtree(path, logger=logger):
    errors = []
    if not os.path.exists(path):
        return False

    def rm_error(func, path, excinfo):
        errors.append(path)

    shutil.rmtree(path, onerror=rm_error)
    if errors:
        logger.error(
            "failed to remove directory %s, the following failed to delete: %s",
            path,
            "\n".join(errors),
        )
        return False
    else:
        logger.trace("removed directory %s", path)
        return True


class WorkFolder:
    """
    Provides access to the directories associated with instances and tasks,
    such as the directories for reading and writing artifacts and secrets.

    Updates to these directories through this class are performed transactionally --
    accessing a directory through this class marks it for writing and creates a
    copy of it in the ``planning`` directory.

    If a task completes successfully ``apply()`` is called, which copies it back to the
    permanent location of the folder.
    """

    always_apply = False

    def __init__(self, task, location: str, preserve: bool):
        self.task = task  # owner
        self.pending_state = True
        self.location = location
        self.preserve = preserve
        # the permanent location:
        self._active = self._get_job_path(task, location, Folders.Active).rstrip(os.sep)
        self._pending = self._get_job_path(task, location, Folders.Planned).rstrip(
            os.sep
        )

    @property
    def cwd(self):
        if self.pending_state:
            return self._pending
        else:
            return self._active

    def get_current_path(self, path, mkdir=True):
        if self.pending_state:
            return self.pending_path(path, mkdir)
        else:
            return self.permanent_path(path, mkdir)

    def permanent_path(self, path, mkdir=True):
        """An absolute path to the permanent location of this directory."""
        dir = self._active
        if mkdir and not os.path.exists(dir):
            os.makedirs(dir)
        return os.path.join(dir, path or "")

    def copy_from(self, path):
        filename = os.path.basename(path)
        return shutil.copy(path, self.get_current_path(filename))

    def copy_to(self, path):
        dir, filename = os.path.split(path)
        if not os.path.exists(dir):
            os.makedirs(dir)
        return shutil.copy(self.get_current_path(filename, False), path)

    def pending_path(self, path=None, mkdir=True):
        """An absolute path to the planning location of this directory."""
        if mkdir and not os.path.exists(self._pending):
            # lazily create the pending folder
            self._start()
        return os.path.join(self._pending, path or "")

    def relpath_to_current(self, path):
        return os.path.relpath(path, self.cwd)

    def write_file(self, contents, name, encoding=None):
        """Create a file with the given contents

        Args:
            contents: .
            name (string): Relative path to write to.
            encoding (string): (Optional) One of "binary", "vault", "json", "yaml"
                               or an encoding registered with the Python codec registry.

        Returns:
          str: An absolute path to the file.
        """
        # XXX don't write till commit time
        ctx = self.task.inputs.context
        path = self.get_current_path(name)
        assert os.path.isabs(path), path
        if self.location == Folders.artifacts or encoding == "vault":
            yaml = None  # default yaml will encrypt if configured
        else:
            # don't encrypt files that arent' being commited to the repo
            # or already going to be encrypted (i.e. the secrets folders)
            yaml = cleartext_yaml
        write_file(ctx, contents, path, self.location, encoding, yaml=yaml)
        return path

    @staticmethod
    def _get_job_path(task, name: str, pending: str):
        instance = task.target
        if name in [Folders.artifacts, Folders.secrets, Folders.local, Folders.tasks]:
            return os.path.join(
                instance.base_dir, pending, name, _get_instance_dir_name(instance)
            )
        elif name == Folders.operation:
            return os.path.join(
                instance.base_dir,
                pending,
                "tasks",
                _get_instance_dir_name(instance),
                task.configSpec.operation,
            )
        elif name == Folders.workflow:
            return os.path.join(
                instance.base_dir,
                pending,
                "tasks",
                _get_instance_dir_name(instance),
                task.configSpec.workflow,
            )
        elif name == Folders.tasks:
            return os.path.join(
                instance.base_dir, pending, "tasks", _get_instance_dir_name(instance)
            )
        else:
            assert False, f"unexpected name '{name}' for workfolder"

    def _start(self):
        # create the .pending folder
        pendingpath = self._pending
        if self.preserve and os.path.exists(self._active):
            shutil.copytree(self._active, pendingpath)
        if not os.path.exists(pendingpath):
            os.makedirs(pendingpath)

        self.task.logger.trace(
            'created pending project path "%s" for %s',
            pendingpath,
            self.task.target.name,
        )
        return pendingpath

    def apply(self) -> str:
        # save_as_previous = False
        pendingpath = self._pending
        renamed_path = ""
        if os.path.exists(pendingpath):
            if os.path.exists(self._active):
                # if save_as_previous:
                #     previouspath = self._get_job_path(
                #         self.task, self.location, self.PREVIOUS_EXT
                #     )
                #     if os.path.exists(previouspath):
                #         self._rmtree(previouspath)
                #     # rename the current version as previous
                #     self._rename_dir(self._active, previouspath)
                # else:
                self._rmtree(self._active)
            # rename the pending version as the current one
            self._rename_dir(pendingpath, self._active)
            renamed_path = self._active
        self.pending_state = False
        return renamed_path

    def discard(self) -> None:
        pendingpath = self._pending
        if os.path.exists(pendingpath):
            self._rmtree(pendingpath)

    def failed(self) -> str:
        pendingpath = self._pending
        errorpath = ""
        if os.path.exists(pendingpath):
            error_dir = "failed." + self.task.changeId
            errorpath = self._get_job_path(self.task, self.location, error_dir)
            self._rename_dir(pendingpath, errorpath)
        self.pending_state = False
        return errorpath

    def _rename_dir(self, src, dst):
        return rename_dir(src, dst, self.task.logger)

    def _rmtree(self, path):
        return rmtree(path, self.task.logger)

    # XXX after run complets:
    #
    # def commit(self):
    #   if os.path.exists(previouspath):
    #        self._rmtree(previouspath)
    #
    # def rollback(self):
    #     # during apply, a task that can safely rollback can call this to revert this to the previous version
    #     if not os.path.exists(self.cwd):
    #         return
    #     shutil.move(self.cwd, self.cwd + self.ERROR_EXT)
    #     if os.path.exists(self.cwd + self.PREVIOUS_EXT):
    #         # restore previous
    #         shutil.move(self.cwd + self.PREVIOUS_EXT, self.cwd)


class File(ExternalValue):
    """
    Represents a local file.
    get() returns the given file path (usually relative)
    `encoding` can be "binary", "vault", "json", "yaml" or an encoding registered with the Python codec registry
    """

    def __init__(self, name, baseDir="", loader=None, yaml=None, encoding=None):
        super().__init__("file", name)
        self.base_dir = baseDir or ""
        self.loader = loader
        self.yaml = yaml
        self.encoding = encoding

    def write(self, obj):
        encoding = self.encoding if self.encoding != "binary" else None
        path = self.get_full_path()
        logger.debug("writing to %s", path)
        save_to_file(path, obj, self.yaml, encoding)

    def get_full_path(self):
        return os.path.abspath(os.path.join(self.base_dir, self.get()))

    def get_contents(self):
        path = self.get_full_path()
        with open(path, "rb") as f:
            contents = f.read()
        if self.loader:
            contents, show = self.loader._decrypt_if_vault_data(contents, path)
        else:
            show = True
        if self.encoding != "binary":
            try:
                # convert from bytes to string
                contents = codecs.decode(contents, self.encoding or "utf-8")  # type: ignore
            except ValueError:
                pass  # keep at bytes
        if not show:  # it was encrypted
            return wrap_sensitive_value(contents)
        else:
            return contents

    def __digestable__(self, options):
        return self.get_contents()

    def resolve_key(self, name=None, currentResource=None):
        """
        Key can be one of:

        path # absolute path
        contents # file contents (None if it doesn't exist)
        encoding
        """
        if not name:
            return self.get()

        if name == "path":
            return self.get_full_path()
        elif name == "encoding":
            return self.encoding or "utf-8"
        elif name == "contents":
            return self.get_contents()
        else:
            raise KeyError(name)


def _file_func(arg, ctx):
    kw = map_value(ctx.kw, ctx)
    writing = "contents" in kw
    encoding = map_value(kw.get("encoding"), ctx)
    if writing and encoding != "vault":
        yaml = cleartext_yaml
    else:
        yaml = ctx.currentResource.root.attributeManager.yaml
    file = File(
        map_value(arg, ctx),
        map_value(kw.get("dir", ctx.currentResource.base_dir), ctx),
        ctx.templar and ctx.templar._loader,
        yaml,
        encoding,
    )
    if writing:
        file.write(map_value(kw["contents"], ctx))
    return file


set_eval_func("file", _file_func)


class TempFile(ExternalValue):
    """
    Represents a temporary local file.
    get() returns the given file path (usually relative)
    """

    def __init__(self, obj, suffix="", yaml=None, encoding=None):
        tp = save_to_tempfile(obj, suffix, yaml=yaml, encoding=encoding)
        super().__init__("tempfile", tp.name)
        self.tp = tp

    def __digestable__(self, options):
        return self.resolve_key("contents")

    def resolve_key(self, name=None, currentResource=None):
        """
        path # absolute path
        contents # file contents (None if it doesn't exist)
        """
        if not name:
            return self.get()

        if name == "path":
            return self.tp.name
        elif name == "contents":
            with open(self.tp.name, "r") as f:
                return f.read()
        else:
            raise KeyError(name)


set_eval_func(
    "tempfile",
    lambda arg, ctx: TempFile(
        map_value(map_value(arg, ctx), ctx),  # XXX
        ctx.kw.get("suffix"),
        ctx.currentResource.root.attributeManager.yaml
        if ctx.kw.get("encoding") == "vault"
        else cleartext_yaml,
        ctx.kw.get("encoding"),
    ),
)


class FilePath(ExternalValue):
    def __init__(self, abspath, base_dir="", rel_to=""):
        abspath = str(abspath)  # in case is a pathlib.Path
        super().__init__("path", os.path.normpath(abspath))
        self.path = abspath[len(base_dir) + 1 :]
        self.rel_to = rel_to

    def get_full_path(self):
        return self.get()

    def __digestable__(self, options):
        fullpath = self.get_full_path()
        stablepath = self.rel_to + ":" + self.path
        if not os.path.exists(fullpath):
            return "path:" + stablepath

        manifest: Optional["Manifest"] = options and options.get("manifest")
        if manifest:
            repo, relPath, revision, bare = manifest.find_path_in_repos(
                self.get_full_path()
            )
            if repo and not repo.is_path_excluded(relPath):
                if relPath:
                    # equivalent to git rev-parse HEAD:path
                    digest = "git:" + repo.repo.rev_parse("HEAD:" + relPath).hexsha
                else:
                    digest = "git:" + revision  # root of repo
                if repo.is_dirty(True, relPath):
                    fstat = os.stat(fullpath)
                    return f"{digest}:{fstat[stat.ST_SIZE]}:{fstat[stat.ST_MTIME]}"
                else:
                    return digest

        if os.path.isfile(fullpath):
            with open(fullpath, "r") as f:
                return "contents:" + f.read()
        else:
            fstat = os.stat(fullpath)
            return f"stat:{stablepath}:{fstat[stat.ST_SIZE]}:{fstat[stat.ST_MTIME]}"


def _get_path(ctx, path, relativeTo=None, mkdir=True):
    if os.path.isabs(path):
        return path, ""

    base = _get_base_dir(ctx, relativeTo)
    if base is None:
        raise UnfurlError(f'Named directory or repository "{relativeTo}" not found')
    fullpath = os.path.join(base, path)
    if mkdir:
        dir = os.path.dirname(fullpath)
        if len(dir) < len(base):
            dir = base
        if not os.path.exists(dir):
            os.makedirs(dir)
    return fullpath, base


def get_path(ctx, path, relativeTo=None, mkdir=False):
    return _get_path(ctx, path, relativeTo, mkdir)[0]


def _abspath(ctx, path, relativeTo=None, mkdir=False):
    abspath, basedir = _get_path(ctx, path, relativeTo, mkdir)
    return FilePath(abspath, basedir, relativeTo)


def _getdir(ctx, folder, mkdir=False):
    return _abspath(ctx, "", folder, mkdir)


def _map_args(args, ctx):
    args = map_value(args, ctx)
    if not isinstance(args, MutableSequence):
        return [args]
    else:
        return args


# see also abspath in filter_plugins.ref
set_eval_func("abspath", lambda arg, ctx: _abspath(ctx, *_map_args(arg, ctx)))

set_eval_func("get_dir", lambda arg, ctx: _getdir(ctx, *_map_args(arg, ctx)))


def write_file(ctx, obj, path, relativeTo=None, encoding=None, yaml=None):
    file = File(
        get_path(ctx, path, relativeTo),
        ctx.base_dir,
        ctx.templar and ctx.templar._loader,
        yaml or ctx.currentResource.root.attributeManager.yaml,
        encoding,
    )
    file.write(obj)
    return file.get_full_path()


def _get_instance_dir_name(instance):
    if instance.template:
        return instance.template.get_uri()
    else:
        return instance.name


def _get_base_dir(ctx, name=None):
    """
    Returns an absolute path based on the given folder name:

    :.:   directory that contains the current instance's ensemble
    :src: directory of the source file this expression appears in
    :artifacts: directory for the current instance (committed to repository).
    :local: The "local" directory for the current instance (excluded from repository)
    :secrets: The "secrets" directory for the current instance (files written there are vault encrypted)
    :tmp:   A temporary directory for the current instance (removed after unfurl exits)
    :tasks: Job specific directory for the current instance (excluded from repository).
    :operation: Operation specific directory for the current instance (excluded from repository).
    :workflow: Workflow specific directory for the current instance (excluded from repository).
    :spec.src: The directory of the source file the current instance's template appears in.
    :spec.home: Directory unique to the current instance's TOSCA template (committed to the spec repository).
    :spec.local: Local directory unique to the current instance's TOSCA template (excluded from repository).
    :project: The root directory of the current project.
    :unfurl.home: The location of home project (UNFURL_HOME).

    Otherwise look for a repository with the given name and return its path or None if not found.
    """

    instance = ctx.currentResource
    spec = instance.template and instance.template.spec
    if not name or name == ".":
        # the folder of the current resource's ensemble
        return instance.base_dir
    elif name == "src":
        # folder of the source file
        base_dir = getattr(ctx.kw, "base_dir", None)  # ctx.kw is the "eval:" dict
        if base_dir and os.path.isabs(base_dir):
            return base_dir
        if os.path.isabs(ctx.base_dir):
            return ctx.base_dir
        elif instance.template:  # XXX ctx.base_dir should be abs
            return os.path.join(instance.template.base_dir, ctx.base_dir or "")
        else:
            return ctx.base_dir
    elif name == "tmp":
        return os.path.join(instance.root.tmp_dir, _get_instance_dir_name(instance))
    elif name in Folders.Persistent:
        return os.path.join(instance.base_dir, name, _get_instance_dir_name(instance))
    elif name in Folders.Job:
        # these will always in "planned" while a task is running
        if ctx.task and instance is ctx.task.target:
            return WorkFolder._get_job_path(ctx.task, name, Folders.Planned)
        elif name == "tasks":  # in case we don't have a ctx.task
            return os.path.join(
                instance.base_dir,
                Folders.Planned,
                Folders.tasks,
                _get_instance_dir_name(instance),
            )
        else:
            assert (
                False
            ), f"cant get_path {name}, {ctx.task and ctx.task.target.name} is not {instance.name}"
    elif name == "project":
        return spec and spec._get_project_dir() or instance.base_dir
    elif name == "unfurl.home":
        return spec and spec._get_project_dir(True) or instance.base_dir
    else:
        template = instance.template
        assert template
        assert template.spec
        start, sep, rest = name.partition(".")
        if sep:
            if start == "spec":
                specHome = os.path.join(template.spec.base_dir, "spec", template.name)
                if rest == "src":
                    return template.base_dir
                if rest == "home":
                    return specHome
                elif rest == "local":
                    return os.path.join(specHome, "local")
            # XXX elif start == 'project' and rest == 'local'
        return template.spec.get_repository_path(name)
