"""Replacements for core Frappe document classes.

Registered through `override_doctype_class` in hooks.py. Each override
subclasses the original and changes as little as possible, delegating to
super() for every path it does not specifically handle.
"""
