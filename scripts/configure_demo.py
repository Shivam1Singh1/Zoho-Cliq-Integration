"""Point the app at the live bot and create the Leave Application rule.

Direct Message mode with a Test Redirect, because the bot is Personal and can
only DM its creator. Switch target_type to Channel once the channel unique name
is known -- nothing else needs to change.
"""
import sys

import frappe

TOKEN = sys.argv[1]
BOT = "erplocalsetupfor"
REDIRECT = "shivam.singh@microcrispr.com"
RULE = "Cliq - Leave Application Status"

# The real workflow states on this site, including the misspelling.
SENT = ["Pending Leave Approval", "Pending by Head Leave Approver"]
APPROVED = [
    "Approved by Leave Approver",
    "Approved By HR Head",
    "Approved by Head Leave Approver",
    "Approved",
]
REJECTED = ["Rejeted by Leave Approver", "Rejected by Head Leave Approver"]
WATCHED = SENT + APPROVED + REJECTED

frappe.init(site="site_local")
frappe.connect()
frappe.set_user("Administrator")

s = frappe.get_single("Cliq Settings")
s.enabled = 1
s.dry_run = 0
s.bot_unique_name = BOT
s.api_domain = "cliq.zoho.in"
s.zapikey = TOKEN
s.target_type = "Direct Message"
s.test_redirect = REDIRECT
s.identity_field = "user_id"
s.timeout = 15
s.max_attempts = 3
s.log_successful_sends = 1
s.save(ignore_permissions=True)
frappe.db.commit()
frappe.clear_cache(doctype="Cliq Settings")
print("Cliq Settings: bot=%s enabled=%s dry_run=%s redirect=%s"
      % (s.bot_unique_name, s.enabled, s.dry_run, s.test_redirect))

if frappe.db.exists("Notification", RULE):
    frappe.delete_doc("Notification", RULE, force=True, ignore_permissions=True)

condition = "doc.workflow_state in %r" % (WATCHED,)

n = frappe.get_doc({
    "doctype": "Notification",
    "name": RULE,
    "subject": "Leave {{ doc.workflow_state }} - {{ doc.employee_name }}",
    "document_type": "Leave Application",
    "channel": "Cliq",
    "event": "Value Change",
    "value_changed": "workflow_state",
    "condition": condition,
    "enabled": 1,
    "is_standard": 0,
    "message": (
        "<p><b>{{ doc.employee_name }}</b> - {{ doc.leave_type }}</p>"
        "<p>Status: <b>{{ doc.workflow_state }}</b><br>"
        "From {{ doc.from_date }} to {{ doc.to_date }} "
        "({{ doc.total_leave_days }} day(s))<br>"
        "Reason: {{ doc.description or 'Not given' }}</p>"
        "<p>Approver: {{ doc.leave_approver_name or doc.leave_approver or 'Not set' }}</p>"
    ),
    "recipients": [{"receiver_by_document_field": "leave_approver"}],
})
n.insert(ignore_permissions=True)
frappe.db.commit()

print("Rule created : %s (enabled=%s, event=%s on %s)"
      % (n.name, n.enabled, n.event, n.value_changed))
print("Watching %d states:" % len(WATCHED))
for st in WATCHED:
    print("   -", st)

frappe.destroy()
