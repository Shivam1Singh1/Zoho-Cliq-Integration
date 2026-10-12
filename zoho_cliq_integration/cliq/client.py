# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.utils import cint, now_datetime

RETRYABLE_STATUS = (408, 429, 500, 502, 503, 504)

LIVE_STATUSES = ("Queued", "Sent", "Dry Run", "Undelivered")

DIRECT = "Direct Message"
CHANNEL = "Channel"
BOTH = "Both"


def get_settings():
	return frappe.get_cached_doc("Cliq Settings")


def get_modes(settings=None) -> list[str]:
	settings = settings or get_settings()
	target_type = settings.target_type or DIRECT
	if target_type == BOTH:
		return [DIRECT, CHANNEL]
	return [target_type]


def resolve_identity(user_email: str) -> str | None:
	user_email = (user_email or "").strip()
	if not user_email:
		return None

	settings = get_settings()
	field = settings.identity_field or "user_id"

	if field == "user_id":
		return user_email.lower()

	mapped = frappe.db.get_value(
		"Employee",
		{"user_id": user_email, "status": "Active"},
		field,
	)

	mapped = (mapped or "").strip()
	return (mapped or user_email).lower()


def resolve_target(settings, mode: str, recipient_user: str | None) -> str | None:
	if mode == CHANNEL:
		channel = (settings.channel_unique_name or "").strip()
		if not channel:
			frappe.log_error(
				title="Cliq Settings incomplete",
				message=(
					"Cliq is set to post into a channel but no Channel Unique Name "
					"is set, so nothing can be sent."
				),
			)
			return None
		return channel

	target = resolve_identity(recipient_user)
	if not target:
		return None

	redirect = (settings.test_redirect or "").strip()
	return redirect.lower() if redirect else target


def queue_message(
	recipient_user: str | None,
	subject: str,
	message: str,
	notification: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	trigger_state: str | None = None,
	modes: list[str] | None = None,
) -> list[str]:
	settings = get_settings()
	if not settings.enabled:
		return []

	log_names = []
	for mode in modes or get_modes(settings):
		target = resolve_target(settings, mode, recipient_user)
		if not target:
			continue

		if _already_raised(
			notification, reference_doctype, reference_name, trigger_state, target
		):
			continue

		log = frappe.get_doc(
			{
				"doctype": "Cliq Message Log",
				"status": "Dry Run" if settings.dry_run else "Queued",
				"delivery_mode": mode,
				"notification": notification,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"trigger_state": trigger_state,
				"recipient_user": recipient_user,
				"recipient_cliq": target,
				"subject": subject,
				"message": message,
				"attempts": 0,
			}
		)
		log.insert(ignore_permissions=True)
		log_names.append(log.name)

		if settings.dry_run:
			continue

		frappe.enqueue(
			"zoho_cliq_integration.cliq.client.deliver",
			queue="short",
			enqueue_after_commit=True,
			job_id=f"cliq-msg::{log.name}",
			log_name=log.name,
		)

	return log_names


def _already_raised(notification, reference_doctype, reference_name, trigger_state, target):
	if not (reference_doctype and reference_name):
		return False

	return bool(
		frappe.db.exists(
			"Cliq Message Log",
			{
				"notification": _unset_or(notification),
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"trigger_state": _unset_or(trigger_state),
				"recipient_cliq": target,
				"status": ("in", LIVE_STATUSES),
			},
		)
	)


def _unset_or(value):
	return value if value else ("is", "not set")


def deliver(log_name: str):
	log = frappe.get_doc("Cliq Message Log", log_name)
	if log.status not in ("Queued", "Failed"):
		return

	settings = get_settings()
	if not settings.enabled:
		_finish(log, "Skipped", error="Cliq Settings is disabled.")
		return

	if settings.dry_run:
		_finish(log, "Dry Run")
		return

	mode = log.delivery_mode or get_modes(settings)[0]
	attempts = cint(log.attempts) + 1
	max_attempts = cint(settings.max_attempts) or 3

	try:
		status_code, body = _post(settings, log.recipient_cliq, log.subject, log.message, mode)
	except Exception as exc:
		if attempts < max_attempts:
			_retry(log, attempts, str(exc))
		else:
			_finish(log, "Failed", attempts=attempts, error=str(exc))
		return

	if 200 <= status_code < 300:
		if mode == CHANNEL or _resolved_anyone(body):
			_finish(log, "Sent", attempts=attempts, response=body)
		else:
			_finish(
				log,
				"Undelivered",
				attempts=attempts,
				error=(
					f"Cliq accepted the message but resolved no user for "
					f"'{log.recipient_cliq}'. The address has no Cliq account, "
					f"or the bot may not DM it."
				),
				response=body,
			)
		return

	error = f"HTTP {status_code}: {body}"
	if status_code in RETRYABLE_STATUS and attempts < max_attempts:
		_retry(log, attempts, error)
	else:
		_finish(log, "Failed", attempts=attempts, error=error, response=body)


def _resolved_anyone(body: str) -> bool:
	try:
		return bool(json.loads(body or "{}").get("user_ids"))
	except Exception:
		return True


def _post(settings, target: str, subject: str, message: str, mode: str = DIRECT):
	import requests

	zapikey = settings.get_password("zapikey", raise_exception=False)
	text = f"*{subject}*\n\n{message}" if subject else message

	if mode == CHANNEL:
		url = f"https://{settings.api_domain}/api/v2/channelsbyname/{target}/message"
		payload = {"text": text}
	else:
		url = (
			f"https://{settings.api_domain}/api/v2/bots/"
			f"{settings.bot_unique_name}/message"
		)
		payload = {"text": text, "userids": target}

	response = requests.post(
		url,
		params={"zapikey": zapikey},
		json=payload,
		timeout=cint(settings.timeout) or 10,
	)
	return response.status_code, response.text[:4000]


def _retry(log, attempts, error):
	log.db_set(
		{"status": "Queued", "attempts": attempts, "error": error},
		update_modified=False,
	)
	frappe.db.commit()
	frappe.enqueue(
		"zoho_cliq_integration.cliq.client.deliver",
		queue="short",
		job_id=f"cliq-msg::{log.name}::retry{attempts}",
		log_name=log.name,
	)


def _finish(log, status, attempts=None, error=None, response=None):
	settings = get_settings()

	if status == "Sent" and not settings.log_successful_sends:
		frappe.delete_doc(
			"Cliq Message Log", log.name, ignore_permissions=True, force=True
		)
		frappe.db.commit()
		return

	values = {"status": status, "error": error}
	if attempts is not None:
		values["attempts"] = attempts
	if status in ("Sent", "Dry Run"):
		values["sent_at"] = now_datetime()
	if response is not None:
		values["response"] = _pretty(response)

	log.db_set(values, update_modified=False)
	frappe.db.commit()


def _pretty(body):
	try:
		return json.dumps(json.loads(body), indent=2)
	except Exception:
		return body


@frappe.whitelist()
def send_test_message(recipient: str = None, text: str = "Test message from ERPNext."):
	frappe.only_for("System Manager")
	log_names = queue_message(
		recipient_user=recipient,
		subject="ERPNext test",
		message=text,
		trigger_state="manual-test",
	)
	if not log_names:
		return {"ok": False, "message": "Nothing queued. Check Cliq Settings is enabled and configured."}

	settings = get_settings()
	if settings.dry_run:
		return {
			"ok": True,
			"logs": log_names,
			"message": "Dry Run is on: the message was rendered and logged, not sent.",
		}

	results = []
	for log_name in log_names:
		deliver(log_name)
		log = frappe.get_doc("Cliq Message Log", log_name)
		results.append(
			{
				"log": log_name,
				"mode": log.delivery_mode,
				"status": log.status,
				"target": log.recipient_cliq,
				"error": log.error,
			}
		)

	return {
		"ok": all(r["status"] == "Sent" for r in results),
		"results": results,
	}
