# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import json
import re

import frappe

from zoho_cliq_integration.zoho_cliq_integration.doctype.cliq_notification_settings.cliq_notification_settings import (
	get_enabled_doctypes,
)

STATE_FIELD = "workflow_state"

_CONDITION_FIELD = re.compile(r"doc\.(\w+)\s*==\s*frappe\.session\.user")

MAX_ROLE_RECIPIENTS = 10


def on_update(doc, method=None):
	try:
		if doc.doctype not in get_enabled_doctypes():
			return
		if not doc.has_value_changed(STATE_FIELD):
			return

		new_state = (doc.get(STATE_FIELD) or "").strip()
		if not new_state:
			return

		# A creation has no `before`, and that is the whole difference between
		# the two paths below. It is not a transition: there is no previous
		# state, and nobody handed the document on.
		before = doc.get_doc_before_save()
		old_state = (before.get(STATE_FIELD) or "").strip() if before else ""
		if before and old_state == new_state:
			return

		workflow = get_active_workflow(doc.doctype)
		if not workflow:
			return

		actor = frappe.session.user
		if before:
			recipients = collect_recipients(
				doc, before, workflow, old_state, new_state, actor
			)
		else:
			recipients = get_creation_recipients(doc, workflow, new_state, actor)
		if not recipients:
			return

		frappe.enqueue(
			"zoho_cliq_integration.cliq_sender.send_workflow_message",
			queue="short",
			enqueue_after_commit=True,
			job_id=f"cliq-wf::{doc.doctype}::{doc.name}::{old_state}->{new_state}",
			deduplicate=True,
			reference_doctype=doc.doctype,
			reference_name=doc.name,
			old_state=old_state,
			new_state=new_state,
			actor=actor,
			emails=recipients,
			applicant=doc.get("employee_name"),
		)
	except Exception:
		frappe.log_error(
			title=f"Cliq: workflow notify failed for {doc.doctype} {doc.name}"[:140],
			message=frappe.get_traceback(),
		)


def get_active_workflow(doctype: str):
	name = frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}, "name")
	return frappe.get_cached_doc("Workflow", name) if name else None


def collect_recipients(doc, before, workflow, old_state, new_state, actor) -> list[str]:
	users = get_forward_recipients(doc, workflow, new_state)

	reply_to = get_reply_recipient(doc, before, old_state)
	if reply_to:
		users.append(reply_to)

	return to_emails(users, actor)


def get_creation_recipients(doc, workflow, new_state, actor) -> list[str]:
	outgoing = [t for t in workflow.transitions if (t.state or "").strip() == new_state]
	if not outgoing:
		return []

	named = [u for u in (get_condition_user(doc, t.condition) for t in outgoing) if u]

	# The role fan-out is a fallback, not an addition. A state this document
	# was born into is usually reachable both by the one approver a condition
	# names and by a role that may hold a hundred people -- announcing a new
	# document to all of them is noise, and the named approver is the person
	# who actually has to act. Roles are used only when no transition out of
	# this state narrows it to anybody.
	if not named:
		for transition in outgoing:
			named.extend(get_role_users(transition.allowed, doc, new_state))

	return to_emails(named, actor)


def to_emails(users, actor) -> list[str]:
	actor_email = get_user_email(actor)

	emails, seen = [], set()
	for user in users:
		if not user or user == actor:
			continue
		email = get_user_email(user)
		if not email or email == actor_email or email in seen:
			continue
		seen.add(email)
		emails.append(email)
	return emails


def get_forward_recipients(doc, workflow, new_state) -> list[str]:
	outgoing = [t for t in workflow.transitions if (t.state or "").strip() == new_state]
	if not outgoing:
		return []

	users = []
	for transition in outgoing:
		named = get_condition_user(doc, transition.condition)
		if named:
			users.append(named)
			continue
		users.extend(get_role_users(transition.allowed, doc, new_state))
	return users


def get_condition_user(doc, condition) -> str | None:
	if not condition:
		return None

	match = _CONDITION_FIELD.search(condition)
	if not match:
		return None

	value = (doc.get(match.group(1)) or "").strip()
	return value or None


def get_role_users(role, doc, new_state) -> list[str]:
	if not role:
		return []

	holders = frappe.get_all(
		"Has Role",
		filters={"role": role, "parenttype": "User"},
		pluck="parent",
	)
	holders = [u for u in holders if u not in ("Administrator", "Guest")]

	enabled = (
		frappe.get_all(
			"User",
			filters={"name": ("in", holders), "enabled": 1},
			pluck="name",
		)
		if holders
		else []
	)

	if len(enabled) > MAX_ROLE_RECIPIENTS:
		frappe.log_error(
			title=f"Cliq: role fan-out capped for {role}"[:140],
			message=(
				f"{doc.doctype} {doc.name} entered '{new_state}', whose transition is "
				f"open to the role '{role}'. {len(enabled)} enabled users hold it, "
				f"above the cap of {MAX_ROLE_RECIPIENTS}, so only the first "
				f"{MAX_ROLE_RECIPIENTS} were notified. The transition carries no "
				f"condition naming a single approver, which is what would normally "
				f"narrow this to one person."
			),
		)
		enabled = sorted(enabled)[:MAX_ROLE_RECIPIENTS]

	return enabled


def get_reply_recipient(doc, before, old_state) -> str | None:
	return (
		get_actor_from_versions(doc, old_state)
		or get_actor_from_workflow_actions(doc)
		or before.modified_by
	)


def get_actor_from_versions(doc, old_state) -> str | None:
	rows = frappe.get_all(
		"Version",
		filters={"ref_doctype": doc.doctype, "docname": doc.name},
		fields=["owner", "data"],
		order_by="creation desc",
		limit=20,
	)
	for row in rows:
		for field, _before, after in changed_fields(row.get("data")):
			if field == STATE_FIELD and (after or "").strip() == old_state:
				return row.get("owner")
	return None


def changed_fields(data):
	try:
		for entry in json.loads(data or "{}").get("changed") or []:
			if len(entry) == 3:
				yield entry[0], entry[1], entry[2]
	except Exception:
		return


def get_actor_from_workflow_actions(doc) -> str | None:
	rows = frappe.get_all(
		"Workflow Action",
		filters={
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"status": "Completed",
		},
		fields=["completed_by"],
		order_by="creation desc",
		limit=10,
	)
	for row in rows:
		completed_by = row.get("completed_by")
		if completed_by and completed_by != frappe.session.user:
			return completed_by
	return None


def get_user_email(user: str) -> str | None:
	if not user or user in ("Administrator", "Guest"):
		return None
	email = (frappe.db.get_value("User", user, "email") or user or "").strip()
	return email.lower() if "@" in email else None
