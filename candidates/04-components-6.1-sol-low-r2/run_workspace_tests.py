"""Run discovery with temporary files confined to this workspace."""
import os
import tempfile
import unittest

tempfile.tempdir = os.path.dirname(os.path.abspath(__file__))
# Python 3.14's Windows mode 0700 ACL excludes this sandbox's restricted
# token. Inherit workspace permissions for test temporary directories.
if os.name == "nt":
    original_mkdir = os.mkdir

    def workspace_mkdir(path, mode=0o777, *, dir_fd=None):
        return original_mkdir(path, 0o777, dir_fd=dir_fd)

    os.mkdir = workspace_mkdir
unittest.main(module=None, argv=["unittest", "discover", "-s", "tests", "-v"])
