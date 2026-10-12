# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import re
from html import unescape

import frappe
from frappe.email.doctype.notification.notification import Notification
from frappe.utils import get_url_to_form, strip_html_tags
from frappe.utils.jinja import validate_template

from zoho_cliq_integration.cliq.client import (
	CHANNEL,
	DIRECT,
	get_modes,
	get_settings,
	queue_message,
)

CLIQ = "Cliq"

_BLOCK_END = re.compile(
	r"(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6]|blockquote|pre|table|ul|ol)\s*>"
)


class CliqNotification(Notification):
	def validate(self):
		super().validate()

		if self.channel == CLIQ and self.subject:
			validate_template(self.subject)

	def send_notification_by_channel(self, doc, context):
		if self.channel != CLIQ:
			super().send_notification_by_channel(doc, context)
			return

		try:
			self.send_a_cliq_msg(doc, context)
		except Exception:
			self.log_error("Failed to send Cliq Notification")

		if self.send_system_notification:
			self.create_system_notification(doc, context)

	def send_a_cliq_msg(self, doc, context):
		modes = get_modes()

		subject = self.subject or ""
		if "{" in subject:
			subject = frappe.render_template(subject, context)

		message = _to_cliq_text(frappe.render_template(self.message or "", context))
		message = _with_link(message, doc)
		trigger_state = _trigger_state(self, doc)

		if CHANNEL in modes:
			queue_message(
				recipient_user=None,
				subject=subject,
				message=message,
				notification=self.name,
				reference_doctype=doc.doctype,
				reference_name=doc.name,
				trigger_state=trigger_state,
				modes=[CHANNEL],
			)

		if DIRECT not in modes:
			return

		recipients, cc, bcc = self.get_list_of_recipients(doc, context)
		targets = _unique(list(recipients) + list(cc) + list(bcc))

		if not targets:
			fallback = (get_settings().fallback_recipient or "").strip()
			if not fallback:
				self.log_error(
					f"Cliq: no recipient for {doc.doctype} {doc.name} "
					f"and no fallback configured"
				)
				return
			targets = [fallback]

		for recipient in targets:
			queue_message(
				recipient_user=recipient,
				subject=subject,
				message=message,
				notification=self.name,
				reference_doctype=doc.doctype,
				reference_name=doc.name,
				trigger_state=trigger_state,
				modes=[DIRECT],
			)


def _unique(values):
	seen = []
	for v in values:
		v = (v or "").strip()
		if v and v not in seen:
			seen.append(v)
	return seen


def _to_cliq_text(html: str) -> str:
	text = _BLOCK_END.sub("\n", html or "")
	text = strip_html_tags(text)
	text = unescape(text).replace("\xa0", " ")

	out, blank_run = [], 0
	for line in text.splitlines():
		line = line.strip()
		if line:
			blank_run = 0
			out.append(line)
		else:
			blank_run += 1
			if blank_run == 1 and out:
				out.append("")
	return "\n".join(out).strip()


def _with_link(message: str, doc) -> str:
	try:
		url = get_url_to_form(doc.doctype, doc.name)
	except Exception:
		return message
	link = f"[{doc.name}]({url})"
	return f"{message}\n\n{link}" if message else link


def _trigger_state(notification, doc):
	if notification.value_changed:
		return str(doc.get(notification.value_changed) or "")
	if doc.get("workflow_state"):
		return str(doc.get("workflow_state"))
	return notification.event or ""
