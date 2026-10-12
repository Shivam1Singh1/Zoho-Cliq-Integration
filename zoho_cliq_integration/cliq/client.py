# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Delivery of a single Cliq message.

Two entry points:

    queue_message()  runs inside the document's own transaction. It writes a
                     Cliq Message Log row and enqueues the send. Nothing here
                     touches the network, so a slow or dead Cliq can never
                     delay or fail a user's save.

    deliver()        runs in a background worker and performs the HTTP call.

The Zoho request contract lives in `_post`, and it is now verified against a
live bot rather than assumed. Two findings from that testing shape this file:

  * `userids` must be a COMMA-SEPARATED STRING. Sent as a JSON array it
    returns HTTP 400 with an empty body, every time.

  * HTTP 200 does NOT mean delivered. An address Cliq cannot resolve comes
    back 200 with `{"user_ids": [], "users": []}` -- accepted, delivered to
    nobody. Success therefore requires a non-empty `user_ids`, which is why
    there is an `Undelivered` status distinct from `Failed`.
"""

import json

import frappe
from frappe.utils import cint, now_datetime

# Worth another try: the far end was busy, throttled, or briefly unavailable.
# Anything else -- a bad token, an unknown bot, an unresolvable recipient --
# fails identically on every retry, so it is settled on the first attempt.
RETRYABLE_STATUS = (408, 429, 500, 502, 503, 504)

# A message already queued, sent, or dry-run for this exact combination is not
# sent twice. This is what makes a re-save of an unchanged document harmless
# even if something upstream calls us more than once.
LIVE_STATUSES = ("Queued", "Sent", "Dry Run", "Undelivered")

CHANNEL = "Channel"


def get_settings():
	return frappe.get_cached_doc("Cliq Settings")


def is_channel_mode(settings=None) -> bool:
	settings = settings or get_settings()
	return (settings.target_type or "Direct Message") == CHANNEL


def resolve_identity(user_email: str) -> str | None:
	"""Map an ERPNext login address to the address the person uses in Cliq.

	Recipients arrive as ERPNext User ids, which are login emails. Whether that
	is also the Cliq login depends on the site: `identity_field` in Cliq
	Settings decides. When no Employee record matches, the address is used
	unchanged -- better to attempt delivery than to drop the message silently.
	"""
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

	# Employee rows carry stray whitespace often enough to matter: two active
	# records on this site have a leading space or a trailing tab, and an
	# unstripped address matches nothing at the Cliq end.
	mapped = (mapped or "").strip()
	return (mapped or user_email).lower()


def queue_message(
	recipient_user: str | None,
	subject: str,
	message: str,
	notification: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	trigger_state: str | None = None,
) -> str | None:
	"""Log the message and schedule it. Returns the log name, or None if skipped."""
	settings = get_settings()
	if not settings.enabled:
		return None

	if is_channel_mode(settings):
		target = (settings.channel_unique_name or "").strip()
		if not target:
			frappe.log_error(
				"Cliq is in Channel mode but no Channel Unique Name is set.",
				"Cliq Settings incomplete",
			)
			return None
	else:
		target = resolve_identity(recipient_user)
		if not target:
			return None
		# A Personal bot can only DM its creator, so a redirect is the only way
		# to see anything at all while testing. The intended recipient is still
		# recorded, so the log shows who it would have gone to in production.
		redirect = (settings.test_redirect or "").strip()
		if redirect:
			target = redirect.lower()

	if _already_raised(notification, reference_doctype, reference_name, trigger_state, target):
		return None

	log = frappe.get_doc(
		{
			"doctype": "Cliq Message Log",
			"status": "Dry Run" if settings.dry_run else "Queued",
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

	if settings.dry_run:
		# Rendered, routed and recorded -- everything except the network call.
		return log.name

	frappe.enqueue(
		"zoho_cliq_integration.cliq.client.deliver",
		queue="short",
		# Nothing leaves the building for a transaction that later rolls back.
		enqueue_after_commit=True,
		job_id=f"cliq-msg::{log.name}",
		log_name=log.name,
	)
	return log.name


def _already_raised(notification, reference_doctype, reference_name, trigger_state, target):
	if not (reference_doctype and reference_name):
		return False

	return bool(
		frappe.db.exists(
			"Cliq Message Log",
			{
				"notification": notification or "",
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"trigger_state": trigger_state or "",
				"recipient_cliq": target,
				"status": ("in", LIVE_STATUSES),
			},
		)
	)


def deliver(log_name: str):
	"""Background worker: perform the HTTP call and record the outcome."""
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

	attempts = cint(log.attempts) + 1
	max_attempts = cint(settings.max_attempts) or 3

	try:
		status_code, body = _post(settings, log.recipient_cliq, log.subject, log.message)
	except Exception as exc:
		# Network-level failure: timeout, DNS, refused connection.
		if attempts < max_attempts:
			_retry(log, attempts, str(exc))
		else:
			_finish(log, "Failed", attempts=attempts, error=str(exc))
		return

	if 200 <= status_code < 300:
		if is_channel_mode(settings) or _resolved_anyone(body):
			_finish(log, "Sent", attempts=attempts, response=body)
		else:
			# Accepted by Zoho, delivered to nobody. Marking this Sent would
			# hide every address mismatch in the company behind a green tick.
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
	"""Did Cliq actually match the address to a person?"""
	try:
		return bool(json.loads(body or "{}").get("user_ids"))
	except Exception:
		# Unparseable body on a 2xx: assume delivered rather than cry wolf.
		return True


def _post(settings, target: str, subject: str, message: str):
	"""The single place that knows Zoho's request shape.

	Verified against a live bot on cliq.zoho.in. Direct messages go through the
	bot endpoint with `userids` as a comma-separated string; channel posts go
	through channelsbyname, where the channel is in the path and no recipient
	is needed. Both authenticate with the same user-level zapikey.
	"""
	import requests

	zapikey = settings.get_password("zapikey", raise_exception=False)
	text = f"*{subject}*\n\n{message}" if subject else message

	if is_channel_mode(settings):
		url = f"https://{settings.api_domain}/api/v2/channelsbyname/{target}/message"
		payload = {"text": text}
	else:
		url = (
			f"https://{settings.api_domain}/api/v2/bots/"
			f"{settings.bot_unique_name}/message"
		)
		# A JSON array here returns 400 with an empty body. It must be a string.
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
	"""Send one message on demand, to prove the configuration works end to end.

	Honours Dry Run, so it is safe to call before going live.
	"""
	frappe.only_for("System Manager")
	log_name = queue_message(
		recipient_user=recipient,
		subject="ERPNext test",
		message=text,
		trigger_state="manual-test",
		reference_doctype="Cliq Settings",
		reference_name=frappe.generate_hash(length=10),
	)
	if not log_name:
		return {"ok": False, "message": "Nothing queued. Check Cliq Settings is enabled and configured."}

	settings = get_settings()
	if settings.dry_run:
		return {
			"ok": True,
			"log": log_name,
			"message": "Dry Run is on: the message was rendered and logged, not sent.",
		}

	# Run it in the foreground so the caller sees the real outcome.
	deliver(log_name)
	log = frappe.get_doc("Cliq Message Log", log_name)
	return {
		"ok": log.status == "Sent",
		"log": log_name,
		"status": log.status,
		"target": log.recipient_cliq,
		"error": log.error,
	}
