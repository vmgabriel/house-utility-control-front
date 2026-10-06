"""Rentals infrastructure: DRF adapter implementations.

The only module here that imports httpx is the shared `DRFAPIClient`; this
package's adapter depends on that client rather than constructing its own.
"""
