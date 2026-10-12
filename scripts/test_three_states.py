"""Send one real Cliq message for each of the three leave scenarios.

Reads real Leave Applications in each state and never writes to any of them.
Also rewrites the rule's template so the wording matches what happened:
a pending leave reads as a request, not a status dump.
"""
import frappe

frappe.init(site="site_local")
frappe.connect()
frappe.set_user("Administrator")

from zoho_cliq_integration.cliq.client import deliver, get_settings

RULE = "Cliq - Leave Application Status"
SENT = ["Pending Leave Approval", "Pending by Head Leave Approver"]
APPROVED = ["Approved by Leave Approver", "Approved By HR Head",
            "Approved by Head Leave Approver", "Approved"]
REJECTED = ["Rejeted by Leave Approver", "Rejected by Head Leave Approver"]

# State-aware wording. The pending branch is the one Vinay triggers when he
# clicks Send For Approval, so it has to read as a request for action.
MESSAGE = (
    "{% set st = doc.workflow_state %}"
    "{% if st in ['Pending Leave Approval', 'Pending by Head Leave Approver'] %}"
    "<p><b>{{ doc.employee_name }}</b> has requested leave and needs your approval.</p>"
    "{% elif st.startswith('Approved') %}"
    "<p>Leave for <b>{{ doc.employee_name }}</b> was <b>approved</b>.</p>"
    "{% else %}"
    "<p>Leave for <b>{{ doc.employee_name }}</b> was <b>rejected</b>.</p>"
    "{% endif %}"
    "<p>Type: {{ doc.leave_type }}<br>"
    "From {{ doc.from_date }} to {{ doc.to_date }} "
    "({{ doc.total_leave_days }} day(s))<br>"
    "Reason: {{ doc.description or 'Not given' }}</p>"
    "<p>Stage: {{ st }}<br>"
    "Approver: {{ doc.leave_approver_name or doc.leave_approver or 'Not set' }}</p>"
)

# Subject is a Data field capped at 140 characters, and Jinja branching alone
# eats most of that budget. The state is already descriptive, so it goes in
# plain and the body carries the state-aware sentence.
SUBJECT = "{{ doc.workflow_state }} - {{ doc.employee_name }}"

n = frappe.get_doc("Notification", RULE)
n.subject = SUBJECT
n.message = MESSAGE
n.save(ignore_permissions=True)
frappe.db.commit()
print("template updated on %s\n" % RULE)

s = get_settings()
print("mode=%s  bot=%s  dry_run=%s  redirect=%s\n"
      % (s.target_type, s.bot_unique_name, s.dry_run, s.test_redirect))

for label, states in (("SEND FOR APPROVAL", SENT), ("APPROVED", APPROVED), ("REJECTED", REJECTED)):
    name = frappe.db.get_value(
        "Leave Application",
        {"workflow_state": ("in", states)},
        "name", order_by="modified desc",
    )
    if not name:
        print("%-18s no document in %s" % (label, states))
        continue

    doc = frappe.get_doc("Leave Application", name)
    before = frappe.db.count("Cliq Message Log")
    n.send(doc)
    frappe.db.commit()
    if frappe.db.count("Cliq Message Log") == before:
        print("%-18s %s -> nothing queued (duplicate guard)" % (label, name))
        continue

    log = frappe.get_last_doc("Cliq Message Log")
    if log.status == "Queued":
        deliver(log.name)
        log.reload()

    print("=" * 66)
    print("%s  |  %s  |  %s" % (label, name, doc.workflow_state))
    print("result : %s -> %s" % (log.status, log.recipient_cliq))
    if log.error:
        print("error  : %s" % log.error)
    print("-" * 66)
    print("SUBJECT:", log.subject)
    print(log.message)
    print()

print("=" * 66)
print("No Leave Application was modified.")
frappe.destroy()
