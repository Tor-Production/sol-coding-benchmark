"""Run the unchanged test suite using workspace-local temporary files."""
import os
from pathlib import Path
import tempfile
import unittest

if __name__ == "__main__":
    workspace = str(Path(__file__).resolve().parent)
    os.environ["TMP"] = workspace
    os.environ["TEMP"] = workspace
    tempfile.tempdir = workspace
    # Python 3.14's Windows mode=0o700 creates an ACL that excludes this
    # sandbox's restricted token. Inherit the workspace ACL for test dirs.
    original_mkdir = os.mkdir

    def workspace_mkdir(path, mode=0o777, *, dir_fd=None):
        if mode == 0o700 and Path(path).resolve().is_relative_to(workspace):
            mode = 0o777
        return original_mkdir(path, mode, dir_fd=dir_fd)

    os.mkdir = workspace_mkdir
    unittest.main(module=None, argv=["unittest", "discover", "-s", "tests", "-v"])
