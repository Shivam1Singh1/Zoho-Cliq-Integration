"""App manifest.

Everything this app adds to a site is declared at the bottom of this file: the
Notification class override that gives Cliq its own delivery channel, and the
install hooks that register "Cliq" as a channel option on the Notification
doctype. The commented blocks in between are Frappe's scaffolding, left as
generated.
"""

app_name = "zoho_cliq_integration"
app_title = "Zoho Cliq Integration"
app_publisher = "MicroCRISPR"
app_description = "Send ERPNext document alerts as Zoho Cliq direct messages"
app_email = "vinay.harane@microcrispr.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "zoho_cliq_integration",
# 		"logo": "/assets/zoho_cliq_integration/logo.png",
# 		"title": "Zoho Cliq Integration",
# 		"route": "/zoho_cliq_integration",
# 		"has_permission": "zoho_cliq_integration.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/zoho_cliq_integration/css/zoho_cliq_integration.css"
# app_include_js = "/assets/zoho_cliq_integration/js/zoho_cliq_integration.js"

# include js, css files in header of web template
# web_include_css = "/assets/zoho_cliq_integration/css/zoho_cliq_integration.css"
# web_include_js = "/assets/zoho_cliq_integration/js/zoho_cliq_integration.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "zoho_cliq_integration/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "zoho_cliq_integration/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "zoho_cliq_integration.utils.jinja_methods",
# 	"filters": "zoho_cliq_integration.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "zoho_cliq_integration.install.before_install"
# after_install = "zoho_cliq_integration.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "zoho_cliq_integration.uninstall.before_uninstall"
# after_uninstall = "zoho_cliq_integration.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "zoho_cliq_integration.utils.before_app_install"
# after_app_install = "zoho_cliq_integration.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "zoho_cliq_integration.utils.before_app_uninstall"
# after_app_uninstall = "zoho_cliq_integration.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "zoho_cliq_integration.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"zoho_cliq_integration.tasks.all"
# 	],
# 	"daily": [
# 		"zoho_cliq_integration.tasks.daily"
# 	],
# 	"hourly": [
# 		"zoho_cliq_integration.tasks.hourly"
# 	],
# 	"weekly": [
# 		"zoho_cliq_integration.tasks.weekly"
# 	],
# 	"monthly": [
# 		"zoho_cliq_integration.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "zoho_cliq_integration.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "zoho_cliq_integration.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "zoho_cliq_integration.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["zoho_cliq_integration.utils.before_request"]
# after_request = ["zoho_cliq_integration.utils.after_request"]

# Job Events
# ----------
# before_job = ["zoho_cliq_integration.utils.before_job"]
# after_job = ["zoho_cliq_integration.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"zoho_cliq_integration.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []



# Zoho Cliq Integration
# ---------------------

# Cliq is added as a Notification channel rather than as a parallel rule
# engine, so conditions, recipients, templating and the Value Change trigger
# all come from Frappe's own Notification doctype.
override_doctype_class = {
	"Notification": "zoho_cliq_integration.overrides.notification.CliqNotification"
}

# A Leave Application's workflow state is announced to one fixed person, who
# is named in Cliq Settings. That recipient has nothing to do with the document
# -- not the applicant, not an approver -- so a Notification rule cannot resolve
# it and this is a document hook instead.

# `on_update` alone is not enough. This workflow approves across the submit
# boundary: Pending -> Approved by Leave Approver submits the document (which
# does run on_update), but Approved by Leave Approver -> Approved By HR Head is
# a change to an allow-on-submit field on an already-submitted document, and
# that only ever reaches on_update_after_submit. The same guarded handler takes
# all three, and the has_value_changed check makes the extra paths free.
_LEAVE_STATE_HOOK = "zoho_cliq_integration.events.leave_application.on_update"

# The table-driven side: Cliq Notification Settings lists which doctypes send a
# message on a workflow state change, and recipients come from each doctype's
# own Workflow. Hence the wildcard -- the configured list is data, not code, so
# the hook cannot name the doctypes up front. The handler's first act is a
# cached membership test, so an unconfigured doctype costs one cache read.
#
# The three events are not interchangeable. `on_update` misses a transition
# between two submitted states, which is how an allow-on-submit workflow field
# changes (Approved by Leave Approver -> Approved By HR Head reaches only
# on_update_after_submit), and cancellation reaches only on_cancel.
_WORKFLOW_HOOK = "zoho_cliq_integration.workflow_notify.on_update"

doc_events = {
	"*": {
		"on_update": _WORKFLOW_HOOK,
		"on_update_after_submit": _WORKFLOW_HOOK,
		"on_cancel": _WORKFLOW_HOOK,
	},
	"Leave Application": {
		"on_update": _LEAVE_STATE_HOOK,
		"on_update_after_submit": _LEAVE_STATE_HOOK,
		"on_cancel": _LEAVE_STATE_HOOK,
	},
}

after_install = "zoho_cliq_integration.setup.after_install"
after_migrate = "zoho_cliq_integration.setup.after_migrate"
