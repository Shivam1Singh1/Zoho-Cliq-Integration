# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CliqSettings(Document):
	def validate(self):
		self.api_domain = (self.api_domain or "").strip().rstrip("/")

		for scheme in ("https://", "http://"):
			if self.api_domain.startswith(scheme):
				self.api_domain = self.api_domain[len(scheme) :]

		self.bot_unique_name = (self.bot_unique_name or "").strip()

		if self.timeout and self.timeout < 1:
			frappe.throw(_("Request Timeout must be at least 1 second."))

		if self.max_attempts and self.max_attempts < 1:
			frappe.throw(_("Max Attempts must be at least 1."))

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
	return frappe.get_cached_doc("Cliq Settings")
