# V1.1 enhancement: Premium Register cancellation tracker

This package is based on the unchanged V1.1 CETTP application. It adds a separate Tax Compliance sidebar option for Premium Register cancellation and endorsement tracking.

## Added
- Import standard unprotected `.xlsx`/`.xlsm` or `.csv` AIMS Premium Register files by detecting the `POLICY NO` and `GROSS PREMIUM` columns.
- Pair negative and positive premium transactions by policy number; exact absolute amount matches are classified as `Matched amount`; a different positive amount is classified as a potential endorsement requiring review; no positive transaction on the policy is classified as `Pure cancellation`.
- Record a mandatory cancellation/endorsement reason, TRA status and TRA reference per negative premium record.
- Import selected or all unimported detected records into the existing CETTP Case workflow, retaining the detected amounts and classification in the tracker.
- Export the cancellation tracker to Excel with reasons, TRA statuses/references and linked case references.
- Download a CSV header template matching the AIMS Premium Register headings.

## Controls and limitations
- Pairing is a suggestion and must be checked against source documents before submission to TRA.
- The imported source file previously supplied could not be read as a standard XLSX, so use an unprotected Excel workbook or CSV export.
- This enhancement does not change the V1.1 database URI handling, PostgreSQL persistence behavior, Render configuration or `/health` behavior.
