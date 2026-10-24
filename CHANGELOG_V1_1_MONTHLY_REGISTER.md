# V1.1 Monthly Premium Register Tracking Enhancement

- Added a required reconciliation-month selector to Premium Register uploads.
- Added a persistent monthly upload history with source file, transaction counts, negative-premium count, exact-match count, different-amount count, pure-cancellation count, uploader and upload timestamp.
- Each upload receives its own batch identifier. Uploading a month again creates a separate retained batch; it does not overwrite earlier tracking records.
- Added month filtering and per-month Excel exports, with an all-month export option.
- Excel exports include the reporting month, cancellation/endorsement reason, TRA status/reference, and CETTP case reference.
- Implemented the history in a new database table so the existing V1.1 `CancellationTrack` and `Case` tables are not changed. `db.create_all()` creates the new table at application startup.
- No V1.2 persistence hardening changes were intentionally added.

## Duplicate cancellation / endorsement controls
- Added duplicate-action detection across initiator-created CETTP cases and Premium Register tracking records.
- Exact duplicate actions are identified using normalized policy number plus action type, effective/transaction date, document/invoice number, and absolute adjustment amount.
- Initiator submissions are stopped with a clear alert when an exact duplicate already exists; related actions on the same policy are flagged for review without blocking legitimate separate endorsements.
- Premium Register uploads skip exact duplicates already in CETTP, in prior register uploads, or repeated within the same upload, and display a summary alert.
- Import-to-CETTP avoids creating a second case for an action already initiated and links the tracker row to the existing CETTP case when available.
