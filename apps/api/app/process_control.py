"""Terminate only the subprocess tree created for a private audio task."""

import subprocess
import sys


def terminate_tree(process):
    if process.poll() is None:
        if sys.platform == "win32":
            # A virtualenv executable may be a launcher with a real Python child.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            process.kill()
    process.wait(timeout=10)
