# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Turn a workflow state change into a list of people to tell.

Registered against `doc_events["*"]`, so this runs on every document save on
the site. The first two lines of `on_update` are therefore the hot path: one
cache read for the enabled list, then one in-memory field comparison. Nothing
else happens for a doctype that is not configured.

Who gets told comes entirely from the doctype's own active Workflow, so one
rule serves every doctype:

    forward -- whoever must act on the state the document just entered. Read
               off the transitions leaving that state. A state with no
               outgoing transitions is final, and nobody is waiting.

    reply   -- whoever made the previous transition, so the person who handed
               the document on learns what became of it.

With three levels: 1->2 tells L2; 2->3 tells L3 and replies to L1; the final
approval replies to L2. The person who just acted is never told, since they
already know.
"""

import json
import re

import frappe

from zoho_cliq_integration.zoho_cliq_integration.doctype.cliq_notification_settings.cliq_notification_settings import (
	get_enabled_doctypes,
)

STATE_FIELD = "workflow_state"

# A transition whose condition names a document field -- the common
# `doc.leave_approver == frappe.session.user` shape -- identifies one specific
# person who must act. Notifying the whole role in that case would be wrong as
# well as loud: on this site 125 users hold Leave Approver and exactly one of
# them can move the document.
_CONDITION_FIELD = re.compile(r"doc\.(\w+)\s*==\s*frappe\.session\.user")

# Backstop for roles with no such condition. Exceeding it is logged, never
# silently truncated -- a capped fan-out that looked complete would be worse
# than a loud one.
MAX_ROLE_RECIPIENTS = 10


def on_update(doc, method=None):
	try:
		if doc.doctype not in get_enabled_doctypes():
			return
		if not doc.has_value_changed(STATE_FIELD):
			return

		before = doc.get_doc_before_save()
		if not before:
			# An insert. Whatever the state is, it was not transitioned into.
			return

		old_state = (before.get(STATE_FIELD) or "").strip()
		new_state = (doc.get(STATE_FIELD) or "").strip()
		if not new_state or old_state == new_state:
			return

		workflow = get_active_workflow(doc.doctype)
		if not workflow:
			return

		actor = frappe.session.user
		recipients = collect_recipients(doc, before, workflow, old_state, new_state, actor)
		if not recipients:
			return

		frappe.enqueue(
			"zoho_cliq_integration.cliq_sender.send_workflow_message",
			queue="short",
			enqueue_after_commit=True,
			# One transition saves the document more than once -- seven to eight
			# hook invocations were observed for a single approval, two of them
			# for the document itself -- so without this the same message is
			# sent twice. A job_id alone does NOT dedupe: frappe.enqueue takes
			# deduplicate=False by default.
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
		# A document save must never fail because of an outbound message.
		frappe.log_error(
			title=f"Cliq: workflow notify failed for {doc.doctype} {doc.name}"[:140],
			message=frappe.get_traceback(),
		)


def get_active_workflow(doctype: str):
	name = frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}, "name")
	return frappe.get_cached_doc("Workflow", name) if name else None


def collect_recipients(doc, before, workflow, old_state, new_state, actor) -> list[str]:
	"""Emails to notify: forward recipients, then the reply recipient.

	Order is preserved so the forward recipients -- the people now blocking the
	document -- lead the list.
	"""
	users = get_forward_recipients(doc, workflow, new_state)

	reply_to = get_reply_recipient(doc, before, old_state)
	if reply_to:
		users.append(reply_to)

	# Compare on the email too, not just the user id: the same person can
	# arrive as a User id from a role and as a differently-cased id from a
	# transition condition, and only the address is a reliable identity.
	actor_email = get_user_email(actor)

	emails, seen = [], set()
	for user in users:
		if not user or user == actor:
			# Never tell the person who just acted.
			continue
		email = get_user_email(user)
		if not email or email == actor_email or email in seen:
			continue
		seen.add(email)
		emails.append(email)
	return emails


def get_forward_recipients(doc, workflow, new_state) -> list[str]:
	"""Who must act on the state the document just entered."""
	outgoing = [t for t in workflow.transitions if (t.state or "").strip() == new_state]
	if not outgoing:
		# Final state: the document is done and nobody is waiting on it.
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
	"""The one person a `doc.<field> == frappe.session.user` condition allows."""
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
	"""Who moved the document into the state it has just left.

	Three sources, in order, and the order is the whole point -- the obvious
	two are both unreliable here:

	  Version       the general answer, but only exists where the doctype has
	                track_changes on. Leave Application on this site does not,
	                so it returns nothing.

	  WorkflowAction `completed_by` is filled in when Frappe closes out the
	                action, and a leftover Open action gets attributed to
	                whoever triggered the NEXT step -- observed here as the
	                'Pending Leave Approval' row reading completed_by = the
	                head approver after they acted. Useful when it agrees,
	                never trusted alone.

	  before        the pre-save snapshot. Its `modified_by` is whoever last
	                saved the document, which is exactly the previous actor.
	                Note this must come off `before` and not off `doc`:
	                set_user_and_timestamp() has already rewritten
	                doc.modified_by to the current user by the time an
	                on_update hook runs, so reading it from `doc` yields the
	                person who just acted and the reply is then dropped as
	                self-notification.
	"""
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
	"""Yield (fieldname, before, after) from a Version row's `changed` list."""
	try:
		for entry in json.loads(data or "{}").get("changed") or []:
			if len(entry) == 3:
				yield entry[0], entry[1], entry[2]
	except Exception:
		return


def get_actor_from_workflow_actions(doc) -> str | None:
	"""The most recent completed action by someone other than the current user.

	`completed_by` on the row is who performed it. Skipping the current actor
	is what makes this the *previous* transition rather than this one, whose
	row is written during this same save.
	"""
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
		# Administrator is a shared login, not a person with a Cliq account.
		return None
	email = (frappe.db.get_value("User", user, "email") or user or "").strip()
	return email.lower() if "@" in email else None
