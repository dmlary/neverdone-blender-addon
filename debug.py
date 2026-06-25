"""
Debug helpers for this addon.

Uses rich.print() / rich.inspect() if the `rich` package happens to be
installed in Blender's Python. Otherwise falls back to plain-Python
implementations defined below.
"""

import builtins
import pprint


def _print(*args, **_ignored):
    parts = [a if isinstance(a, str) else pprint.pformat(a) for a in args]
    builtins.print(*parts)


def _inspect(obj, **_ignored):
    if hasattr(obj, "bl_rna"):
        data = {
            p.identifier: getattr(obj, p.identifier, "<error>")
            for p in obj.bl_rna.properties
        }
    else:
        data = getattr(obj, "__dict__", repr(obj))
    builtins.print(pprint.pformat(data))


try:
    from rich import print, inspect
except ImportError:
    print, inspect = _print, _inspect
