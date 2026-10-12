"""Fire a real Cliq message through the app's own code path, from ERPNext.

Reads a real Leave Application; never writes to it.
"""
import frappe

frappe.init(site="site_local")
frappe.connect()
frappe.set_user("Administrator")

from zoho_cliq_integration.cliq.client import deliver, get_settings

s = get_settings()
print("settings: bot=%s domain=%s mode=%s dry_run=%s redirect=%s"
      % (s.bot_unique_name, s.api_domain, s.target_type, s.dry_run, s.test_redirect))

la = frappe.db.get_value(
    "Leave Application",
    {"workflow_state": "Pending Leave Approval", "leave_approver": ("!=", "")},
    "name", order_by="modified desc",
)
doc = frappe.get_doc("Leave Application", la)
print("rendering against %s (approver %s)" % (doc.name, doc.leave_approver))

n = frappe.get_doc("Notification", "Cliq - Leave Application Status")
before = frappe.db.count("Cliq Message Log")
n.send(doc)
frappe.db.commit()
print("log rows: %d -> %d" % (before, frappe.db.count("Cliq Message Log")))

log = frappe.get_last_doc("Cliq Message Log")
print("queued  : status=%s intended=%s target=%s"
      % (log.status, log.recipient_user, log.recipient_cliq))

# The worker would normally pick this up; run it inline so we see the result.
if log.status == "Queued":
    deliver(log.name)
    log.reload()

print()
print("RESULT   : %s" % log.status)
print("TARGET   : %s" % log.recipient_cliq)
print("RESPONSE : %s" % (log.response or "").replace("\n", " "))
print("ERROR    : %s" % (log.error or "-"))
print()
print("----- what was sent -----")
print("SUBJECT:", log.subject)
print(log.message)
print("-------------------------")
print("\n%s unchanged: modified=%s"
      % (doc.name, frappe.db.get_value("Leave Application", doc.name, "modified")))

frappe.destroy()
