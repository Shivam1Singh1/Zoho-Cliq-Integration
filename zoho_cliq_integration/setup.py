# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

CLIQ = "Cliq"


def after_install():
	add_cliq_channel()
	seed_settings()


def after_migrate():
	add_cliq_channel()


def add_cliq_channel():
	meta = frappe.get_meta("Notification")
	field = meta.get_field("channel")
	if not field:
		return

	options = [o for o in (field.options or "").split("\n") if o.strip()]

	if CLIQ not in options:
		options.append(CLIQ)

	make_property_setter(
		"Notification",
		"channel",
		"options",
		"\n".join(options),
		"Text",
		validate_fields_for_doctype=False,
		is_system_generated=True,
	)
	frappe.clear_cache(doctype="Notification")


def seed_settings():
	settings = frappe.get_single("Cliq Settings")
	if settings.get("api_domain"):
		return

	settings.enabled = 0
	settings.dry_run = 1
	settings.api_domain = "cliq.zoho.in"
	settings.identity_field = "user_id"
	settings.timeout = 10
	settings.max_attempts = 3
	settings.log_successful_sends = 1
	settings.save(ignore_permissions=True)
