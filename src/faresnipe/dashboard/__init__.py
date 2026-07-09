"""Dashboard web para faresnipe (stdlib, sin frameworks).

El dashboard sólo dispara escaneos manuales desde la UI. El watch continuo lo
manejan el CLI (``faresnipe --watch``) o systemd, no la UI.
"""

from .server import serve_dashboard
from .state import DashboardServer

__all__ = ["DashboardServer", "serve_dashboard"]
