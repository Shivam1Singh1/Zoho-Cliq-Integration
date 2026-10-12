# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Cliq as a Notification channel.

Frappe's Notification doctype already does everything a Cliq alert needs: it
watches a field for a change, evaluates a condition, resolves recipients from
document fields, and renders a Jinja message. The only thing it cannot do is
deliver over Cliq.

So this adds a channel rather than a parallel rule engine. `Cliq` is appended
to the channel options by a Property Setter (see setup.py), and the single
dispatch method below is overridden to route it. Everything else -- conditions,
recipients, templating, the Value Change trigger -- is inherited unchanged.
"""

import re
from html import unescape

import frappe
from frappe.email.doctype.notification.notification import Notification
from frappe.utils import get_url_to_form, strip_html_tags
from frappe.utils.jinja import validate_template

from zoho_cliq_integration.cliq.client import get_settings, is_channel_mode, queue_message

CLIQ = "Cliq"

# Tags that end a visual block, so their removal must leave a line break.
_BLOCK_END = re.compile(
	r"(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6]|blockquote|pre|table|ul|ol)\s*>"
)


class CliqNotification(Notification):
	def validate(self):
		super().validate()

		# The parent validates the subject template for every channel except
		# ours, because it does not know ours exists.
		if self.channel == CLIQ and self.subject:
			validate_template(self.subject)

	def send_notification_by_channel(self, doc, context):
		if self.channel != CLIQ:
			super().send_notification_by_channel(doc, context)
			return

		# Mirrors the parent's contract: a delivery failure is logged, never
		# raised, so a broken notification can never block a user's save.
		try:
			self.send_a_cliq_msg(doc, context)
		except Exception:
			self.log_error("Failed to send Cliq Notification")

		if self.send_system_notification:
			self.create_system_notification(doc, context)

	def send_a_cliq_msg(self, doc, context):
		if is_channel_mode():
			# The channel is the audience. Resolving per-document recipients
			# would only duplicate the same post once per approver.
			targets = [None]
		else:
			recipients, cc, bcc = self.get_list_of_recipients(doc, context)
			targets = _unique(list(recipients) + list(cc) + list(bcc))

			if not targets:
				fallback = (get_settings().fallback_recipient or "").strip()
				if not fallback:
					# No approver and no fallback configured. Recorded rather
					# than dropped, so the gap shows up in the log.
					self.log_error(
						f"Cliq: no recipient for {doc.doctype} {doc.name} "
						f"and no fallback configured"
					)
					return
				targets = [fallback]

		subject = self.subject or ""
		if "{" in subject:
			subject = frappe.render_template(subject, context)

		message = _to_cliq_text(frappe.render_template(self.message or "", context))
		message = _with_link(message, doc)
		trigger_state = _trigger_state(self, doc)

		for recipient in targets:
			queue_message(
				recipient_user=recipient,
				subject=subject,
				message=message,
				notification=self.name,
				reference_doctype=doc.doctype,
				reference_name=doc.name,
				trigger_state=trigger_state,
			)


def _unique(values):
	seen = []
	for v in values:
		v = (v or "").strip()
		if v and v not in seen:
			seen.append(v)
	return seen


def _to_cliq_text(html: str) -> str:
	"""Notification messages are authored as Markdown or HTML; Cliq wants text.

	frappe.strip_html_tags only deletes the tags, so a paragraph break becomes
	nothing at all and "...applied for leave.</p><p>Type: Casual" arrives as
	"applied for leave.Type: Casual". Block boundaries are turned into real
	newlines first, and entities are decoded after, since stripping tags leaves
	&amp; and &nbsp; behind.
	"""
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
			# Keep one blank line between blocks, never a run of them.
			if blank_run == 1 and out:
				out.append("")
	return "\n".join(out).strip()


def _with_link(message: str, doc) -> str:
	"""Append a Markdown link. Cliq renders [label](url), so show the doc name."""
	try:
		url = get_url_to_form(doc.doctype, doc.name)
	except Exception:
		return message
	link = f"[{doc.name}]({url})"
	return f"{message}\n\n{link}" if message else link


def _trigger_state(notification, doc):
	"""What changed, for the duplicate guard and for the log.

	Prefer the watched field, then the workflow state. A New-event rule has no
	`value_changed`, but it still needs a meaningful key: falling back to the
	bare event name would make every creation look identical to the duplicate
	guard, and would tell the reader nothing in the log.
	"""
	if notification.value_changed:
		return str(doc.get(notification.value_changed) or "")
	if doc.get("workflow_state"):
		return str(doc.get("workflow_state"))
	return notification.event or ""
