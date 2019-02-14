"""
Basic operation of GitErOp is to apply the specified configuration to a resource
and record the results of the application.

A manifest can contain a reproducible history of changes to a resource.
This history is stored in the resource definition so it doesn't
rely on git history for this. In fact a git repository isn't required at all.
But the intent is for commits in a git repo to correspond to reproducible configuration states of the system.
The git repo is also used to record or archive exact versions of each configurators applied.
"""

from pkg_resources import get_distribution
__version__ = get_distribution(__name__).version
