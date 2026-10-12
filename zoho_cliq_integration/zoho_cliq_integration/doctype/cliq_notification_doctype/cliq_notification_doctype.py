# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CliqNotificationDoctype(Document):
	def validate_row(self, seen: set):
		self.validate_not_duplicate(seen)
		self.warn_if_no_active_workflow()

	def validate_not_duplicate(self, seen: set):
		document_type = (self.document_type or "").strip()
		if not document_type:
			return

		if document_type in seen:
			frappe.throw(
				_("Row {0}: {1} is already in the list.").format(
					self.idx, frappe.bold(document_type)
				),
				title=_("Duplicate Doctype"),
			)

		seen.add(document_type)

	def warn_if_no_active_workflow(self):
		if not self.enabled or not self.document_type:
			return

		if frappe.db.exists(
			"Workflow", {"document_type": self.document_type, "is_active": 1}
		):
			return

		frappe.msgprint(
			_("{0} has no active Workflow, so no Cliq message can be sent for it.").format(
				frappe.bold(self.document_type)
			),
			title=_("No Active Workflow"),
			indicator="orange",
		)
