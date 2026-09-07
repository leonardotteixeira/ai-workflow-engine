"""REST API — Fase 10.

Depends on `workflow_engine.engine` and `workflow_engine.persistence`, never
the other way around, and never imported by either — this is the outermost
layer. `create_app()` in `app.py` is the only entry point that matters from
outside this package.
"""

from workflow_engine.api.app import create_app

__all__ = ["create_app"]
