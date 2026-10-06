"""Framework-agnostic auth primitives shared by every bounded context.

Cookie management and CSRF token issuing are cross-cutting: every blueprint in
every context needs them, and none of them own the rules. These are pure Python
and import no web framework -- the caller passes the request and response in.
"""
