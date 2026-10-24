# CETTP V1.1 — Case and Batch Administration

- Added Tax Compliance-only deletion for individual Case Register records.
- Deleting a case also removes linked Premium Register tracker rows to prevent stale tracking and re-import confusion.
- Added individual Premium Register tracker-row deletion.
- Added monthly upload batch deletion from upload history. It removes the selected batch's tracker rows and register-imported cases, while preserving cases originally initiated by users.
- Added Case Register Excel export, respecting current search, workflow-status, and request-type filters.
- Added confirmation prompts and CSRF-protected POST forms for destructive actions.
