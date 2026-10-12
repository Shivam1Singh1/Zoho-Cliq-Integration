# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Render and deliver one workflow-state message.

Runs in a background worker: `workflow_notify` enqueues it after commit, so a
slow or dead Cliq can never delay a save, and nothing is sent for a
transaction that later rolls back.

The HTTP call itself is delegated to `cliq.client._post`, which is the single
place in this app that knows Zoho's request shape. That matters more than it
looks: `userids` must be a comma-separated STRING -- sent as a JSON array the
API returns HTTP 400 with an empty body -- and a 200 with an empty `user_ids`
means accepted and delivered to nobody. Re-implementing the call here would
rediscover both the hard way.

Bot name, zapikey and data-centre domain are read from Cliq Settings. They are
deliberately not duplicated onto Cliq Notification Settings: one integration,
one place for its credentials.
"""

import json

import frappe
from frappe.utils import get_url_to_form, now_datetime

from zoho_cliq_integration.cliq.client import _post, get_settings, is_channel_mode


def send_workflow_message(
	reference_doctype: str,
	reference_name: str,
	old_state: str,
	new_state: str,
	actor: str,
	emails: list,
	applicant: str | None = None,
):
	"""Deliver to every recipient in one call. Returns the outcome for callers
	that want it (the test harness); failures are logged, never raised."""
	try:
		settings = get_settings()
		if not settings.enabled:
			return {"ok": False, "skipped": "Cliq Settings is disabled."}

		targets = [e for e in (emails or []) if e]
		if not targets:
			return {"ok": False, "skipped": "No recipients."}

		# The site's staging guard. While it is set, every message goes to that
		# one address instead -- which also stops a role fan-out from spraying
		# real people from a test bench.
		redirect = (settings.test_redirect or "").strip()
		intended = list(targets)
		if redirect:
			targets = [redirect.lower()]

		subject = f"{reference_doctype} {reference_name}: {new_state}"
		text = build_message(
			reference_doctype, reference_name, old_state, new_state, actor, applicant
		)

		if settings.dry_run:
			record(
				"Dry Run", reference_doctype, reference_name, old_state, new_state,
				intended, targets, subject, text,
			)
			return {
				"ok": True,
				"dry_run": True,
				"intended": intended,
				"targets": targets,
				"text": text,
			}

		# `_post` switches on the site's target_type, and in Channel mode it
		# treats its target argument as a channel name and puts it in the URL.
		# Handing it a comma-joined list of emails there would build a nonsense
		# request, so the target is chosen for the mode we are actually in.
		if is_channel_mode(settings):
			channel = (settings.channel_unique_name or "").strip()
			if not channel:
				frappe.log_error(
					title="Cliq Settings incomplete",
					message=(
						"Cliq is in Channel mode but no Channel Unique Name is set, "
						f"so the {reference_doctype} {reference_name} state change "
						f"({old_state} -> {new_state}) could not be announced."
					),
				)
				return {"ok": False, "skipped": "Channel mode with no channel set."}
			target = channel
			# The post goes to the channel, so that is what the log must say
			# was sent to. `intended` still records the real recipients.
			targets = [channel]
		else:
			target = ",".join(targets)

		status_code, body = _post(settings, target, subject, text)
		resolved = resolved_user_ids(body)

		outcome = {
			# resolved is None when the body could not be parsed: no verdict,
			# so do not call it a failure. [] means Zoho matched nobody.
			"ok": 200 <= status_code < 300
			and (is_channel_mode(settings) or resolved is None or bool(resolved)),
			"status_code": status_code,
			"body": body,
			"intended": intended,
			"targets": targets,
			"resolved_user_ids": resolved,
			"text": text,
		}

		record(
			"Sent" if outcome["ok"] else "Undelivered",
			reference_doctype, reference_name, old_state, new_state,
			intended, targets, subject, text,
			status_code=status_code, body=body,
		)

		if not outcome["ok"]:
			frappe.log_error(
				title=f"Cliq: workflow message not delivered for {reference_name}"[:140],
				message=(
					f"{reference_doctype} {reference_name} ({old_state} -> {new_state})\n"
					f"intended : {', '.join(intended)}\n"
					f"sent to  : {', '.join(targets)}\n"
					f"HTTP     : {status_code}\n"
					f"body     : {body}\n\n"
					f"An HTTP 200 with an empty user_ids means Zoho accepted the "
					f"message and resolved it to nobody: the address has no Cliq "
					f"account, or the bot is not permitted to DM it."
				),
			)

		return outcome
	except Exception:
		frappe.log_error(
			title=f"Cliq: workflow message failed for {reference_name}"[:140],
			message=frappe.get_traceback(),
		)
		return {"ok": False, "error": "see Error Log"}


def record(
	status: str,
	reference_doctype: str,
	reference_name: str,
	old_state: str,
	new_state: str,
	intended: list,
	targets: list,
	subject: str,
	text: str,
	status_code=None,
	body=None,
):
	"""Write the outcome to Cliq Message Log.

	This is the app's existing record of what it sent, so workflow messages
	appear beside the Notification-channel ones rather than in a log of their
	own. It also keeps the API's verdict: `frappe.logger` would not, since
	Frappe's default level is WARNING and an info() call is dropped on the
	floor -- which is exactly how the first run of this looked delivered while
	leaving no trace at all.

	Never raises: losing the audit row must not lose the message.
	"""
	try:
		settings = get_settings()
		if status == "Sent" and not settings.log_successful_sends:
			return

		frappe.get_doc(
			{
				"doctype": "Cliq Message Log",
				"status": status,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"trigger_state": f"{old_state} -> {new_state}",
				# The intended recipients, so a test_redirect never hides who
				# the message was actually for.
				"recipient_user": fit(intended),
				"recipient_cliq": fit(targets),
				"subject": subject,
				"message": text,
				"attempts": 1,
				"sent_at": now_datetime() if status in ("Sent", "Dry Run") else None,
				# The full recipient list goes here, where there is room for it:
				# recipient_user is Data(140) and a wide fan-out does not fit.
				"response": json.dumps(
					{
						"http_status": status_code,
						"body": body,
						"intended_recipients": intended,
						"sent_to": targets,
					},
					indent=2,
				),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"Cliq: could not log workflow message for {reference_name}"[:140],
			message=frappe.get_traceback(),
		)


def fit(addresses: list, limit: int = 140) -> str:
	"""Join addresses into a Data(140) field without losing the count.

	Plain truncation reads as a complete list in the log while quietly
	dropping names off the end -- which is how a nine-recipient fan-out
	appeared to stop at six. The full list is in `response`.
	"""
	joined = ", ".join(addresses)
	if len(joined) <= limit:
		return joined

	kept = []
	for address in addresses:
		# Leave room for the "(+N more)" suffix.
		if len(", ".join(kept + [address])) > limit - 14:
			break
		kept.append(address)
	return f"{', '.join(kept)} (+{len(addresses) - len(kept)} more)"


def build_message(
	reference_doctype: str,
	reference_name: str,
	old_state: str,
	new_state: str,
	actor: str,
	applicant: str | None = None,
) -> str:
	"""One format for every doctype, so nothing here is doctype-specific.

	`applicant` is whatever the document calls a person -- employee_name where
	it exists -- and is simply omitted when the doctype has no such field.
	"""
	lines = [
		f"{reference_doctype}: {old_state or '(none)'} -> {new_state}",
		"",
	]
	if applicant:
		lines.append(f"Applicant: {applicant}")
	lines.append(f"Actioned by: {get_full_name(actor)}")

	try:
		url = get_url_to_form(reference_doctype, reference_name)
		lines += ["", f"[{reference_name}]({url})"]
	except Exception:
		lines += ["", reference_name]

	return "\n".join(lines)


def get_full_name(user: str) -> str:
	if not user:
		return "unknown"
	return frappe.db.get_value("User", user, "full_name") or user


def resolved_user_ids(body: str):
	"""Who Zoho actually matched the addresses to.

	Returns None, not [], for a body it cannot parse. The caller treats None
	as "no verdict" and leaves the send counted as delivered, which is what
	`cliq.client._resolved_anyone` does too -- an empty list means Zoho
	explicitly matched nobody, and conflating the two would cry wolf on every
	response shape Zoho ever changes.
	"""
	try:
		return json.loads(body or "{}").get("user_ids") or []
	except Exception:
		return None
