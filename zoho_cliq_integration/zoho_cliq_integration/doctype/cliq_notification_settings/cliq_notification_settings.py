# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

CACHE_KEY = "cliq_notification_enabled_doctypes"

PARENT = "Cliq Notification Settings"


class CliqNotificationSettings(Document):
	def validate(self):
		seen = set()
		for row in self.doctypes_list or []:
			row.validate_row(seen)

	def on_update(self):
		clear_cache()


def clear_cache():
	frappe.cache().delete_value(CACHE_KEY)


def get_enabled_doctypes() -> list[str]:

	def build():
		rows = frappe.get_all(
			"Cliq Notification Doctype",
			filters={"parenttype": PARENT, "parentfield": "doctypes_list", "enabled": 1},
			pluck="document_type",
		)
		return sorted({(row or "").strip() for row in rows if (row or "").strip()})

	return frappe.cache().get_value(CACHE_KEY, build) or []
