# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CliqMessageLog(Document):
	pass


def clear_old_logs(days: int = 90):
	frappe.db.delete(
		"Cliq Message Log",
		{
			"status": ("in", ("Sent", "Dry Run", "Skipped")),
			"creation": ("<", frappe.utils.add_days(frappe.utils.nowdate(), -days)),
		},
	)
