# Zoho Cliq Integration

Send ERPNext workflow alerts into Zoho Cliq — as direct messages to the people a
workflow is waiting on, or as a post into a shared channel.

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

A document changes workflow state. The people who now have to act on it get a
Cliq message, and so does the person who handed it on. Nobody tells the person
who just acted, because they already know.

**There is exactly one way in, and no doctype is named anywhere in this app.**
You tick a doctype in **Cliq Notification Settings**; from then on its
creation and every workflow-state change on it are announced, and who to tell is
read off that doctype's own active Workflow. Adding a doctype is configuration,
never code.

That single entry point is deliberate. Two earlier paths were removed because
each one knew something it had no business knowing:

| Removed path | Why it went |
|---|---|
| `events/leave_application.py` | Named one doctype in code and messaged one fixed person from settings. Nothing about it generalised. |
| `overrides/notification.py` | Added `Cliq` as a channel on Frappe's Notification doctype — a second, parallel place to configure the same integration, with its own recipient list that could disagree with the Workflow. |

If you are looking for the Notification-rule behaviour, see
[Withdrawing the Notification channel](#withdrawing-the-notification-channel)
below — the upgrade path is a tick in Cliq Notification Settings.

## Architecture

```
TRIGGER                   DECIDE (who)       QUEUE               DELIVER
────────────────────────  ─────────────────  ──────────────────  ──────────────
any save, any doctype  →  is it ticked in    background worker   _post() → Zoho
(doc_events["*"])         Cliq Notification                   →
                          Settings?       →  the doctype's
                                             active Workflow
```

Two properties hold on every path:

**A save is never blocked.** The HTTP call happens in a worker, at least one hop
away from the document's own transaction. A slow or dead Cliq cannot delay or
fail a user's save.

**A save is never failed.** Every hook body is wrapped; failures go to the Error
Log and are never raised. An outbound message is not a reason for an employee's
leave request to break.

## Verified Zoho API behaviour

Both of these were found against a live bot, not assumed, and both shape the
code:

**`userids` must be a comma-separated string.** Sent as a JSON array the API
returns HTTP 400 with an empty body — every time, with no error text to work
from.

**HTTP 200 does not mean delivered.** An address Cliq cannot resolve comes back
200 with `{"user_ids": [], "users": []}` — accepted, and delivered to nobody.
Success therefore requires a non-empty `user_ids`, which is why `Undelivered`
exists as a status distinct from `Failed`. Treating that response as `Sent` would
hide every address mismatch in the company behind a green tick.

---

## Module notes

### `workflow_notify.py` — who to tell

Registered against `doc_events["*"]`, so this runs on every document save on the
site. The first two lines of `on_update` are therefore the hot path: one cache
read for the enabled list, then one in-memory field comparison. Nothing else
happens for a doctype that is not ticked.

That wildcard is what keeps the app doctype-agnostic. The alternative — naming
doctypes in `hooks.py` — would mean a code change and a restart for every new
one, which is exactly what this app no longer does.

Who gets told comes entirely from the doctype's own active Workflow, so one rule
serves every doctype:

- **forward** — whoever must act on the state the document just entered. Read off
  the transitions leaving that state. A state with no outgoing transitions is
  final, and nobody is waiting.
- **reply** — whoever made the previous transition, so the person who handed the
  document on learns what became of it.
- **creation** — the one person the workflow names as having to act on the state
  the document was born into.

With three levels: 1→2 tells L2; 2→3 tells L3 and replies to L1; the final
approval replies to L2. The person who just acted is never told.

**A creation is not a transition, and `get_creation_recipients()` is separate
because of it.** There is no previous state, so there is no reply recipient; and
the role fan-out is a *fallback* here rather than an addition. A state a document
is born into is typically reachable both by the one approver a condition names
and by a role that may hold a hundred people — on this site, creating a leave
resolves to **one** recipient through the condition, where the same state
*entered by a transition* resolves to 21. Announcing a brand-new document to all
21 would be noise, and the named approver is the person who actually has to act.
Roles are consulted only when no transition out of that state narrows it to
anybody.

Both paths live in `on_update`, which Frappe runs on insert as well. A new
document is told apart by `get_doc_before_save()` returning `None` — that absent
snapshot *is* the signal, and it used to be an early `return`, which is why
creation went unannounced until this was added.

**Condition parsing.** A transition whose condition names a document field — the
common `doc.leave_approver == frappe.session.user` shape — identifies one
specific person who must act. Notifying the whole role in that case would be
wrong as well as loud: on this site 125 users hold Leave Approver and exactly one
of them can move the document.

**`MAX_ROLE_RECIPIENTS = 10`.** A backstop for roles with no such condition.
Exceeding it is logged, never silently truncated — a capped fan-out that looked
complete would be worse than a loud one.

**Deduplication.** One transition saves the document more than once — seven to
eight hook invocations were observed for a single approval, two of them for the
document itself — so without this the same message is sent twice. A `job_id`
alone does *not* dedupe: `frappe.enqueue` takes `deduplicate=False` by default.

**Identity comparison uses the email, not just the user id.** The same person can
arrive as a User id from a role and as a differently-cased id from a transition
condition, and only the address is a reliable identity.

**`get_reply_recipient()` — three sources, in order, and the order is the whole
point.** The obvious two are both unreliable here:

| Source | Why it is not enough alone |
|---|---|
| `Version` | Only exists where the doctype has `track_changes` on. Leave Application on this site does not, so it returns nothing. |
| `Workflow Action` | `completed_by` is filled in when Frappe closes out the action, and a leftover Open action gets attributed to whoever triggered the *next* step — observed here as the Pending Leave Approval row reading `completed_by` = the head approver after they acted. Useful when it agrees, never trusted alone. |
| `before.modified_by` | The pre-save snapshot — whoever last saved the document, which is exactly the previous actor. |

The last one **must** come off `before` and not off `doc`:
`set_user_and_timestamp()` has already rewritten `doc.modified_by` to the current
user by the time an `on_update` hook runs, so reading it from `doc` yields the
person who just acted, and the reply is then dropped as self-notification.

**`get_actor_from_workflow_actions()`** skips the current actor, which is what
makes it the *previous* transition rather than this one — whose row is written
during this same save.

**`Administrator` is never a recipient.** It is a shared login, not a person with
a Cliq account.

### `cliq_sender.py` — one workflow-state message

Renders and delivers a workflow-state message to every recipient in one call.
Runs in a background worker: `workflow_notify` enqueues it after commit.

The HTTP call is delegated to `cliq.client._post`. That matters more than it
looks — re-implementing the call here would rediscover both Zoho findings above
the hard way.

Bot name, zapikey and data-centre domain are read from Cliq Settings, and are
deliberately not duplicated onto Cliq Notification Settings: one integration, one
place for its credentials.

**`build_plans()` picks a target per mode.** In channel mode `_post` treats its
target argument as a channel name and puts it in the URL; handing it a
comma-joined list of emails there would build a nonsense request. So each mode
gets its own target, and the log records what that route actually sent to while
`intended` still records the real recipients.

**`resolved_user_ids()` returns `None`, not `[]`, for a body it cannot parse.**
The caller treats `None` as "no verdict" and leaves the send counted as
delivered. An empty list means Zoho explicitly matched nobody; conflating the two
would cry wolf on every response shape Zoho ever changes.

**`record()` never raises.** Losing the audit row must not lose the message. The
outcome goes to Cliq Message Log, and it keeps the API's own verdict.
`frappe.logger` would not: Frappe's default level is WARNING and an `info()` call
is dropped on the floor — which is exactly how the first run of this looked
delivered while leaving no trace at all.

**`fit()`.** Joins addresses into a `Data(140)` field without losing the count.
Plain truncation reads as a complete list in the log while quietly dropping names
off the end — which is how a nine-recipient fan-out appeared to stop at six. The
full list is in `response`.

**`state_label()` and the creation wording.** An empty `old_state` means a
creation. Rendered naively that produces `" -> Pending"`, which reads as a value
that went missing, so a creation is labelled `(new) -> Pending` instead — and
since `trigger_state` is the duplicate guard's key, that label has to be stable
rather than merely tidy. The message headline likewise says
`Leave Application created: …` and credits the raiser rather than an actioner,
because on a creation nobody has acted yet.

**`build_message()`** uses one format for every doctype, so nothing here is
doctype-specific. `applicant` is whatever the document calls a person —
`employee_name` where it exists — and is simply omitted when the doctype has no
such field.

### `cliq/client.py` — delivery of a single message

Two entry points:

- **`queue_message()`** runs inside the caller's own transaction. It writes a
  Cliq Message Log row per delivery mode and enqueues each send. Nothing here
  touches the network.
- **`deliver()`** runs in a background worker and performs the HTTP call.

Since the Notification channel was withdrawn, the only caller of `queue_message`
is `send_test_message()`. The retry, duplicate-guard and logging machinery is
kept rather than folded into `cliq_sender`, because the test button is the one
thing that must prove the *whole* delivery path — settings, identity mapping,
both modes and the real HTTP call — before anyone trusts it in production.

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

### Doctypes

**Cliq Notification Settings** — the only place a doctype is named. Lists which
doctypes send a Cliq message when their workflow state changes.
`get_enabled_doctypes()` is read on every document save on the site, because the
event handler is registered against `doc_events["*"]`. It must therefore cost one
cache read and nothing else — never a query. `on_update` drops the cache, which
is what makes an unchecked box or a deleted row take effect on the next save
rather than on the next restart.

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
went out on.

`clear_old_logs()` is a scheduled trim: successful and dry-run rows age out;
**failures are kept**.

---

## Configuration

Two doctypes, and they answer different questions.

**Cliq Notification Settings — *which doctypes*.** One row per doctype, each with
an `enabled` tick. This is the whole of "what does this app watch".

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

Nothing a user authored is deleted. To get those alerts back, tick the rule's
doctype in Cliq Notification Settings. The difference to expect:

| | Notification rule | Cliq Notification Settings |
|---|---|---|
| Recipients | a list you maintain on the rule | the doctype's active Workflow |
| Message | your Jinja template | one built-in format for every doctype |
| Triggers | any event, plus a condition | creation and workflow-state change |

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

Then, in the site: fill in **Cliq Settings**, tick the doctypes you want in
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
