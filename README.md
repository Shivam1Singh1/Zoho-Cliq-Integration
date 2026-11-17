# Zoho Cliq Integration

Send a Zoho Cliq message to the project manager a Material Request is waiting
on — as a direct message, or as a post into a shared channel.

This file is the app's design documentation. Every non-obvious decision in the
code is recorded here, grouped by the module it belongs to. The one module that
keeps its commentary inline is `hooks.py`, because what it declares *is* the
wiring and is best read in place.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Verified Zoho API behaviour](#verified-zoho-api-behaviour)
- [Module notes](#module-notes)
- [Configuration](#configuration)
- [Message statuses](#message-statuses)
- [Installation](#installation)

---

## What it does

A Material Request enters **Pending from Project Manager**. The project manager
it is now waiting on gets one Cliq message. That is the entire behaviour.

**One doctype, one state, one direction.**

| | |
|---|---|
| Doctype | Material Request, and nothing else |
| State | `Pending from Project Manager`, and no other |
| Direction | outbound only — to the approver, never back to the requester |

The last row is a deliberate constraint, not an omission. An approval, a
rejection, a send-back and a cancellation all pass in silence: the requester
either performed the action or will see it on the document, and a message
saying so is noise. There is no template for any of them.

**The switch is a row, not a deploy.** The handler will not fire unless
`Material Request` is listed in **Cliq Notification Settings**. Remove the row
and the app goes quiet from the next save onward; the row is seeded on install
and on the first migrate of a site that has no list at all.

Three earlier paths were removed or retired because each one sent more than
this:

| Retired path | Why it went |
|---|---|
| `events/leave_application.py` | Named one doctype in code and messaged one fixed person from settings. Deleted. |
| `overrides/notification.py` | Added `Cliq` as a channel on Frappe's Notification doctype — a second, parallel place to configure the same integration. Deleted. |
| `workflow_notify.py` + `cliq_sender.py` | A wildcard rule on `doc_events["*"]` that announced every workflow-state change on every doctype listed in Cliq Notification Settings, and the sender it enqueued. Deleted. |
| `purchase_order_notify.py` + `Cliq Purchase Approver` | A Purchase Order handler keyed by COM/SER/IT branch, and the approver table it read. Deleted, table empty. |

The last two were unhooked first and kept dormant for a while. That turned out
to be the worse of the two options: code nothing calls is code nobody reads,
and it still has to be understood by whoever comes next. Git has them.

If you are looking for the Notification-rule behaviour, see
[Withdrawing the Notification channel](#withdrawing-the-notification-channel)
below.

## Architecture

```
TRIGGER                   DECIDE (who)        QUEUE               DELIVER
────────────────────────  ──────────────────  ──────────────────  ──────────────
Material Request save  →  state now 'Pending  background worker   _post() → Zoho
(on_update,               from Project     →  one message,     →
 on_update_after_submit)  Manager'?           one recipient

                          the project manager:
                            1. MR.custom_project_head_name  (if an address)  ← the one that answers
                            2. MR.custom_project_lead_name  (if an address)
                            3. Project.custom_project_lead  (Link → Employee)
                            4. Project.custom_project_lead_name (if an address)
                            5. Employee Function.project_list[project].project_head
```

Two properties hold on every path:

**A save is never blocked.** The HTTP call happens in a worker, at least one hop
away from the document's own transaction. A slow or dead Cliq cannot delay or
fail a user's save.

**A save is never failed.** Every hook body is wrapped; failures go to the Error
Log and are never raised. An outbound message is not a reason for an employee's
leave request to break.

## Verified Zoho API behaviour

All of these were found against a live bot, not assumed, and each one shapes the
code:

**`userids` must be a comma-separated string.** Sent as a JSON array the API
returns HTTP 400 with an empty body — every time, with no error text to work
from.

**A button label is capped at 20 characters.** Longer and the whole message is
rejected with HTTP 400 `{"code": "input_maxlength_reached"}` — the card does not
degrade, nothing is delivered. `"Open MAT-MR-2026-03832"` is 22 and was the first
thing that failed, which is why `BUTTON_LABEL` is a fixed string rather than a
template and is truncated to `BUTTON_LABEL_LIMIT` regardless.

**`card`, `slides` and `buttons` sit beside `text`, not inside it.** They are
top-level keys of the same JSON body. `text` is still worth sending with a card:
it is the line rendered above it, and it is also what the chat-list preview and
the push notification show, where the card is not rendered at all.

**A button's URL is opened by the reader's browser, not by Cliq.** It must
therefore be an address that resolves from *their* machine. Frappe builds it from
`host_name` in `site_config.json`, and with that unset `get_url_to_form` falls
back to the site's own name — producing `http://site_local/app/...`, which is a
valid-looking link that opens nothing. The button is only as good as `host_name`.

**HTTP 200 does not mean delivered.** An address Cliq cannot resolve comes back
200 with `{"user_ids": [], "users": []}` — accepted, and delivered to nobody.
Success therefore requires a non-empty `user_ids`, which is why `Undelivered`
exists as a status distinct from `Failed`. Treating that response as `Sent` would
hide every address mismatch in the company behind a green tick.

---

## Module notes

### `notifications/` — one module per doctype

```
notifications/
  __init__.py          what the package is
  common.py            resolution, rendering and queueing, shared
  material_request.py  the only handler, and the only doctype
```

**A doctype's file holds everything about that doctype** — the states it reacts
to, how it finds the person to tell, and every word it can say. Nothing about
one doctype is spread across two files, and nothing in `common.py` names a
doctype. Adding one is a new file here plus a line in `hooks.py`; touching one
cannot disturb another.

The wording still lives apart from the logic, but as a fenced block at the top
of the file rather than a second module. Nothing below that banner is logic and
nothing above it is a sentence, so rewording is still a change to one place —
without the import round-trip that two files cost.

### `notifications/common.py` — what they share

Nothing in here names a doctype, reads a doctype-specific field, or contains a
sentence a user will read:

| | |
|---|---|
| `state_transition()` | did the workflow state actually move? Returns `(old, new)` or `None` |
| `enqueue_job()` | hand the transition to a worker, exactly once |
| `deliver()` | turn a plan into log rows and queued messages |
| `resolve_email()` | a User link, an Employee id or an address → an address |
| `dedupe()` | one message per person, never one back to the actor |
| `render()` | template + context → message body |
| `form_url()`, `state_label()` | a link that opens from outside; the duplicate-guard key |

Only one handler uses these today, which makes the split look like ceremony.
It stays because it is the seam a second doctype gets added along, and because
every function here would serve that one unchanged.

**`state_transition()` orders its checks by cost.** `has_value_changed` is an
in-memory field comparison, so a save that did not touch the workflow state —
the overwhelming majority — costs that and nothing else; only then is the
before-snapshot read. A document created directly into a state has no `before`,
and that is a real transition with an empty `old_state`, not a non-event.

**`deliver(..., key_by_audience=)`** decides whether the audience goes into the
duplicate-guard key. A handler that sends two messages for one transition needs
it — in Channel mode both resolve to the same channel, and without it the guard
swallows the second. Material Request sends one message and passes it off, which
keeps the log readable; Purchase Order sends several and sets it.

### `notifications/material_request.py` — the one handler that runs

Registered against `doc_events["Material Request"]` on both `on_update` and
`on_update_after_submit`. Every state in `Purchase Requisition Matrix` is
doc_status 0 except the two terminal approvals, and `apply_workflow` reaches
those through `doc.submit()`, which runs `on_update` before `on_submit` — so
`on_update` alone already sees the transition. The second event costs one
`has_value_changed` call and covers any future allow-on-submit edit. There is
no `on_cancel`: cancelling tells the requester nothing they did not just do.

The guard runs in cheapening order and nothing escapes it:

1. a cached membership test against **Cliq Notification Settings**
2. `has_value_changed(workflow_state)` — an in-memory field comparison
3. `new_state == "Pending from Project Manager"` — a string equality
4. the before-snapshot differs, so a re-save of the same state is silent

**Finding the project manager is the only hard part.** The approver is not in
the Workflow. `Purchase Requisition Matrix` gates the transition by role, and
`Projects Manager` has 23 holders — the role says who *may* act, never who
*must*. The address is on the document instead, and five sources are tried in
turn, nearest the document first:

| # | Source | Why it is where it is |
|---|---|---|
| 1 | `Material Request.custom_project_head_name` | **The one that answers.** The form fills this with the project manager's address. Across 17,700 documents it is the only field that ever holds an email. |
| 2 | `Material Request.custom_project_lead_name` | The **Project Lead Name** field, and a trap: it is the obvious-looking one and it holds an email in **zero** of those 17,700 records. It stays as a fallback and must never be first. |
| 3 | `Project.custom_project_lead` | `Link → Employee`, so it resolves to a real address. Set on 24 of 190 projects here; all 24 resolve. |
| 4 | `Project.custom_project_lead_name` | The same free-text field one level up, for a document whose copy was never fetched. |
| 5 | `Employee Function.project_list[project].project_head` | The route this module used originally. Needs the document to carry an Employee Function *and* that function to map a head for this exact project. |

Ordering 1 and 2 the wrong way round is not hypothetical — it shipped, and
every notification silently skipped. `MAT-MR-2026-03944` carried
`bhanu.bisht@microcrispr.com` in field 1 while the code read field 2, found a
blank, walked a chain that was also blank, and logged
`Skipped - No Recipient`. The trigger was never at fault, which is exactly what
made it hard to see.

**Exactly one person, always.** Every branch returns on first match, so a
project mapped in two places cannot produce two messages — the nearest source
wins and the rest are never read. Audited across all 17,700 documents: no
record resolves to more than one recipient, and no two sources disagree on who
the manager is.

None resolving is a **configuration gap, not a failure** — it lands in Cliq
Message Log as `Skipped - No Recipient`, naming every place that was looked at,
and never reaches the Error Log.

**Every message records which source named the recipient**, in the log's
`resolved_via` field: `Material Request.custom_project_head_name`, or
`Employee Function VP-LTP-PDV-001.project_head[PLTP-2025-0045]`. An approver
who says "I'm not the manager for this" can be answered from the row rather
than argued with.

**`resolve_email()` tries four places** because the leads above are
`Link → User` *or* `Link → Employee`, and several hold employee numbers with no
User record behind them. It falls through the Employee fields in the order this
site actually populates them: `user_id` (696/710), `company_email` (686),
`prefered_email` (683).

**Deduplication.** One transition saves the document more than once — seven to
eight hook invocations were observed for a single approval — so without this
the same message goes out twice. A `job_id` alone does *not* dedupe:
`frappe.enqueue` takes `deduplicate=False` by default. `queue_message` then
guards again on `(reference, trigger_state, target)`, which is what makes the
worker idempotent.

**The actor is never the recipient.** In practice the project manager is never
the person who moved the document into this state, but a direct edit by them
would otherwise bounce straight back.

**`Administrator` is never a recipient.** It is a shared login, not a person
with a Cliq account.

**The message is a Cliq card, not a paragraph.** The text half is one bold line
— `"{mr}: your approval needed"` — and everything the approver needs is in the
card under it: the document's own title, then `CARD_ROWS` as a two-column table,
then one button to the form.

The line above the card deliberately says nothing the card repeats. An earlier
draft read *"MAT-MR-… raised by Developer is pending your approval"* while the
table already carried a `Created By` row — the same fact twice, in the two places
a reader looks first.

`CARD_ROWS` *is* the card. Adding or reordering a row is one line in that tuple
and nothing else; the label is what Cliq shows and the fieldname is read off the
document. An empty value is dropped rather than rendered blank — a row reading
`Project Description` with nothing after it is noise, and on a phone every row
dropped is one more real row visible without scrolling.

The `slides` type is `table`, with `CARD_HEADERS` naming the two columns. The
alternative, `label`, renders the same pairs as a vertical list with no header
row. Both were sent to a live bot and compared side by side; the table won.

One row is not on the reference card: **Remark**, appended when the transition
carried one. At this point in the chain that remark is whatever the previous
approver wrote on the way past, and the person now being asked to approve is the
one who benefits from reading it. It is absent when there is none, so the card is
otherwise unchanged.

### `cliq/client.py` — delivery of a single message

Two entry points:

- **`queue_message()`** runs inside the caller's own transaction. It writes a
  Cliq Message Log row per delivery mode and enqueues each send. Nothing here
  touches the network.
- **`deliver()`** runs in a background worker and performs the HTTP call.

`queue_message` has two callers: `notifications/common.deliver()`, which is how
every per-doctype handler sends, and `send_test_message()`. The second matters
as much as the first — the test button is the one thing that proves the *whole*
delivery path (settings, identity mapping, both modes and the real HTTP call)
before anyone trusts it in production.

**Retry policy.** `RETRYABLE_STATUS = (408, 429, 500, 502, 503, 504)` — worth
another try, because the far end was busy, throttled, or briefly unavailable.
Anything else (a bad token, an unknown bot, an unresolvable recipient) fails
identically on every retry, so it is settled on the first attempt.

**Duplicate guard.** `LIVE_STATUSES = ("Queued", "Sent", "Dry Run",
"Undelivered")` — a message already in one of these states for this exact
combination of document, trigger state and target is not sent twice. This is what
makes a re-save of an unchanged document harmless even if something upstream
calls us more than once.

The guard matches an absent `trigger_state` with `("is", "not set")`, not with
`""`. An absent value is stored as SQL `NULL`, and `NULL = ''` is never true, so
filtering on the empty string matched nothing and the guard silently did not
apply at all.

**`resolve_identity()`.** Recipients arrive as ERPNext User ids, which are login
emails. Whether that is also the Cliq login depends on the site, so
`identity_field` in Cliq Settings decides. When no Employee record matches, the
address is used unchanged — better to attempt delivery than to drop the message
silently. Addresses are stripped: Employee rows carry stray whitespace often
enough to matter, and two active records on this site have a leading space or a
trailing tab. An unstripped address matches nothing at the Cliq end.

**`test_redirect`.** A Personal bot can only DM its creator, so a redirect is the
only way to see anything at all while testing. The intended recipient is still
recorded, so the log shows who the message would have gone to in production.

**`enqueue_after_commit=True`.** Nothing leaves the building for a transaction
that later rolls back.

**`_post()` — the single place that knows Zoho's request shape.** Direct messages
go through the bot endpoint with `userids` as a comma-separated string; channel
posts go through `channelsbyname`, where the channel is in the path and no
recipient is needed. Both authenticate with the same zapikey. The mode is passed
in explicitly rather than read from settings, so one call can be a DM while the
next is a channel post.

```
DM      →  /api/v2/bots/{bot_unique_name}/message    + {"text": …, "userids": "a@x,b@x"}
Channel →  /api/v2/channelsbyname/{channel}/message  + {"text": …}
```

**`_resolved_anyone()`.** Returns `True` for an unparseable body on a 2xx: assume
delivered rather than cry wolf.

**`send_test_message()`.** Sends one message on demand to prove the configuration
works end to end. Honours Dry Run, so it is safe to call before going live, and
runs `deliver()` in the foreground so the caller sees the real outcome.

### `setup.py` — install and upgrade wiring

This app makes **no schema change to any core doctype**. It used to add `Cliq` to
Notification's channel options with a Property Setter; `after_migrate` now takes
that back out again.

**`remove_cliq_channel()` disables the rules before it removes the option.** Order
matters: once `Cliq` is gone from the options, a Notification still set to that
channel fails validation on its next save, so leaving such a rule enabled would
strand it. They are disabled rather than deleted — the rule, its condition and
its message template are all still there to read, and nothing a user authored is
destroyed.

Only a Property Setter whose value actually contains `Cliq` is deleted. Another
app may legitimately own one on the same field, and deleting that would take its
channel out with ours.

It stays in `after_migrate` rather than being a one-shot patch so that a site
restored from an older backup is cleaned up too. After the first run it is two
cheap queries that find nothing.

**`seed_settings()`** creates the Single with safe defaults: off, and in dry run.

**`seed_notification_doctype()`** puts `Material Request` in the list, once, on
a site whose list is empty. The handler will not fire for a doctype that is not
listed, so an empty table means nothing is ever sent — and a silent install is
worse than a wrong one. A table with anything in it is left alone, so an
administrator who clears the row has said no and is not overruled on the next
migrate.

### Doctypes

**Cliq Notification Settings** — the on/off switch, and the only place a
doctype is named. Only `Material Request` does anything; listing anything else
is inert now that the wildcard rule is unhooked. `get_enabled_doctypes()` is
read on every Material Request save, so it must cost one cache read and nothing
else — never a query. `on_update` drops the cache, which is what makes an added
or deleted row take effect on the next save rather than on the next restart.

It is **deliberately not whitelisted**: it is called from the document hook, never
from the client, and exposing it would hand any logged-in user a list of which
doctypes are wired to an outbound integration.

**Cliq Notification Doctype** (child table) — Frappe does not run `validate` on
child rows, only the parent's, so the parent invokes `validate_row` once per row.
The checks still live with the doctype they belong to rather than being hoisted
into the parent controller.

A doctype with no active Workflow produces a **warning rather than an error**: a
workflow may legitimately be added after the row, and refusing the save would be
the wrong way round.

**Cliq Settings** — the single place this app is configured. Holds the bot
credentials, whether messages go out as direct messages or as a channel post, and
how an ERPNext user maps to a Cliq login. Two switches keep it safe: `enabled`
stops everything, and `dry_run` renders and logs messages without calling Zoho.

Validation guards the one dangerous transition — **going live with an incomplete
bot configuration**. A half-filled bot would otherwise fail silently, one Error
Log row per notification. A domain pasted with its scheme is stripped rather than
rejected, since it still works everywhere else in Frappe.

**Cliq Message Log** — one row per message, successful or not. This is both the
audit trail and the duplicate guard. `delivery_mode` records which route the row
went out on, and `card_data` holds the structured half of the payload — the
`card`, `slides` and `buttons` that went out beside `text`. It is empty for a
plain-text message, which is what makes the card optional: a handler with
nothing structured to say omits it and the payload is byte-for-byte what it was
before cards existed. A row whose `card_data` will not parse degrades to a
plain-text send rather than failing delivery.

`clear_old_logs()` is a scheduled trim: successful and dry-run rows age out;
**failures are kept**.

---

## Configuration

Two doctypes, and they answer different questions.

**Cliq Notification Settings — *which doctypes*.** One row per doctype. The row's
presence is the switch — there is nothing to tick. This is the whole of "what
does this app watch".

**Cliq Settings — *how to send*.**

| Field | Holds |
|---|---|
| `enabled` | Master switch |
| `dry_run` | Render, route and log — no network call at all |
| `bot_unique_name` | The bot that sends direct messages |
| `zapikey` | Incoming webhook token, stored encrypted |
| `api_domain` | `cliq.zoho.in` or `cliq.zoho.com` — the account's region |
| `target_type` | `Direct Message`, `Channel`, or `Both` |
| `channel_unique_name` | The channel's slug, not its display title |
| `identity_field` | `user_id` or `company_email` — how an ERPNext user maps to Cliq |
| `test_redirect` | Send everything to one address instead |
| `timeout`, `max_attempts` | HTTP behaviour |
| `log_successful_sends` | Keep or discard `Sent` rows |

**A direct message needs an organisation-scoped bot.** A Personal bot is accepted
by the API and delivered to nobody.

**A channel post needs the zapikey's owner to be a member of that channel,**
because `channelsbyname` posts as that user, not as the bot.

### Delivery modes

`target_type` chooses where a single event is announced. `get_modes()` turns it
into the list of routes to take:

| `target_type` | Routes taken |
|---|---|
| `Direct Message` | one DM per resolved recipient |
| `Channel` | one post into `channel_unique_name` |
| `Both` | both of the above, for the same event |

`Both` is not a third endpoint — it is the same two calls, made for one event.
Each route gets **its own Cliq Message Log row**, stamped with `delivery_mode`, so
a DM that was `Undelivered` and a channel post that was `Sent` stay visible
separately rather than being averaged into one verdict.

The duplicate guard keys on the target, so a DM row and a channel row for the
same document and state never suppress one another.

The `user_ids` check applies to direct messages only. A channel post has no
recipient to resolve, so a 2xx is the whole verdict available — which is why
`Undelivered` can never appear on a channel row.

### Withdrawing the Notification channel

Earlier versions added `Cliq` as a channel on Frappe's Notification doctype. That
is gone, and `bench migrate` cleans up after it: the Property Setter is deleted,
and any Notification rule still set to channel `Cliq` is **disabled** and named in
an Error Log entry so the change is not silent.

Nothing a user authored is deleted. There is no longer a like-for-like
replacement, though: this app now sends one message, for one doctype, in one
state. The difference to expect:

| | Notification rule | This app |
|---|---|---|
| Recipients | a list you maintain on the rule | the project manager, resolved from the document |
| Message | your Jinja template | one built-in format |
| Triggers | any event, plus a condition | Material Request entering `Pending from Project Manager` |

The recipient row is the reason for the change: a hand-maintained list drifts out
of step with the workflow it is supposed to mirror, and then the wrong person is
told — or nobody is. Reading the Workflow cannot drift, because it *is* the thing
that decides who may act.

What you give up is real: no per-rule Jinja template, and no trigger other than a
creation or a state change. If you need either, the Notification doctype still
does it over Email, Slack or System Notification.

**`Administrator` is the one account this app cannot notify**, on any path — it is
a shared login, not a person with a Cliq account, so it is filtered out as both
an actor and a recipient. A transition performed by Administrator into a final
state therefore tells nobody at all, correctly but confusingly. Test with a real
user.

## Message statuses

| Status | Meaning |
|---|---|
| `Queued` | Written to the log, waiting for a worker |
| `Sent` | 2xx **and** Zoho resolved at least one user |
| `Undelivered` | 2xx, but `user_ids` was empty — no Cliq account, or the bot may not DM that address |
| `Failed` | Non-2xx, or retries exhausted |
| `Dry Run` | Rendered, routed and logged; no network call |
| `Skipped` | Settings disabled mid-flight |

---

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app zoho_cliq_integration
```

Then, in the site: fill in **Cliq Settings**, list the doctypes you want in
**Cliq Notification Settings**, and leave `dry_run` on until the Send Test
Message button comes back `Sent`.

## Contributing

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/zoho_cliq_integration
pre-commit install
```

Configured tools: ruff, eslint, prettier, pyupgrade.

## License

mit
