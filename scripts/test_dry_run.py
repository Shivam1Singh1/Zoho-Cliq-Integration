"""End-to-end check in dry run.

Reads a real Leave Application but never writes to it. The test Notification is
created with enabled=0 so it can never fire on a live save, and is deleted at
the end along with the log rows it produced.
"""
import frappe

frappe.init(site="site_local")
frappe.connect()
frappe.set_user("Administrator")

TEST_NOTIFICATION = "ZZ Cliq Test - Leave Pending"
results = []


def check(label, passed, detail=""):
    results.append((label, passed, detail))
    print("%-52s %s  %s" % (label, "PASS" if passed else "FAIL", detail))


# ---------------------------------------------------------------- 1. override
settings = frappe.get_single("Cliq Settings")
settings.enabled = 1
settings.dry_run = 1
settings.identity_field = "user_id"
settings.fallback_recipient = None
settings.save(ignore_permissions=True)
frappe.db.commit()

cls = frappe.get_doc({"doctype": "Notification"}).__class__
check("Notification class is overridden", cls.__name__ == "CliqNotification", cls.__name__)

# ------------------------------------------------- 2. a real pending document
la_name = frappe.db.get_value(
    "Leave Application",
    {"workflow_state": "Pending Leave Approval", "leave_approver": ("!=", "")},
    "name",
    order_by="modified desc",
)
check("Found a live pending Leave Application", bool(la_name), la_name or "none")
if not la_name:
    frappe.destroy()
    raise SystemExit(1)

doc = frappe.get_doc("Leave Application", la_name)
print("   using %s | approver=%s | state=%s" % (doc.name, doc.leave_approver, doc.workflow_state))

# ------------------------------------------------------- 3. test notification
if frappe.db.exists("Notification", TEST_NOTIFICATION):
    frappe.delete_doc("Notification", TEST_NOTIFICATION, force=True, ignore_permissions=True)

n = frappe.get_doc({
    "doctype": "Notification",
    "name": TEST_NOTIFICATION,
    "subject": "Leave approval needed: {{ doc.employee_name }}",
    "document_type": "Leave Application",
    "channel": "Cliq",
    "event": "Value Change",
    "value_changed": "workflow_state",
    "enabled": 0,                      # never fires on a real save
    "is_standard": 0,
    "message": (
        "<p><b>{{ doc.employee_name }}</b> has applied for leave.</p>"
        "<p>Type: {{ doc.leave_type }}<br>"
        "From {{ doc.from_date }} to {{ doc.to_date }} "
        "({{ doc.total_leave_days }} day(s))</p>"
    ),
    "recipients": [{"receiver_by_document_field": "leave_approver"}],
})
n.insert(ignore_permissions=True)
frappe.db.commit()
check("Notification saved with channel=Cliq", n.channel == "Cliq")

# ------------------------------------------------------------ 4. send, twice
from frappe.email.doctype.notification.notification import get_context

before = frappe.db.count("Cliq Message Log")
n.send(doc)
frappe.db.commit()
after_first = frappe.db.count("Cliq Message Log")
check("First send created a log row", after_first == before + 1, "%d -> %d" % (before, after_first))

n.send(doc)
frappe.db.commit()
after_second = frappe.db.count("Cliq Message Log")
check("Duplicate suppressed on second send", after_second == after_first,
      "%d -> %d" % (after_first, after_second))

# ------------------------------------------------------------ 5. inspect row
log = frappe.get_last_doc("Cliq Message Log")
check("Status is Dry Run", log.status == "Dry Run", log.status)
check("Recipient resolved", bool(log.recipient_cliq), log.recipient_cliq or "")
check("Trigger state captured", log.trigger_state == doc.workflow_state, log.trigger_state or "")
check("Subject rendered (no Jinja left)", "{{" not in (log.subject or ""), log.subject or "")
check("Message rendered as text (no HTML)", "<p>" not in (log.message or ""))
check("Block tags became line breaks", "leave.Type" not in (log.message or "")
      and "LeaveFrom" not in (log.message or ""))
check("No HTML entities left", "&amp;" not in (log.message or "")
      and "&nbsp;" not in (log.message or ""))
check("Link appended", "/leave-application/" in (log.message or "").lower() or "http" in (log.message or ""))
print("\n----- rendered message -----")
print("SUBJECT:", log.subject)
print(log.message)
print("----------------------------\n")

# ------------------------------------------------ 6. no recipient + fallback
n2_name = "ZZ Cliq Test - No Recipient"
if frappe.db.exists("Notification", n2_name):
    frappe.delete_doc("Notification", n2_name, force=True, ignore_permissions=True)
n2 = frappe.get_doc({
    "doctype": "Notification", "name": n2_name,
    "subject": "No recipient test", "document_type": "Leave Application",
    "channel": "Cliq", "event": "Value Change", "value_changed": "workflow_state",
    "enabled": 0, "is_standard": 0, "message": "<p>body</p>",
    "recipients": [{"receiver_by_document_field": "custom_field_that_does_not_exist"}],
})
n2.insert(ignore_permissions=True)

fb_user = frappe.db.get_value("User", {"enabled": 1, "user_type": "System User",
                                       "name": ("not in", ("Administrator", "Guest"))}, "name")
settings.reload()
settings.fallback_recipient = fb_user
settings.save(ignore_permissions=True)
frappe.db.commit()
frappe.clear_cache(doctype="Cliq Settings")

c_before = frappe.db.count("Cliq Message Log")
n2.send(doc)
frappe.db.commit()
c_after = frappe.db.count("Cliq Message Log")
check("Empty approver routed to fallback", c_after == c_before + 1, "fallback=%s" % fb_user)
if c_after > c_before:
    fb_log = frappe.get_last_doc("Cliq Message Log")
    check("Fallback recipient on the row", fb_log.recipient_user == fb_user, fb_log.recipient_cliq or "")

# ------------------------------------------------------- 7. disabled = silent
settings.reload()
settings.enabled = 0
settings.save(ignore_permissions=True)
frappe.db.commit()
frappe.clear_cache(doctype="Cliq Settings")

d_before = frappe.db.count("Cliq Message Log")
n.send(doc)
frappe.db.commit()
check("Disabled settings send nothing", frappe.db.count("Cliq Message Log") == d_before)

# ---------------------------------------------------------------- 8. cleanup
for name in (TEST_NOTIFICATION, n2_name):
    frappe.db.delete("Cliq Message Log", {"notification": name})
    if frappe.db.exists("Notification", name):
        frappe.delete_doc("Notification", name, force=True, ignore_permissions=True)

settings.reload()
settings.enabled = 0
settings.dry_run = 1
settings.fallback_recipient = None
settings.save(ignore_permissions=True)
frappe.db.commit()

print("\nLeave Application %s unchanged: modified=%s" % (
    doc.name, frappe.db.get_value("Leave Application", doc.name, "modified")))

failed = [r for r in results if not r[1]]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
for label, _p, detail in failed:
    print("  FAILED:", label, detail)

frappe.destroy()
