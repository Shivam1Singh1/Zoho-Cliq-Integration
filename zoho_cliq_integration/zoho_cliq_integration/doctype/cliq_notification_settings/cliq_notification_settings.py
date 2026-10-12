# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Which doctypes send a Cliq message when their workflow state changes.

`get_enabled_doctypes` is read on every document save on the site, because the
event handler is registered against `doc_events["*"]`. It must therefore cost
one cache read and nothing else -- never a query. `on_update` drops the cache,
which is what makes an unchecked box or a deleted row take effect on the next
save rather than on the next restart.
"""

import frappe
from frappe.model.document import Document

CACHE_KEY = "cliq_notification_enabled_doctypes"

PARENT = "Cliq Notification Settings"


class CliqNotificationSettings(Document):
	def validate(self):
		# Child controllers are not called by Frappe; see the child's docstring.
		seen = set()
		for row in self.doctypes_list or []:
			row.validate_row(seen)

	def on_update(self):
		clear_cache()


def clear_cache():
	frappe.cache().delete_value(CACHE_KEY)


def get_enabled_doctypes() -> list[str]:
	"""Doctypes with enabled = 1, cached.

	Deliberately not whitelisted: this is called from the document hook, never
	from the client, and exposing it would hand any logged-in user a list of
	which doctypes are wired to an outbound integration.
	"""

	def build():
		rows = frappe.get_all(
			"Cliq Notification Doctype",
			filters={"parenttype": PARENT, "parentfield": "doctypes_list", "enabled": 1},
			pluck="document_type",
		)
		return sorted({(row or "").strip() for row in rows if (row or "").strip()})

	return frappe.cache().get_value(CACHE_KEY, build) or []
