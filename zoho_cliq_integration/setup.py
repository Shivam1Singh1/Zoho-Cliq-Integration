# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

import frappe

CLIQ = "Cliq"


def after_install():
	seed_settings()


def after_migrate():
	remove_cliq_channel()


def remove_cliq_channel():
	rules = frappe.get_all("Notification", filters={"channel": CLIQ}, pluck="name")

	for name in rules:
		frappe.db.set_value("Notification", name, "enabled", 0, update_modified=False)

	for row in frappe.get_all(
		"Property Setter",
		filters={
			"doc_type": "Notification",
			"field_name": "channel",
			"property": "options",
		},
		fields=["name", "value"],
	):
		if CLIQ in (row.value or ""):
			frappe.delete_doc(
				"Property Setter", row.name, ignore_permissions=True, force=True
			)

	frappe.clear_cache(doctype="Notification")

	if rules:
		frappe.log_error(
			title="Cliq: Notification channel withdrawn",
			message=(
				"This app no longer delivers over Frappe's Notification doctype. "
				"'Cliq' has been removed from the channel options, and these rules "
				"were disabled because their channel no longer exists:\n\n"
				+ "\n".join(f"  - {name}" for name in rules)
				+ "\n\nThey are disabled, not deleted, so nothing is lost. To get "
				"these alerts back, tick the doctype in Cliq Notification Settings "
				"instead: recipients then come from the doctype's own Workflow "
				"rather than from a rule's recipient list.\n\n"
				"Leaving them enabled was not an option -- the channel option is "
				"gone, so saving such a rule now fails validation."
			),
		)


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
