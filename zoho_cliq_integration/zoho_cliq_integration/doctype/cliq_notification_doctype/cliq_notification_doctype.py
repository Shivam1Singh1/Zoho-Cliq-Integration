# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""One row of Cliq Notification Settings.doctypes_list.

Frappe does not run `validate` on child rows -- only the parent document's
validate is called -- so the parent invokes `validate_row` once per row. The
checks still live here, with the doctype they belong to, rather than being
hoisted into the parent controller or a shared utils module.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class CliqNotificationDoctype(Document):
	def validate_row(self, seen: set):
		"""`seen` carries document_types already checked in this save."""
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
		"""Messages follow the workflow, so a doctype without one sends nothing.

		A warning rather than an error: a workflow may legitimately be added
		after the row, and refusing the save would be the wrong way round.
		"""
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
