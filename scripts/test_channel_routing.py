"""The override is site-wide, so prove the other channels still dispatch.

Nothing is actually sent: the channel methods are replaced on the instance with
recorders, so this asserts routing only.
"""
import frappe

frappe.init(site="site_local")
frappe.connect()
frappe.set_user("Administrator")

from frappe.email.doctype.notification.notification import get_context

results = []


def check(label, passed, detail=""):
    results.append((label, passed))
    print("%-54s %s  %s" % (label, "PASS" if passed else "FAIL", detail))


doc = frappe.get_doc(
    "Leave Application",
    frappe.db.get_value("Leave Application", {}, "name", order_by="modified desc"),
)
context = get_context(doc)

for channel, expected in (
    ("Email", "send_an_email"),
    ("Slack", "send_a_slack_msg"),
    ("SMS", "send_sms"),
    ("System Notification", "create_system_notification"),
    ("Cliq", "send_a_cliq_msg"),
):
    n = frappe.get_doc({
        "doctype": "Notification",
        "subject": "routing probe",
        "document_type": "Leave Application",
        "channel": channel,
        "event": "Value Change",
        "value_changed": "workflow_state",
        "enabled": 0,
        "is_standard": 0,
        "message": "<p>probe</p>",
        "recipients": [{"receiver_by_document_field": "leave_approver"}],
    })  # never inserted -- in memory only

    called = []
    for meth in ("send_an_email", "send_a_slack_msg", "send_sms",
                 "create_system_notification", "send_a_cliq_msg"):
        setattr(n, meth, (lambda m: lambda *a, **k: called.append(m))(meth))

    n.send_notification_by_channel(doc, context)
    check(f"channel {channel:<20} -> {expected}", called == [expected], str(called))

check("Override class in use", frappe.get_doc({"doctype": "Notification"}).__class__.__name__
      == "CliqNotification")

failed = [r for r in results if not r[1]]
print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
frappe.destroy()
