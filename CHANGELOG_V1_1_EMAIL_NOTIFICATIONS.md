# CETTP V1.1 — New Request Email Notifications

- Added an `EmailNotificationLog` database table for sent, failed and not-configured notification attempts.
- New Initiator-submitted cancellation and endorsement cases trigger one email attempt to the shared Tax Compliance mailbox after the case is committed.
- Added duplicate-send prevention with a unique case/event key.
- Added Microsoft 365 SMTP submission via STARTTLS, configured only through environment variables.
- Email failures do not undo the request; status and bounded error details are recorded for follow-up.
- Added configurable public CETTP URL for the case link in the email.
- Premium Register batch imports do not trigger this new-Initiator-request notification.
- No WHT reconciliation functionality was added or changed as part of this enhancement.

Microsoft 365 SMTP AUTH availability and credential method must be confirmed by Britam IT. If SMTP AUTH is not allowed, integrate the approved Microsoft Graph method instead.
