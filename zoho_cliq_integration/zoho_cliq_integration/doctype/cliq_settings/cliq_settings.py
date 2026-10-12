# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""The single place this app is configured.

Holds the bot credentials, whether messages go out as direct messages or as a
channel post, and how an ERPNext user maps to a Cliq login. Two switches keep
it safe: `enabled` stops everything, and `dry_run` renders and logs messages
without calling Zoho. Validation here guards the one dangerous transition --
going live with an incomplete bot configuration.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class CliqSettings(Document):
	def validate(self):
		self.api_domain = (self.api_domain or "").strip().rstrip("/")

		# A domain pasted with the scheme still works everywhere else in Frappe,
		# so strip it rather than rejecting the save.
		for scheme in ("https://", "http://"):
			if self.api_domain.startswith(scheme):
				self.api_domain = self.api_domain[len(scheme) :]

		self.bot_unique_name = (self.bot_unique_name or "").strip()

		if self.timeout and self.timeout < 1:
			frappe.throw(_("Request Timeout must be at least 1 second."))

		if self.max_attempts and self.max_attempts < 1:
			frappe.throw(_("Max Attempts must be at least 1."))

		# Going live is the one transition worth guarding: a half-filled bot
		# would otherwise fail silently, one Error Log row per notification.
		if self.enabled and not self.dry_run:
			missing = [
				label
				for label, value in (
					(_("Bot Unique Name"), self.bot_unique_name),
					(_("API Domain"), self.api_domain),
					(_("Zapikey"), self.get_password("zapikey", raise_exception=False)),
				)
				if not value
			]
			if missing:
				frappe.throw(
					_("Cannot leave Dry Run with these fields empty: {0}").format(
						", ".join(missing)
					)
				)


def get_settings():
	"""Cached settings. Cleared by Cliq Settings.on_update via the Single hook."""
	return frappe.get_cached_doc("Cliq Settings")
