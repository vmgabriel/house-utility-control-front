"""Bounded context for tasks, goals, and daily plans.

Separate from `budget` because the capability is different: the backlog is
about *what to do*, not about money moving. It is separate from `rentals`
because it shares no vocabulary with property management.

The DRF backend exposes the same split (`apps.tasks`), so the two sides map
onto each other one-for-one.
"""
