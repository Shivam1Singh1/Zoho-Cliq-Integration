"""Re-apply the install-time setup by hand.

`after_install` and `after_migrate` already do this, so you only need it when
the app was installed before setup.py existed, or to repair the Property
Setter that puts "Cliq" in the Notification channel list. Safe to re-run --
both steps are idempotent.

    cd sites && ../env/bin/python ../apps/zoho_cliq_integration/scripts/apply_setup.py
"""

import frappe

frappe.init(site="site_local")
frappe.connect()

from zoho_cliq_integration.setup import add_cliq_channel, seed_settings

add_cliq_channel()
seed_settings()
frappe.db.commit()

meta = frappe.get_meta("Notification")
print("channel options now:", repr(meta.get_field("channel").options))

s = frappe.get_single("Cliq Settings")
print("settings -> enabled=%s dry_run=%s domain=%s identity=%s attempts=%s" % (
    s.enabled, s.dry_run, s.api_domain, s.identity_field, s.max_attempts))

frappe.destroy()
