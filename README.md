# Britam Tax Reconciliation Portal – CETTP v1.1

Integrated internal portal for cancellation and endorsement tracking plus VAT and WHT reconciliation workflows.

## Features
- Initiator workflow for new cancellation/endorsement cases.
- Tax Compliance workspace protected by a configurable access code (default development code: `4823`).
- Tracking of original invoices, adjustment/VAT amounts, credit notes, replacement invoices, adjustment references, TRA status and acknowledgement.
- Workflow statuses, search/filter, case detail and audit history.
- Dashboard metrics and Chart.js visualizations.
- VAT and WHT reconciliation workspace with CSV/XLSX imports, sample templates, period filters, book-vs-TRA totals, row-level variances and deletion of imported rows.
- Generic reconciliation columns support VAT streams (e.g. General/Medical premiums, facultative inwards, commission receivable, fronted business, salvage and fees) and WHT streams (e.g. agents commissions and other payees) using the Source and Category fields.
- SQLite by default for development; PostgreSQL via `DATABASE_URL` in production.
- Flask-WTF CSRF protection, SQLAlchemy ORM and Flask-Migrate support.

## Local run
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
set FLASK_APP=app.py
flask run
```
Open http://127.0.0.1:5000.

## Render deployment
1. Push this folder to a Git repository.
2. Create a Render Blueprint from `render.yaml`.
3. Set `TAX_ACCESS_CODE` in Render environment variables. For production, use a strong secret code rather than the development default.
4. Confirm the managed PostgreSQL database is attached and `DATABASE_URL` is set.
5. Deploy and test `/health`, initiation, tax access, updates and history.

## Important production controls
- The requested no-username/password design is implemented as role selection plus a tax access code. This is a lightweight access gate, not user authentication. Anyone who knows the code can enter Tax Compliance and actions are not attributable to verified individuals.
- Do not use the default code `4823` in production. Set `TAX_ACCESS_CODE` through Render environment variables.
- Set a strong random `SECRET_KEY`; keep it private.
- For stronger auditability, add individual SSO/user accounts, role-based authorization, rate limiting, security monitoring, and a documented retention policy before handling sensitive customer/policy information.
- Database migrations: `flask db init` (once), `flask db migrate -m "initial schema"`, `flask db upgrade`. The application currently calls `db.create_all()` on startup for simple first-run development; for controlled production schema evolution, remove that startup call after initializing migrations and use release/deploy migration commands.


## Visual design refresh
The interface now uses a branded blue header, navy workspace sidebar, colorful KPI cards, a blue welcome banner, colored charts, polished forms, responsive layouts and updated navigation styling. The UI uses Bootstrap 5 and Chart.js via CDN, so an internet connection is required for those assets unless they are self-hosted.

## Reconciliation upload template
Download the VAT or WHT CSV template from the reconciliation screen. Required columns are `period`, `source`, `book_amount`, and `tra_amount`. Optional columns are `reference`, `counterparty`, `category`, and `notes`. XLSX uploads read the first worksheet and first row as column headers. Amounts are interpreted as TZS and row variance is Books minus TRA. Uploads append data; check for duplicates before importing.

## Security and tax review
This module provides reconciliation calculations and exception visibility, not automatic confirmation of TRA records or a substitute for tax review. Restrict access, protect the access code and secret key, back up the database, and verify source data before making filings.


## Premium Register Cancellation & Endorsement Tracker (added to the V1.1 base)

Tax Compliance users can open **Register Cancellations** from the sidebar and upload an AIMS Premium Register in standard, unprotected `.xlsx` or `.csv` format. The importer searches for the `POLICY NO` and `GROSS PREMIUM` headings, then groups positive and negative signed premiums by policy number. Negative rows are classified as: exact positive amount match, different positive amount on the same policy (review as a potential endorsement), or pure cancellation where no positive row exists for that policy.

Each detected negative transaction is retained in the tracker. Tax Compliance should record the reason, TRA status and TRA reference. The **Auto-fill matched figures / Import to CETTP** action creates linked records in the existing case workflow; selected rows are imported when checkboxes are selected, otherwise all unimported rows are imported. The Excel export includes the cancellation reason, classification, TRA status/reference and CETTP case reference.

**Important:** pairing is a suggested control based on policy number and premium amount; verify it against source documents before submitting a cancellation or endorsement to TRA. The uploaded Premium Register previously provided could not be opened by standard XLSX tools (it may be encrypted or not a standard XLSX). Save an accessible unprotected workbook or CSV export for importing. This enhancement does not change the V1.1 database configuration or add PostgreSQL persistence changes.


## Monthly Premium Register tracking (V1.1 enhancement)

In Tax Compliance, open **Premium Register Cancellations & Endorsements**. Select the reconciliation month and upload that month's AIMS Premium Register. Every upload is retained as a separate batch with a monthly history. Filter the register by month, export that month to Excel, or export all tracked months. The Excel report includes reporting month, matching classification, cancellation/endorsement reason, TRA status/reference and CETTP case reference. Re-uploading a month does not overwrite earlier batches.

See `CHANGELOG_V1_1_MONTHLY_REGISTER.md` for the enhancement notes. This release is based on the V1.1 enhancement package and does not intentionally add the V1.2 persistence changes.

### Duplicate prevention for cancellations and endorsements
The cancellation/endorsement workflow checks for repeated actions across initiator-created cases and Premium Register imports. It normalizes policy numbers and compares action type, date, document/invoice number and absolute adjustment amount. Exact duplicates are blocked or skipped with an alert. Where the policy already has related activity but the date/document/amount differs, the system raises a review warning rather than blocking it, because a policy may legitimately have more than one endorsement.

### Case and monthly tracking administration
- Tax Compliance can delete an individual case from the Case Register; linked Premium Register tracker rows are deleted with it. Deletion is permanent and requires confirmation.
- Tax Compliance can delete an individual Premium Register tracking row or delete a selected monthly upload batch from the monthly upload history.
- Deleting a batch removes its tracker rows and the CETTP cases that were created by that register import. Cases originally raised by an Initiator are preserved; their tracker links are removed with the batch.
- The Case Register provides an Excel export. Current search, workflow status, and request type filters are applied to the export.
- Destructive actions use POST requests protected by Flask-WTF CSRF.


## Automatic email alerts for new cancellation / endorsement requests

When an Initiator submits a new request through **New Request**, CETTP commits the case first and then attempts to email the configured shared Tax Compliance mailbox. Email failure does not cancel or roll back the case submission. Notifications are recorded in the `email_notification_log` database table with case reference, recipient, status (`sent`, `failed`, or `not_configured`), attempt count, timestamp, and error detail. A unique case/event constraint suppresses duplicate sends for the same newly initiated case. Premium Register imports do not trigger this new-Initiator-request email.

Configure these environment variables in the deployment environment (for example, Render Environment):

| Variable | Purpose |
|---|---|
| `CETTP_TAX_COMPLIANCE_EMAIL` | Required shared mailbox recipient. Multiple recipients may be separated by commas or semicolons. |
| `M365_SMTP_HOST` | Optional; defaults to `smtp.office365.com`. |
| `M365_SMTP_PORT` | Optional; defaults to `587` (STARTTLS). |
| `M365_SMTP_USERNAME` | Microsoft 365 mailbox username approved for SMTP submission. |
| `M365_SMTP_PASSWORD` | Secret credential/app password supplied by Britam IT, if permitted by policy. Store only as an environment secret. |
| `M365_SMTP_FROM` | Approved sender address; defaults to `M365_SMTP_USERNAME`. |
| `CETTP_BASE_URL` | Public CETTP base URL used to build the link in the email, e.g. `https://your-approved-cettp-host`. |

**Microsoft 365 / IT approval required:** This implementation uses authenticated SMTP submission with STARTTLS. Britam IT must confirm that SMTP AUTH is permitted for the sender mailbox and tenant, provide the approved credential method, and approve the sender/recipient configuration. If SMTP AUTH is disabled or organizational policy requires OAuth/Graph, this SMTP transport must be replaced with the approved Microsoft Graph mail-sending method; do not weaken tenant security to make SMTP work.

For local testing, set the variables in your shell or a local `.env` file loaded by your development setup. Do not commit credentials, and do not add real credentials to `.env.example`. The application logs send/failure events to its application logger and records each case's notification outcome in the database. Failed emails are not automatically retried to avoid duplicate delivery; check failed/not-configured records and correct configuration before implementing a controlled retry action.


## In-app Tax Compliance notifications (no email required)

New cancellation and endorsement requests now create an in-portal notification after the case is saved. Tax Compliance can view alerts from the bell icon or the **Notifications** sidebar link after logging in. Each alert shows the case reference, request type, policy, customer, initiator, and submission timestamp. The notification list distinguishes **Unread** from **Read** items; opening a notification records it as read for the current Tax Compliance actor, and **Mark all as read** is also available. The unread badge updates on page loads.

These notifications work without configuring Microsoft 365 or any email address. Email delivery remains optional and separate. The notification and per-actor read receipt records are created in the same application database as the other CETTP records. For a deployed database, ensure the app's startup `db.create_all()` can create the two new tables (`in_app_notification` and `in_app_notification_read`); for stricter production change control, create them through the project's migration process before deploying.
