# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import get_url_to_form

from zoho_cliq_integration.cliq.client import queue_message

STATE_FIELD = "workflow_state"

EMAIL_FIELDS = ("user_id", "prefered_email", "company_email")


def on_update(doc, method=None):
	try:
		if not doc.has_value_changed(STATE_FIELD):
			return

		before = doc.get_doc_before_save()
		if not before:
			return

		old_state = (before.get(STATE_FIELD) or "").strip()
		new_state = (doc.get(STATE_FIELD) or "").strip()
		if old_state == new_state:
			return

		settings = frappe.get_cached_doc("Cliq Settings")
		if not settings.enabled or not settings.notify_leave_state_change:
			return

		frappe.enqueue(
			"zoho_cliq_integration.events.leave_application.send_state_change_message",
			queue="short",
			enqueue_after_commit=True,
			job_id=f"cliq-leave::{doc.name}::{old_state}->{new_state}",
			deduplicate=True,
			leave_application=doc.name,
			old_state=old_state,
			new_state=new_state,
		)
	except Exception:
		frappe.log_error(
			title=f"Cliq: leave state hook failed for {doc.name}"[:140],
			message=frappe.get_traceback(),
		)


def send_state_change_message(leave_application: str, old_state: str, new_state: str):
	try:
		settings = frappe.get_cached_doc("Cliq Settings")
		if not settings.enabled or not settings.notify_leave_state_change:
			return

		identifier = (settings.leave_state_recipient_employee or "").strip()
		if not identifier:
			frappe.log_error(
				title="Cliq: no leave alert recipient configured",
				message=(
					"Leave state alerts are on but no Recipient Employee is set "
					"in Cliq Settings, so there is nobody to notify."
				),
			)
			return

		email = resolve_employee_email(identifier)
		if not email:
			frappe.log_error(
				title="Cliq: leave alert recipient unresolved",
				message=(
					f"No email found for Employee '{identifier}': none of "
					f"{', '.join(EMAIL_FIELDS)} is set. "
					f"Leave Application {leave_application} "
					f"({old_state} -> {new_state}) was not announced."
				),
			)
			return

		doc = frappe.get_doc("Leave Application", leave_application)
		log_names = queue_message(
			recipient_user=email,
			subject=f"Leave {new_state}: {doc.get('employee_name') or doc.employee}",
			message=build_message(doc, old_state, new_state),
			reference_doctype=doc.doctype,
			reference_name=doc.name,
			trigger_state=f"{old_state} -> {new_state}",
		)
		if not log_names:
			frappe.logger("zoho_cliq").info(
				f"Cliq: nothing queued for {leave_application} "
				f"({old_state} -> {new_state}), recipient {email}"
			)
	except Exception:
		frappe.log_error(
			title=f"Cliq: leave alert failed for {leave_application}"[:140],
			message=frappe.get_traceback(),
		)


def resolve_employee_email(identifier: str) -> str | None:
	row = None
	if frappe.db.exists("Employee", identifier):
		row = frappe.db.get_value("Employee", identifier, EMAIL_FIELDS, as_dict=True)
	else:
		matches = frappe.get_all(
			"Employee",
			filters={"employee_name": identifier},
			order_by="FIELD(status, 'Active') desc, modified desc",
			fields=list(EMAIL_FIELDS),
			limit=1,
		)
		row = matches[0] if matches else None

	if not row:
		return None

	for field in EMAIL_FIELDS:
		value = (row.get(field) or "").strip()
		if value:
			return value.lower()
	return None


def build_message(doc, old_state: str, new_state: str) -> str:
	applicant = doc.get("employee_name") or doc.employee
	lines = [
		f"{old_state} -> {new_state}",
		"",
		f"Applicant: {applicant} ({doc.employee})",
		f"Leave Type: {doc.get('leave_type') or '-'}",
		f"Dates: {frappe.format(doc.from_date, 'Date')} to {frappe.format(doc.to_date, 'Date')}",
		f"Total Days: {doc.get('total_leave_days') or 0}",
	]

	try:
		url = get_url_to_form(doc.doctype, doc.name)
		lines += ["", f"[{doc.name}]({url})"]
	except Exception:
		lines += ["", doc.name]

	return "\n".join(lines)
