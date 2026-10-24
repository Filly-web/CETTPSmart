# V1.1 enhancement — In-app Tax Compliance notifications

- Added in-app alerts when a new cancellation or endorsement request is submitted.
- Added a Tax Compliance notification center with unread/read labels and timestamps.
- Added per-actor read receipts, mark-one-as-read/open-case, and mark-all-as-read actions.
- Added unread count badge to the navigation bar and sidebar.
- In-app notifications work without an email address or Microsoft 365 configuration.
- Email remains optional and separate; request submission is retained if email delivery is not configured or fails.
- Added `InAppNotification` and `InAppNotificationRead` database tables through the existing `db.create_all()` startup setup.
