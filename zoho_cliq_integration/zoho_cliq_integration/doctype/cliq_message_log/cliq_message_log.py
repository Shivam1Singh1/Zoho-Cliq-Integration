# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""One row per Cliq message, successful or not.

This is both the audit trail and the duplicate guard: a message already
Queued, Sent, Dry Run or Undelivered for the same rule, document, state and
target is never raised twice. The `Undelivered` status exists because Zoho
answers 200 even when it resolved nobody, so a green HTTP code alone would
hide every address mismatch in the company.
"""

import frappe
from frappe.model.document import Document


class CliqMessageLog(Document):
	pass


def clear_old_logs(days: int = 90):
	"""Scheduled trim. Successful and dry-run rows age out; failures are kept."""
	frappe.db.delete(
		"Cliq Message Log",
		{
			"status": ("in", ("Sent", "Dry Run", "Skipped")),
			"creation": ("<", frappe.utils.add_days(frappe.utils.nowdate(), -days)),
		},
	)
