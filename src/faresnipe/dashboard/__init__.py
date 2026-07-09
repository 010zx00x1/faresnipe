"""Web dashboard for faresnipe (stdlib, no frameworks).

The dashboard only triggers manual scans from the UI; continuous watch is
handled by the CLI (``faresnipe --watch``) or systemd, not the UI.
"""

from .server import serve_dashboard
from .state import DashboardServer

__all__ = ["DashboardServer", "serve_dashboard"]
