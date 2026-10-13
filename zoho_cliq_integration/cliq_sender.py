# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.utils import get_url_to_form, now_datetime

from zoho_cliq_integration.cliq.client import CHANNEL, _post, get_modes, get_settings


def send_workflow_message(
	reference_doctype: str,
	reference_name: str,
	old_state: str,
	new_state: str,
	actor: str,
	emails: list,
	applicant: str | None = None,
):
	try:
		settings = get_settings()
		if not settings.enabled:
			return {"ok": False, "skipped": "Cliq Settings is disabled."}

		targets = [e for e in (emails or []) if e]
		if not targets:
			return {"ok": False, "skipped": "No recipients."}

		redirect = (settings.test_redirect or "").strip()
		intended = list(targets)
		if redirect:
			targets = [redirect.lower()]

		subject = f"{reference_doctype} {reference_name}: {new_state}"
		text = build_message(
			reference_doctype, reference_name, old_state, new_state, actor, applicant
		)

		plans = build_plans(
			settings, targets, reference_doctype, reference_name, old_state, new_state
		)
		if not plans:
			return {"ok": False, "skipped": "No destination configured."}

		if settings.dry_run:
			for mode, _target, log_targets in plans:
				record(
					"Dry Run", mode, reference_doctype, reference_name, old_state,
					new_state, intended, log_targets, subject, text,
				)
			return {
				"ok": True,
				"dry_run": True,
				"intended": intended,
				"modes": [mode for mode, _t, _lt in plans],
				"text": text,
			}

		outcomes = []
		for mode, target, log_targets in plans:
			status_code, body = _post(settings, target, subject, text, mode)
			resolved = resolved_user_ids(body)

			ok = 200 <= status_code < 300 and (
				mode == CHANNEL or resolved is None or bool(resolved)
			)

			record(
				"Sent" if ok else "Undelivered",
				mode, reference_doctype, reference_name, old_state, new_state,
				intended, log_targets, subject, text,
				status_code=status_code, body=body,
			)

			if not ok:
				frappe.log_error(
					title=f"Cliq: workflow message not delivered for {reference_name}"[:140],
					message=(
						f"{reference_doctype} {reference_name} ({old_state} -> {new_state})\n"
						f"mode     : {mode}\n"
						f"intended : {', '.join(intended)}\n"
						f"sent to  : {', '.join(log_targets)}\n"
						f"HTTP     : {status_code}\n"
						f"body     : {body}\n\n"
						f"An HTTP 200 with an empty user_ids means Zoho accepted the "
						f"message and resolved it to nobody: the address has no Cliq "
						f"account, or the bot is not permitted to DM it."
					),
				)

			outcomes.append(
				{
					"ok": ok,
					"mode": mode,
					"status_code": status_code,
					"body": body,
					"targets": log_targets,
					"resolved_user_ids": resolved,
				}
			)

		return {
			"ok": all(outcome["ok"] for outcome in outcomes),
			"outcomes": outcomes,
			"intended": intended,
			"text": text,
		}
	except Exception:
		frappe.log_error(
			title=f"Cliq: workflow message failed for {reference_name}"[:140],
			message=frappe.get_traceback(),
		)
		return {"ok": False, "error": "see Error Log"}


def build_plans(settings, targets, reference_doctype, reference_name, old_state, new_state):
	plans = []
	for mode in get_modes(settings):
		if mode == CHANNEL:
			channel = (settings.channel_unique_name or "").strip()
			if not channel:
				frappe.log_error(
					title="Cliq Settings incomplete",
					message=(
						"Cliq is set to post into a channel but no Channel Unique Name "
						f"is set, so the {reference_doctype} {reference_name} state "
						f"change ({old_state} -> {new_state}) could not be announced."
					),
				)
				continue
			plans.append((mode, channel, [channel]))
		else:
			plans.append((mode, ",".join(targets), targets))
	return plans


def record(
	status: str,
	mode: str,
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
	try:
		settings = get_settings()
		if status == "Sent" and not settings.log_successful_sends:
			return

		frappe.get_doc(
			{
				"doctype": "Cliq Message Log",
				"status": status,
				"delivery_mode": mode,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"trigger_state": state_label(old_state, new_state),
				"recipient_user": fit(intended),
				"recipient_cliq": fit(targets),
				"subject": subject,
				"message": text,
				"attempts": 1,
				"sent_at": now_datetime() if status in ("Sent", "Dry Run") else None,
				"response": json.dumps(
					{
						"http_status": status_code,
						"body": body,
						"mode": mode,
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


def state_label(old_state: str, new_state: str) -> str:
	# An empty old state means a creation, not a transition out of nowhere.
	# "(new) -> Pending" says that; " -> Pending" reads like a lost value, and
	# it is also the duplicate guard's key, so it has to be stable.
	return f"{old_state} -> {new_state}" if old_state else f"(new) -> {new_state}"


def fit(addresses: list, limit: int = 140) -> str:
	joined = ", ".join(addresses)
	if len(joined) <= limit:
		return joined

	kept = []
	for address in addresses:
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
	lines = [
		f"{reference_doctype} created: {new_state}"
		if not old_state
		else f"{reference_doctype}: {old_state} -> {new_state}",
		"",
	]
	if applicant:
		lines.append(f"Applicant: {applicant}")
	lines.append(
		f"{'Raised by' if not old_state else 'Actioned by'}: {get_full_name(actor)}"
	)

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
	try:
		return json.loads(body or "{}").get("user_ids") or []
	except Exception:
		return None
