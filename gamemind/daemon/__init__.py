"""GameMind daemon — FastAPI app on 127.0.0.1:8766.

Per autoplan Amendment A3, the daemon binds ONLY to 127.0.0.1, requires
a bearer token on authenticated endpoints, and rejects requests with an
`Origin` header (browser CORS attack prevention). See `main.py`.
"""

from __future__ import annotations
