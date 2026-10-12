"""Zoho Cliq Integration.

Delivers ERPNext document alerts to Zoho Cliq, as a direct message to the
person who has to act or as a post into a channel. Rather than adding a
parallel rule engine, it registers "Cliq" as a new channel on Frappe's own
Notification doctype, so triggers, conditions, recipients and Jinja templates
are configured exactly where the rest of the site's alerts already are --
adding a doctype needs a new Notification record, not code.
"""

__version__ = "0.0.1"
