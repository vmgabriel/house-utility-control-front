"""Rentals application layer: use cases, ports, and JSON mappers.

Pure Python by contract (AGENTS.md): nothing in this package may import Flask,
httpx, Jinja2, or pydantic. The mappers in particular take already-parsed JSON
(``dict`` / ``list[dict]``), never a transport response object.
"""
