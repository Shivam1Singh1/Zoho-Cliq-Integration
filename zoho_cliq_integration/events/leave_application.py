# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Leave Application workflow-state alerts.

One fixed person -- whoever is named in Cliq Settings -- gets a direct message
every time a Leave Application moves from one workflow state to another.

This is deliberately a `doc_events` hook rather than a Notification rule. The
Notification route (see overrides/notification.py) resolves recipients from the
document itself, which is exactly wrong here: the recipient is constant and has
nothing to do with who applied or who approves. A rule would need a hardcoded
address in its recipient list; a hook can read one from settings.

Two things this must never do, because both would surface to the applicant as a
failed leave request:

  * raise -- every path is wrapped, failures go to the Error Log
  * block -- the HTTP call happens in a worker, two hops away from the save

The first hop (`frappe.enqueue` below) exists so that even the Employee lookup
and the log row insert are off the save's critical path. The second hop is
inside `queue_message`, which enqueues the actual delivery.
"""

import frappe
from frappe.utils import get_url_to_form

from zoho_cliq_integration.cliq.client import queue_message

STATE_FIELD = "workflow_state"

# In preference order. `user_id` is the ERPNext login and so the address most
# likely to match a Cliq account; the other two are what sites fall back to
# when a person has no ERPNext user at all.
EMAIL_FIELDS = ("user_id", "prefered_email", "company_email")


# frappe.log_error is called by keyword everywhere below, on purpose. Its
# positional form is (title, message) with a heuristic that swaps the two if
# the first argument contains a newline -- so a traceback lands correctly by
# accident, while a long single-line message does not: it stays in `title`,
# which is Data(140), and the insert dies with CharacterLengthExceededError.
# That turns a diagnostic into a second, less useful error.


def on_update(doc, method=None):
	"""Fire only on a real workflow-state transition."""
	try:
		if not doc.has_value_changed(STATE_FIELD):
			return

		before = doc.get_doc_before_save()
		if not before:
			# An insert. There is no transition yet, whatever the state is.
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
			# Nothing is queued for a save that later rolls back.
			enqueue_after_commit=True,
			# frappe.enqueue takes deduplicate=False by default, so a job_id on
			# its own does not stop a second send. One transition saves the
			# document more than once, which produced two identical messages
			# in testing until this was added.
			job_id=f"cliq-leave::{doc.name}::{old_state}->{new_state}",
			deduplicate=True,
			leave_application=doc.name,
			old_state=old_state,
			new_state=new_state,
		)
	except Exception:
		# The applicant's save is never the place to report our own failure.
		frappe.log_error(
			title=f"Cliq: leave state hook failed for {doc.name}"[:140],
			message=frappe.get_traceback(),
		)


def send_state_change_message(leave_application: str, old_state: str, new_state: str):
	"""Worker: resolve the recipient and hand the message to the Cliq client."""
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
		log_name = queue_message(
			recipient_user=email,
			subject=f"Leave {new_state}: {doc.get('employee_name') or doc.employee}",
			message=build_message(doc, old_state, new_state),
			reference_doctype=doc.doctype,
			reference_name=doc.name,
			trigger_state=f"{old_state} -> {new_state}",
		)
		if not log_name:
			# queue_message declined: disabled mid-flight, unresolvable
			# identity, or this exact transition was already announced.
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
	"""Find an Employee by ID or by name, and return their best address.

	The settings field is a Link, so in practice the identifier is an Employee
	ID. A name is accepted too, because an ID copied from a report or typed by
	hand is easy to get wrong in a way a name is not.
	"""
	row = None
	if frappe.db.exists("Employee", identifier):
		row = frappe.db.get_value("Employee", identifier, EMAIL_FIELDS, as_dict=True)
	else:
		matches = frappe.get_all(
			"Employee",
			filters={"employee_name": identifier},
			# An inactive namesake should never win over the current employee.
			order_by="FIELD(status, 'Active') desc, modified desc",
			fields=list(EMAIL_FIELDS),
			limit=1,
		)
		row = matches[0] if matches else None

	if not row:
		return None

	for field in EMAIL_FIELDS:
		# Employee rows on this site carry stray whitespace often enough that
		# an unstripped address is a real failure mode, not a hypothetical one.
		value = (row.get(field) or "").strip()
		if value:
			return value.lower()
	return None


def build_message(doc, old_state: str, new_state: str) -> str:
	"""Plain text with a Markdown link, which is what Cliq renders."""
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
