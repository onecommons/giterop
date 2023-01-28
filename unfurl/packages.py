# Copyright (c) 2023 Adam Souzis
# SPDX-License-Identifier: MIT
"""
A repository definition can also reference a ``package`` which is a more abstract collection of artifacts or imports. The contents of a package share a semantic version and package references are resolved to a specific repository when an ensemble is loaded.
When the ``url`` field in a repository is set to an identifier that doesn't look like an absolute URL (e.g. doesn't include "://") or a relative file path (doesn't start with a ".") it is treated as a package.

Some examples of package ids:
```
unfurl.cloud/onecommons/unfurl-types
example.org
example.org/mypackage/v2
```

If the package references to a path in a git repository we follow Go's convention for including the path after ".git/" in the name. For example:

```
onecommons.org/unfurl-type.git/anotherpackage/v2
gitlab.com/onecommons/unfurl-types.git/v2
```

Package identifiers resolve to a git repository following the algorthims for [Go modules](https://go.dev/ref/mod). Repository declarations can include required version either by including a ``revision`` field or by including it as a URL fragment in the package identifier (e.g ``#v1.1.0``).

If multiple repository declarations refer to the same package and they specify versions those versions need to be compatible. If the version look like a semantic version the semantic versioning rules for compatibility will be applied otherwise the version specifiers need to be identical.

If no revision was set, the package will retrieve the revision that matches the latest git tag that looks like a semantic version tag (see https://go.dev/ref/mod#vcs-version for the algorithm). If none is found the latest revision from the repository's default branch will be used.

If the keys in a `repositories` section look like package identifiers 
that block is used as a rule override the location or version of a package 
or replace the package with another package.

```
environments:
  defaults:
    repositories:
        # set the repository URL and optionally the version for the given package
        unfurl.cloud/onecommons/blueprints/wordpress:
        url: https://unfurl.cloud/user/repo.git#main # choose an explicit repository and revision or replace with another package?
        revision: 1.2.15 # (optional) pin explicit revision for this package

      # replace a package with another
      unfurl.cloud/onecommons/unfurl-types:
        url: github.com/user1/myfork

      # A trailing * applies the rule to all packages that match
      unfurl.cloud/onecommons/*:
          url: https://staging.unfurl.cloud/onecommons/*
      
      # replace for a particular package, version combination
      unfurl.cloud/onecommons/blueprints/ghost#v1.6.0:
        url: github.com/user1/myforks.git/ghost
        revision: 1.6.1 # e.g. a security patch
```

You can also set these rules in ``UNFURL_PACKAGE_RULES`` environment variable where the key value pairs are separated by spaces. This example defines two rules:

```UNFURL_PACKAGE_RULES="unfurl.cloud/onecommons/* #main unfurl.cloud/onecommons/unfurl-types github.com/user1/myfork"```
"""
import re
from typing import Dict, List, NamedTuple, Optional, Union, cast
from typing_extensions import Literal
from urllib.parse import urlparse
from .repo import RepoView, is_url_or_git_path, split_git_url, get_remote_tags
from .logs import getLogger
from .util import UnfurlError
from toscaparser.utils.validateutils import TOSCAVersionProperty

logger = getLogger("unfurl")


class Package_Url_Info(NamedTuple):
    package_id: Optional[str]
    revision: Optional[str]
    url: Optional[str]


class PackageSpec:
    def __init__(self, package_spec: str, url: Optional[str], minimum_version: Optional[str]):
        # url can be package, and url prefix, url with a revision or branch, # 
        self.package_spec = package_spec
        if url:
            self.package_id, revision, self.url = get_package_id_from_url(url)
        else:
            self.url = None
            self.package_id = None
        self.revision = minimum_version or revision

    def __str__(self):
        return f"PackageSpec({self.package_spec}:{self.package_id} {self.revision} {self.url})"

    def matches(self, package: "Package") -> bool:
        # * use the package name (or prefix) as the name of the repository to specify replacement or name resolution
        candidate = package.package_id
        if self.package_spec.endswith("*"):
            return candidate.startswith(self.package_spec.rstrip("*"))
        elif "#" in self.package_spec:
            package_id, revision = self.package_spec.split("#")
            # match exact match with package and revision
            return candidate == package_id and revision == package.revision
        else:
            return candidate == self.package_spec

    def update(self, package: "Package") -> str:
        # if the package's package_id was replaced return that
        if self.package_spec.endswith("*"):
            if self.url:
                package.url = self.url.replace("*", package.package_id[len(self.package_spec) - 1:])
                return ""
            elif self.package_id:
                replaced_id = package.package_id
                package.package_id = self.package_id.replace("*", package.package_id[len(self.package_spec) - 1:])
                package.url = ""
                return replaced_id
            elif self.revision:
                # if (only) the revsion was set and the package didn't set one itself, set it
                if not package.revision:
                    package.revision = self.revision
                return ""
            else:
                # package_specs
                raise UnfurlError(f"Malformed package spec: {self.package_spec}: missing url or package id")

        if self.revision:
            package.revision = self.revision
        if self.url:
            package.url = self.url
        if self.package_id:
            replaced_id = package.package_id
            package.package_id = self.package_id
            return replaced_id
        return ""

    @staticmethod
    def update_package(package_specs: Dict[str, "PackageSpec"], package: "Package") -> bool:
        """_summary_

        Args:
            package_specs (PackageSpec): Rules to apply to the package.
            package (Package): Package will be updated in-place if there are rules that apply to it.

        Raises:
            UnfurlError: If applying the rules creates a circular reference.

        Returns:
            bool: True if the package was updated
        """
        old = []
        while True:
            for pkg_spec in package_specs.values():
                if pkg_spec.matches(package):
                    replaced_id = pkg_spec.update(package)
                    logger.trace("updated package %s using rule %s", package, pkg_spec)
                    if not replaced_id:
                        if not package.url:
                            # use default url pattern for the package_id
                            package.set_url_from_package_id()
                        return True  # we're done
                    if replaced_id in old:
                        raise UnfurlError(f"Circular reference in package rules: {replaced_id}")
                    old.append(replaced_id)
                    break  # package_id replaced start over
            else:
                # no matches found, we're done
                if not package.url:
                    # use default url pattern for the package_id
                    package.set_url_from_package_id()
                return bool(old)


def get_package_id_from_url(url: str) -> Package_Url_Info:
    if url.startswith(".") or url.startswith("file:"):
        # this isn't a package id or a git url
        return Package_Url_Info(None, None, url)

    # package_ids can have a revision in the fragment
    url, repopath, revision = split_git_url(url)
    parts = urlparse(url)
    path = parts.path.strip("/")
    if path.endswith(".git"):
        path = path[:len(path) - 4]
    if parts.hostname:
        package_id = parts.hostname + "/" + path
    else:
        package_id = path
    # follow Go's convention for including the path part of git url fragment in package_ids:
    if repopath:
        package_id += ".git/" + repopath

    # don't set url if url was just a package_id (so it didn't have a scheme)
    return Package_Url_Info(package_id, revision, url if parts.scheme else None)


def package_id_to_url(package_id: str, minimum_version: Optional[str] = ""):
    package_id, sep, revision = package_id.partition(".git/")
    repoloc, sep, repopath = package_id.partition(".git/")
    if repopath or revision:
        return f"https://{repoloc}.git#{minimum_version or revision or ''}:{repopath}"
    else:
        return f"https://{repoloc}.git"


class Package:
    def __init__(self, package_id: str, url: str, minimum_version: Optional[str]):
        self.package_id = package_id
        self.url = url
        self.revision = minimum_version
        self.repositories: List[RepoView] = []

    def __str__(self):
        return f"Package({self.package_id},v{self.revision} {self.url})"

    def version_tag_prefix(self) -> str:
        # see https://go.dev/ref/mod#vcs-version
        url, repopath, urlrevision = split_git_url(self.url)
        # return tag prefix to match version tags with
        if repopath:
            # strip out major version suffix
            return re.sub(r"(/v\d+)?$", '', repopath) + "/v"
        return 'v'

    def find_latest_version_from_repo(self) -> Optional[str]:
        prefix = self.version_tag_prefix()
        tags = [tag[len(prefix):] for tag in get_remote_tags(self.url, prefix + "*")
                if TOSCAVersionProperty.VERSION_RE.match(tag[len(prefix):])]
        if tags:
            return tags[0]
        return None

    def set_version_from_repo(self):
        self.revision = self.find_latest_version_from_repo()

    def set_url_from_package_id(self):
        self.url = package_id_to_url(self.package_id, self.revision_tag)

    @property
    def revision_tag(self) -> str:
        if not self.revision:
            return ""
        if not self.has_semver():
            return self.revision
        else:
            # since "^v" is in the semver regex, make sure don't end up with "vv"
            return self.version_tag_prefix() + self.revision.lstrip("v")

    def add_reference(self, repoview: RepoView) -> bool:
        if repoview not in self.repositories:
            self.repositories.append(repoview)
            repoview.package = self
            # we need to set the path, url, and revision to match the package
            url, repopath, urlrevision = split_git_url(self.url)
            repoview.path = repopath
            repoview.revision = self.revision
            repoview.repository.url = f"{url}#{self.revision_tag}:{repopath}"
            return True
        return False

    def has_semver(self):
        return self.revision and TOSCAVersionProperty.VERSION_RE.match(self.revision) is not None

    def is_compatible_with(self, package: "Package") -> bool:
        """
        If both the current package and the given package has a semantic version,
        return true if the current packages' major version is equal and minor version is less than or equal to the given package.
        If either package doesn't specify a version, return true.
        Otherwise only return true if the packages revisions match exactly.
        """
        if not self.revision or not package.revision:
            return True
        if not self.has_semver():
            return self.revision == package.revision
        if not package.has_semver():  # doesn't have a semver and doesn't match
            return False
        
        # # if given revision is newer than current packages we need to reload (error for now?)
        return TOSCAVersionProperty(package.revision).is_semver_compatible_with(
                                    TOSCAVersionProperty(self.revision))


PackagesType = Dict[str, Union[Literal[False], Package]]


def resolve_package(repoview: RepoView, packages: PackagesType, package_specs: Dict[str, PackageSpec]) -> Optional["Package"]:
    """
    If repository references a package, register it with existing package or create a new one.
    A error is raised if a package's version conficts with the repository's version requirement.
    """
    package_id, revision, url = get_package_id_from_url(repoview.url)
    if not package_id:
        repoview.package = False
        return None

    # if repository.revision is set it overrides the revision in the url fragment
    minimum_version = repoview.repository.revision or revision
    package = Package(package_id, url or "", minimum_version)
    # possibly change the package info if we match a PackageSpec
    PackageSpec.update_package(package_specs, package)
    if package.package_id not in packages:
        if not package.url:
            # the repository didn't specify a full url and there wasn't already an existing package or package spec
            raise UnfurlError(f'Could not find a repository that matched package "{package.package_id}"')
        if not package.revision:
            # no version specified, use the latest version tagged in the repository
            package.set_version_from_repo()
        if not package.revision:
            # no version tags, repository can't be used as a package
            repoview.package = False
            packages[package.package_id] = False
            return None
        packages[package_id] = package
    else:
        existing = packages[package.package_id]
        if not existing:  # the repository isn't a package
            return None
        # we don't want different implementations of the same package so use the one
        # we already have. But we need to check if it compatible with the version requested here.
        if existing.repositories and not package.is_compatible_with(existing):
            # XXX if we need a later version, update the existing package and reload any content from it
            # not yet implemented so just throw an error
            raise UnfurlError(f"{package.package_id} has version {package.revision} but {existing.revision} is already in use.")
        package = existing

    package.add_reference(repoview)
    return package
