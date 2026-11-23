"""Prints pythonw.exe next to this Python (the server without a console window), else this Python (run.bat)."""
import os
import sys

p = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
print(p if os.path.isfile(p) else sys.executable)
