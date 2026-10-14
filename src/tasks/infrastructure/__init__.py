"""Infrastructure layer for the tasks context.

Adapters that speak the DRF tasks API: the httpx client and the mappers that
turn its JSON into domain entities. Nothing here is imported by the domain or
application layers (AGENTS.md, constraint 2).
"""
