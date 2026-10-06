"""HTTP adapters shared by every bounded context.

The DRF client is the outer boundary: it is the only place ``httpx`` exceptions
are caught and translated, so nothing above it ever sees a socket error.
``auto_refresh`` wraps repositories of *any* context, which is why it lives
here rather than in one of them.
"""
