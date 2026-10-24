# CETTP V1.1 – Case Supporting Documents

## Added
- Case-level supporting-document register for cancellation and endorsement requests.
- Upload categories: Original invoice; TRA cancellation note/approval; Credit note; Corrected/replacement invoice; Other supporting document.
- Upload during initial request submission and later from the case details page.
- Multiple files per category; PDF, PNG, JPG/JPEG, XLS/XLSX, DOC/DOCX and CSV accepted.
- 12 MB per-file limit and 40 MB total request limit.
- Documents stored as binary data in the configured SQL database, avoiding dependence on Render's ephemeral local filesystem.
- Authenticated-by-role download routes and Tax Compliance-only removal from the active case list.
- Upload metadata includes original filename, category, uploader, UTC timestamp, file size and MIME type.
- Upload/removal actions are written to the case audit history. Removal is a soft-delete from the active list; the binary and audit metadata are retained.
- Existing requests can receive supporting documents after submission. TRA notes and credit notes are not required at initiation because they may only be available later.

## Deployment note
The new `case_attachment` table is created by the application's existing `db.create_all()` startup initialization. For production environments using managed migrations, create and apply a migration for the new table before deployment. Back up the database regularly because file contents are stored in it.

## Security note
The feature uses the portal's existing role/access model. Before production use with sensitive documents, configure a strong `SECRET_KEY` and `TAX_ACCESS_CODE`, restrict access to authorised users, and confirm the organisation's document retention policy. Extension filtering is applied; this is not a malware scanner.
