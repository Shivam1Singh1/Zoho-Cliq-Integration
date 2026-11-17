# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Material Request -- the one handler this app has wired up.

A Material Request enters 'Pending from Project Manager'; the project manager
it is now waiting on gets one Cliq message. That is the entire behaviour. No
other state sends anything, and nothing is ever sent back to the requester --
the message is one-directional by design, so an approval, a rejection, a
send-back and a cancellation all pass in silence.

Finding that one project manager is the only hard part, because the approver
is not in the Workflow. `Purchase Requisition Matrix` gates the transition by
role, and 'Projects Manager' has 23 holders -- the role says who *may* act,
never who *must*. The address is on the document instead, in Project Head
Name, and `resolve_project_manager` reads that first; the rest of the chain
exists only for records where the form never filled it in.

Everything about Material Request is in this file: the state it reacts to, how
it finds the recipient, and every word it can say. The machinery underneath --
address resolution, the duplicate guard, the queue -- is in `common.py` and
names no doctype.
"""

import frappe
from frappe.utils import formatdate

from zoho_cliq_integration.cliq.client import get_settings
from zoho_cliq_integration.notifications import common
from zoho_cliq_integration.zoho_cliq_integration.doctype.cliq_notification_settings.cliq_notification_settings import (
	get_enabled_doctypes,
)

DOCTYPE = "Material Request"

# The only state that sends. Every other state in the workflow -- including
# the approvals, rejections and send-backs -- passes in silence.
NOTIFY_STATE = "Pending from Project Manager"

APPROVER = "approver"


# ===========================================================================
# WORDING -- everything a Material Request notification can say
# ===========================================================================
#
# Nothing below this banner is logic, and nothing above it is a sentence.
# Rewording is a change to this block alone. The placeholders are the contract
# between the block and `base_context`:
#
#     mr      MAT-MR-2026-03931
#     url     link to the form
#     reason  remark captured with the action, or ""

# The message is a Cliq card, so the wording is only the one line above it --
# everything else the approver needs is in CARD_ROWS below. There is no footer:
# the card carries the detail and the button carries the link.
SUBJECTS = {
	APPROVER: "{mr}: Please Approve it",
}

MESSAGES = {
	# Deliberately bare of words. The subject is already rendered in bold above
	# it by `build_request`, and repeating the document in a sentence under a
	# card that names it reads as padding.
	#
	# It is not empty, though: `build_request` joins subject and body with a
	# blank line, so a body gives the heading room to breathe above the table.
	# The character is a non-breaking space rather than a plain one because a
	# trailing plain space is trimmed and the gap disappears with it.
	"approver_pending": " ",
}

# Column headings, deliberately blank: every row is self-evidently a field and
# its value, so "Field" and "Value" were a row of the table spent saying
# nothing.
#
# They cannot simply be dropped. A table slide with no `headers` key is
# rejected with HTTP 400 `operation_failed`, and the two strings have to differ
# because each row is a dict keyed by its header -- two identical keys collapse
# into one and the table loses a column. Hence "" and a single space.
CARD_HEADERS = ("", " ")

# What the card shows, in this order. Each entry is (label, fieldname); the
# value is read off the document and formatted by `card_value`. This list is
# the card -- adding a row is one line here and nothing else.
#
# Labels are kept short because the left column is narrow: "Employee Function"
# and "Project Description" each wrapped onto two lines, which doubled the
# height of their row for no added meaning.
# Series and Company are deliberately absent. Every Material Request on this
# site carries the same series and the same company, so both rows said the
# same thing on every card and cost the approver two lines to read nothing.
CARD_ROWS = (
	("Purpose", "material_request_type"),
	# NOT `owner`. The form's 'Created By' is its own field, and the two
	# disagree: MAT-MR-2026-03942 is owned by Administrator but was created by
	# developer@example.com. `owner` is who saved the record, which on a
	# workflow document is often not who raised it.
	("Created By", ("custom_full_name", "custom_created_by", "owner")),
	("Date", "transaction_date"),
	("Stages", "custom_stages"),
	("Function Head", "custom_employee_function"),
	# The project's name, not its code. 'PLTP-2025-0045' identifies the record;
	# 'AAV - Huntington Disease - HTT' is what the approver recognises. The code
	# is the fallback for a record whose name was never fetched.
	("Project", ("custom_project_name", "project")),
	("Description", "project_description"),
)

# A field whose stored value is a code, and where the readable name lives.
# 'VP-LTP-MFG-001' identifies the record but says nothing to the person being
# asked to approve. What they want from that record is the head it names --
# 'Rushikesh Patil' -- not the function's own name, which is the department
# and tells the approver nothing they do not already know.
NAMED_LINKS = {
	"custom_employee_function": ("Employee Function", "function_head_name"),
}

# Cliq rejects a button label over 20 characters with HTTP 400
# 'input_maxlength_reached', so this is a hard limit, not a style choice.
# "Open MAT-MR-2026-03832" is 22 and was the first thing that failed.
BUTTON_LABEL = "Open in ERPNext"
BUTTON_LABEL_LIMIT = 20

# Longest value a table cell may carry before it is cut; 0 would mean never.
#
# Project Description is free text and people paste paragraphs into it --
# MAT-MR-2026-03946 holds 245 characters, which wraps the card into a wall and
# buries the rows above it. Cut at 120 with an ellipsis; the approver opens the
# document for the rest.
CARD_VALUE_LIMIT = 120

# Written into Cliq Message Log when the message cannot be addressed. These
# are routine configuration gaps, not failures, so they never reach the Error
# Log.
SKIPS = {
	"no_project_manager": (
		"{mr} entered '{state}' but no project manager could be found for it. "
		"Neither Project Head Name nor Project Lead Name on the document holds "
		"an email address, project {project} has no Project Lead set, and "
		"Employee Function {ef} maps no Project Head for that project."
	),
	"no_email": (
		"{mr} entered '{state}' and its project manager is {user}, but no "
		"email address could be resolved for them (no User record, and no "
		"Employee with user_id, company_email or prefered_email set)."
	),
}


# ---------------------------------------------------------------------------
# hook entry point
# ---------------------------------------------------------------------------


def on_workflow_state_change(doc, method=None):
	"""Queue the notification when, and only when, the document starts waiting
	on its project manager.

	Registered on both `on_update` and `on_update_after_submit`. Every state in
	this workflow is doc_status 0 except the two terminal approvals, and
	`apply_workflow` reaches those through `doc.submit()`, which runs
	`on_update` before `on_submit` -- so `on_update` alone already sees the
	transition. The second event costs one `has_value_changed` call and covers
	any future allow-on-submit edit.
	"""
	try:
		if doc.doctype != DOCTYPE:
			return

		# The switch an administrator actually turns: remove the Material
		# Request row from Cliq Notification Settings and this app goes quiet
		# without a code change.
		if DOCTYPE not in get_enabled_doctypes():
			return

		transition = common.state_transition(doc)
		if not transition:
			return

		old_state, new_state = transition
		if new_state != NOTIFY_STATE:
			return

		common.enqueue_job(
			f"{__name__}.send_transition_messages",
			"cliq-mr",
			doc.name,
			old_state,
			new_state,
			material_request=doc.name,
		)
	except Exception:
		# A notification must never cost the user their save.
		frappe.log_error(
			title=f"Cliq MR: could not queue notification for {doc.name}"[:140],
			message=frappe.get_traceback(),
		)


# ---------------------------------------------------------------------------
# the job
# ---------------------------------------------------------------------------


def send_transition_messages(material_request, old_state, new_state, actor):
	"""Resolve the project manager and hand the message to the shared sender."""
	try:
		if not get_settings().enabled:
			return {"ok": False, "skipped": "Cliq Settings is disabled."}

		doc = frappe.get_doc(DOCTYPE, material_request)
		plan = build_plan(doc, new_state, actor)
		label = common.state_label(old_state, new_state)

		return {
			"ok": True,
			"transition": label,
			"queued": common.deliver(DOCTYPE, doc.name, plan, label),
			"skipped": plan["skips"],
		}
	except Exception:
		frappe.log_error(
			title=f"Cliq MR: notification failed for {material_request}"[:140],
			message=frappe.get_traceback(),
		)
		return {"ok": False, "error": "see Error Log"}


# ---------------------------------------------------------------------------
# who hears what
# ---------------------------------------------------------------------------


def build_plan(doc, new_state, actor) -> dict:
	"""Turn the transition into an addressed message, or a skip reason."""
	context = base_context(doc, new_state)

	link, resolved_via, reason = resolve_project_manager(doc, new_state)
	if reason:
		return {"messages": [], "skips": [reason]}

	email = common.resolve_email(link)
	if not email:
		return {
			"messages": [],
			"skips": [SKIPS["no_email"].format(mr=doc.name, state=new_state, user=link)],
		}

	message = {
		"email": email,
		"subject": SUBJECTS[APPROVER].format(**context),
		"body": common.render(MESSAGES["approver_pending"], context),
		"card": build_card(doc, context),
		"resolved_via": resolved_via,
	}

	# `dedupe` is what drops the message when the project manager is also the
	# person who just moved the document here. That never happens on a normal
	# approval, but a direct edit by them would otherwise bounce straight back.
	return {"messages": common.dedupe([message], actor), "skips": []}


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


def resolve_project_manager(doc, new_state):
	"""Return (user link or email, source, None), or (None, None, skip reason).

	The three sources are tried nearest-the-document first; see the module
	docstring for why there are three. `source` names the one that answered and
	is written to Cliq Message Log, so a recipient who looks wrong can be traced
	back to the exact field that produced them instead of being argued about.

	**Exactly one person, always.** Every branch returns on the first match, so
	a project mapped in two places cannot produce two messages -- the nearest
	source wins and the rest are never consulted.
	"""
	# 1. Project Head Name on this Material Request. This is the field the
	#    form fills with the project manager's address and the one to trust:
	#    across 17,700 documents it is the only one that ever holds an email.
	#    `custom_project_lead_name` never does -- not once -- which is why it
	#    is second and not first.
	for field in ("custom_project_head_name", "custom_project_lead_name"):
		value = (doc.get(field) or "").strip()
		if "@" in value:
			return value, f"Material Request.{field}", None

	project = (doc.get("project") or "").strip()
	if project:
		row = frappe.db.get_value(
			"Project",
			project,
			["custom_project_lead", "custom_project_lead_name"],
			as_dict=True,
		) or {}

		# 2. The project's own Project Lead -- a Link to Employee, so it
		#    resolves to a real address through common.resolve_email.
		employee = (row.get("custom_project_lead") or "").strip()
		if employee:
			return employee, f"Project {project}.custom_project_lead", None

		# Same free-text field one level up, in case the document's copy was
		# never fetched.
		lead = (row.get("custom_project_lead_name") or "").strip()
		if "@" in lead:
			return lead, f"Project {project}.custom_project_lead_name", None

	# 3. Employee Function, the route this handler used before. The project
	#    must match exactly: a row for a different project is a different
	#    manager, and matching loosely here is how the wrong person gets told.
	employee_function = get_employee_function(doc)
	if employee_function:
		for row in employee_function.project_list or []:
			if (row.projects or "").strip() == project and row.project_head:
				return (
					row.project_head,
					f"Employee Function {employee_function.name}.project_head[{project}]",
					None,
				)

	return None, None, SKIPS["no_project_manager"].format(
		mr=doc.name,
		state=new_state,
		project=project or "(not set)",
		ef=employee_function.name if employee_function else "(not set)",
	)


def get_employee_function(doc):
	"""The document's Employee Function, or None.

	Checked for existence first: `custom_employee_function` is a Link, but
	older rows hold codes for functions that have since been renamed or
	deleted, and `get_cached_doc` on a missing name raises.
	"""
	code = (doc.get("custom_employee_function") or "").strip()
	if not code or not frappe.db.exists("Employee Function", code):
		return None
	return frappe.get_cached_doc("Employee Function", code)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def base_context(doc, new_state) -> dict:
	"""The three values the wording and the card actually read.

	It used to carry eleven -- creator, actor, approver, items, required_by and
	more -- left over from when the message was a paragraph. The card reads its
	rows straight off the document through `CARD_ROWS`, so each of those was a
	lookup performed on every notification and then thrown away.
	"""
	return {
		"mr": doc.name,
		"url": common.form_url(DOCTYPE, doc.name),
		"reason": reason_for(doc, new_state),
	}


def build_card(doc, context) -> dict:
	"""The structured half of the message: the document, as a Cliq card.

	`slides` of type `table` renders the two-column grid. The alternative,
	`label`, renders the same pairs as a vertical list without a header row;
	both were sent to a live bot and compared, and the table was chosen.

	An empty value is dropped rather than shown blank. A row that says
	"Project Description" and nothing else is noise, and on a narrow screen
	every dropped row is one more real row visible without scrolling.
	"""
	field, value_header = CARD_HEADERS

	rows = []
	for label, fieldname in CARD_ROWS:
		value = card_value(doc, fieldname)
		if value:
			rows.append({field: label, value_header: value})

	# Not in the reference card, and added because the approver is the one
	# person who benefits from it: at this point the remark is whatever the
	# previous approver wrote on the way past. It appears only when there is
	# one, so the card is unchanged when there is not.
	if context.get("reason"):
		rows.append({field: "Remark", value_header: context["reason"]})

	return {
		# No `title` on the card. Cliq renders it as a line of its own directly
		# above `text`, and `text` already opens with the document name -- the
		# id appeared twice, stacked, which is the first thing a reader sees.
		# The theme stays: it is what makes this render as a card at all.
		"card": {"theme": "modern-inline"},
		# No slide title either. It used to repeat the document's own title
		# field above the table, which is a line the table does not need.
		"slides": [
			{
				"type": "table",
				"data": {"headers": list(CARD_HEADERS), "rows": rows},
			}
		],
		"buttons": [
			{
				"label": BUTTON_LABEL.format(**context)[:BUTTON_LABEL_LIMIT],
				"type": "+",
				"action": {
					"type": "open.url",
					"data": {"web": context["url"]},
				},
			}
		],
	}


def card_value(doc, fieldname) -> str:
	"""One card row's value, as a string Cliq can render.

	`fieldname` may be a tuple, in which case the first field with a value
	wins. That is how 'Created By' prefers the readable name over the link
	over `owner`, without three rows or three special cases.

	Dates arrive as `datetime.date` and would otherwise serialise as
	`2026-05-21`, which is not the format this site shows anywhere else.
	A `NAMED_LINKS` field is swapped for its readable name, falling back to the
	code if the record has no name set -- a code is poor, but blank is worse.
	"""
	if isinstance(fieldname, tuple):
		for candidate in fieldname:
			value = card_value(doc, candidate)
			if value:
				return value
		return ""

	value = doc.get(fieldname)
	if value in (None, ""):
		return ""

	if fieldname == "transaction_date":
		return formatdate(value)

	if fieldname in NAMED_LINKS:
		linked_doctype, name_field = NAMED_LINKS[fieldname]
		value = frappe.db.get_value(linked_doctype, value, name_field) or value

	# Collapse runs of whitespace: several of these values are typed by hand
	# and carry double spaces, which the table renders literally.
	value = " ".join(str(value).split())

	if CARD_VALUE_LIMIT and len(value) > CARD_VALUE_LIMIT:
		value = value[: CARD_VALUE_LIMIT - 1].rstrip() + "…"

	return value


def reason_for(doc, new_state) -> str:
	"""The remark attached to this action, or "" if there is none.

	Read from Material Request Workflow Log rather than `doc.custom_remark`,
	because mr_close.material_request_double_approval blanks that field during
	before_save -- by the time this runs it is always empty.
	"""
	remark = frappe.db.get_value(
		"Material Request Workflow Log",
		{"material_request": doc.name, "workflow_state": new_state},
		"remarks",
		order_by="action_datetime desc",
	)

	remark = (remark or doc.get("custom_remark") or "").strip()
	# The logger falls back to writing the state name when there is no remark;
	# echoing that back as a reason says nothing.
	if not remark or remark == new_state:
		return ""
	return remark
