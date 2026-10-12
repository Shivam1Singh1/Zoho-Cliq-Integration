"""Outbound Cliq transport.

Everything that knows how to talk to Zoho lives here, deliberately isolated
from the Notification override so the two can change independently. The HTTP
contract itself is confined to a single function in client.py.
"""
