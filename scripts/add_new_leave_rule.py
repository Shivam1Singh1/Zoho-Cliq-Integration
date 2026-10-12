"""Add the rule that catches a leave the moment it is created.

Why a second rule is needed:

  * "Pending Leave Approval" is state idx=1 in the Leave Application workflow,
    which makes it the DEFAULT for a new document. Saving a new leave lands
    there directly -- there is no transition into it.

  * Frappe never evaluates a Value Change alert on insert. document.py is
    explicit about it:

        if not self.flags.in_insert and not self.flags.in_delete:
            # value change is not applicable in insert
            event_map["on_change"] = "Value Change"

So the existing Value Change rule correctly catches every later transition
(approve, reject, escalate to HOD) but can never catch the creation itself.
Event "New" maps to after_insert, which is exactly that moment.
"""
import frappe

frappe.init(site="site_local")
frappe.connect()
frappe.set_user("Administrator")

RULE = "Cliq - Leave Requested"
PENDING = ["Pending Leave Approval", "Pending by Head Leave Approver"]

MESSAGE = (
    "<p><b>{{ doc.employee_name }}</b> has requested leave and needs your approval.</p>"
    "<p>Type: {{ doc.leave_type }}<br>"
    "From {{ doc.from_date }} to {{ doc.to_date }} "
    "({{ doc.total_leave_days }} day(s))<br>"
    "Reason: {{ doc.description or 'Not given' }}</p>"
    "<p>Stage: {{ doc.workflow_state }}<br>"
    "Approver: {{ doc.leave_approver_name or doc.leave_approver or 'Not set' }}</p>"
)

if frappe.db.exists("Notification", RULE):
    frappe.delete_doc("Notification", RULE, force=True, ignore_permissions=True)

n = frappe.get_doc({
    "doctype": "Notification",
    "name": RULE,
    "subject": "Leave requested - {{ doc.employee_name }}",
    "document_type": "Leave Application",
    "channel": "Cliq",
    "event": "New",
    "condition": "doc.workflow_state in %r" % (PENDING,),
    "enabled": 1,
    "is_standard": 0,
    "message": MESSAGE,
    "recipients": [{"receiver_by_document_field": "leave_approver"}],
})
n.insert(ignore_permissions=True)
frappe.db.commit()
frappe.cache.hdel("notifications", "Leave Application")

print("created: %s  event=%s  enabled=%s" % (n.name, n.event, n.enabled))
print("condition: %s" % n.condition)
print()
print("Active Cliq rules on Leave Application:")
for r in frappe.get_all(
    "Notification",
    filters={"document_type": "Leave Application", "channel": "Cliq", "enabled": 1},
    fields=["name", "event", "value_changed"],
):
    print("   %-38s event=%-14s watches=%s" % (r.name, r.event, r.value_changed or "-"))

frappe.destroy()
