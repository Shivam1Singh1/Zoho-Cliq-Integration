"""Post one message into a Cliq channel, through the app's own code path.

    bench --site site_local execute ... is not used here: this is a plain
    script so it can take the channel slug on the command line.

        python scripts/send_channel_test.py <channel_unique_name> [--dry] [--keep] [token]

    --dry   render, route and log -- no network call at all
    --keep  leave Cliq Settings in Channel mode afterwards (default: restore)
    token   a different zapikey; omitted, the one already stored is reused

Channel mode posts as the OWNER OF THE ZAPIKEY, not as the bot, so that user
must be a member of the channel. A non-member is refused with a non-2xx.
"""
import sys

import frappe

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = {a for a in sys.argv[1:] if a.startswith("--")}

if not args:
    sys.exit("usage: send_channel_test.py <channel_unique_name> [--dry] [--keep] [token]")

CHANNEL = args[0].strip().lstrip("#")
TOKEN = args[1].strip() if len(args) > 1 else None
DRY = "--dry" in flags
KEEP = "--keep" in flags

frappe.init(site="site_local")
frappe.connect()
frappe.set_user("Administrator")

from zoho_cliq_integration.cliq.client import deliver, queue_message

s = frappe.get_single("Cliq Settings")
was = {
    "target_type": s.target_type,
    "channel_unique_name": s.channel_unique_name,
    "dry_run": s.dry_run,
    "test_redirect": s.test_redirect,
}
print("before : mode=%s channel=%s dry_run=%s redirect=%s"
      % (was["target_type"], was["channel_unique_name"], was["dry_run"], was["test_redirect"]))

s.enabled = 1
s.target_type = "Channel"
s.channel_unique_name = CHANNEL
s.dry_run = 1 if DRY else 0
# Channel mode ignores it, but a leftover redirect is confusing in the log.
s.test_redirect = ""
if TOKEN:
    s.zapikey = TOKEN
s.save(ignore_permissions=True)
frappe.db.commit()
frappe.clear_cache()

print("sending: domain=%s channel=%s dry_run=%s" % (s.api_domain, CHANNEL, s.dry_run))
print("url    : https://%s/api/v2/channelsbyname/%s/message" % (s.api_domain, CHANNEL))

log_name = queue_message(
    recipient_user=None,
    subject="ERPNext integration test",
    message="Hello from ERPNext. This is a connectivity test for the Zoho Cliq integration.",
    reference_doctype="Cliq Settings",
    reference_name=frappe.generate_hash(length=10),
    trigger_state="channel-test",
)

if not log_name:
    print("RESULT : nothing queued -- settings disabled, or this exact message was already sent")
else:
    log = frappe.get_doc("Cliq Message Log", log_name)
    if log.status == "Queued":
        # The worker would pick this up; run it inline so the result is visible.
        deliver(log_name)
        log.reload()
    print()
    print("RESULT : %s" % log.status)
    print("target : %s" % log.recipient_cliq)
    if log.error:
        print("error  : %s" % log.error)
    if log.response:
        print("response:\n%s" % log.response)
    print()
    print("message sent:")
    print("-" * 60)
    print(log.message)
    print("-" * 60)

if KEEP:
    print("\nCliq Settings left in Channel mode on '%s'." % CHANNEL)
else:
    s = frappe.get_single("Cliq Settings")
    for field, value in was.items():
        setattr(s, field, value)
    s.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache()
    print("\nCliq Settings restored to %s mode. Re-run with --keep to make it stick."
          % was["target_type"])
