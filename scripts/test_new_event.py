"""Prove the New-event rule fires on the after_insert hook.

Rather than creating a Leave Application -- which would hit leave balances and
also trip the three enabled EMAIL notifications, mailing real approvers -- this
takes an existing pending document and runs Frappe's own notification
dispatcher for the after_insert method. That is the identical code path a real
save takes, so if the Cliq rule fires here it fires on a real creation.
"""
import frappe

frappe.init(site="site_local")
frappe.connect()
frappe.set_user("Administrator")

# Safety check: nothing else may be listening on New, or this would send mail.
others = frappe.get_all(
    "Notification",
    filters={"document_type": "Leave Application", "enabled": 1, "event": "New"},
    fields=["name", "channel"],
)
print("enabled rules on event=New:")
for o in others:
    print("   %-38s channel=%s" % (o.name, o.channel))
if any(o.channel != "Cliq" for o in others):
    print("ABORT: a non-Cliq notification would also fire.")
    raise SystemExit(1)

name = frappe.db.get_value(
    "Leave Application",
    {"workflow_state": "Pending Leave Approval", "leave_approver": ("!=", "")},
    "name", order_by="modified desc",
)
doc = frappe.get_doc("Leave Application", name)
print("\nusing %s (state=%s, approver=%s)" % (doc.name, doc.workflow_state, doc.leave_approver))

# Clear any prior row so the duplicate guard does not mask the result.
frappe.db.delete("Cliq Message Log", {"reference_name": doc.name})
frappe.db.commit()

before = frappe.db.count("Cliq Message Log")

# The real dispatcher, with the real method name.
doc.flags.notifications = None
doc.flags.notifications_executed = []
doc.run_notifications("after_insert")
frappe.db.commit()

after = frappe.db.count("Cliq Message Log")
print("log rows: %d -> %d" % (before, after))

if after == before:
    print("\nFAIL: the New rule did not fire.")
    raise SystemExit(1)

from zoho_cliq_integration.cliq.client import deliver

log = frappe.get_last_doc("Cliq Message Log")
if log.status == "Queued":
    deliver(log.name)
    log.reload()

print("\nPASS: fired via after_insert")
print("rule     : %s" % log.notification)
print("result   : %s -> %s" % (log.status, log.recipient_cliq))
print("intended : %s" % log.recipient_user)
print("trigger  : %s" % log.trigger_state)
print("-" * 60)
print("SUBJECT:", log.subject)
print(log.message)
print("-" * 60)
print("\n%s unchanged: modified=%s"
      % (doc.name, frappe.db.get_value("Leave Application", doc.name, "modified")))

frappe.destroy()
