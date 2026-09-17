"""IronCAD MCP server package.

A local (stdio) MCP server that wraps IronCAD 2024's COM API via the
``python-ironcad`` package, so Claude can inspect and build models in the user's
own IronCAD session using their catalog, referring to parts by name.

Windows-only. Requires IronCAD 2024 running and its API COM components
registered (one-time elevated ``python -m python_ironcad``). See README.md.
"""

__version__ = "0.1.0"
