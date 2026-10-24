import os
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import StringField, SelectField, DateField, DecimalField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Optional, Length, NumberRange
from flask_migrate import Migrate
from sqlalchemy import or_, func
from werkzeug.utils import secure_filename
from io import BytesIO
import csv, io, smtplib, ssl, logging
from email.message import EmailMessage
from decimal import Decimal, InvalidOperation
from sqlalchemy import UniqueConstraint

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key-before-production")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("cettp.notifications")
database_url = os.environ.get("DATABASE_URL", "sqlite:///cettp.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024  # 40 MB total request size
ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "xlsx", "xls", "doc", "docx", "csv"}
MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024  # 12 MB per file
ATTACHMENT_CATEGORIES = [
    ("original_invoice", "Original invoice"),
    ("tra_cancellation_note", "TRA cancellation note / approval"),
    ("credit_note", "Credit note"),
    ("replacement_invoice", "Corrected / replacement invoice"),
    ("other", "Other supporting document"),
]
db = SQLAlchemy(app)
csrf = CSRFProtect(app)
migrate = Migrate(app, db)

STATUSES = [
    "Initiated", "Under Tax Review", "Returned for Clarification",
    "Approved for TRA Processing", "Submitted to TRA", "TRA Approved",
    "TRA Rejected", "Closed"
]
TYPES = [("Cancellation", "Policy Cancellation"), ("Endorsement", "Policy Endorsement")]
TRA_STATUSES = ["Not Submitted", "Pending", "Approved", "Rejected", "Not Applicable"]

class Case(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(30), unique=True, nullable=False, index=True)
    case_type = db.Column(db.String(30), nullable=False)
    policy_number = db.Column(db.String(80), nullable=False, index=True)
    customer_name = db.Column(db.String(160), nullable=False, index=True)
    customer_tin = db.Column(db.String(40))
    product = db.Column(db.String(100))
    branch = db.Column(db.String(100))
    underwriter = db.Column(db.String(120))
    reason = db.Column(db.Text, nullable=False)
    effective_date = db.Column(db.Date, nullable=False)
    original_invoice_no = db.Column(db.String(100))
    original_invoice_amount = db.Column(db.Numeric(16,2), default=0)
    adjustment_amount = db.Column(db.Numeric(16,2), default=0)
    vat_amount = db.Column(db.Numeric(16,2), default=0)
    credit_note_no = db.Column(db.String(100))
    replacement_invoice_no = db.Column(db.String(100))
    adjustment_reference = db.Column(db.String(100))
    tra_status = db.Column(db.String(40), default="Not Submitted")
    tra_reference = db.Column(db.String(100))
    workflow_status = db.Column(db.String(50), default="Initiated", nullable=False)
    initiator_name = db.Column(db.String(120), nullable=False)
    tax_owner = db.Column(db.String(120))
    tax_notes = db.Column(db.Text)
    closure_notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    closed_at = db.Column(db.DateTime)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case.id"), nullable=False, index=True)
    actor_role = db.Column(db.String(30), nullable=False)
    actor_name = db.Column(db.String(120))
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    case = db.relationship("Case", backref=db.backref("audit_logs", lazy=True, cascade="all, delete-orphan"))


class EmailNotificationLog(db.Model):
    """Audit trail for notification attempts; retained even if the case is later deleted."""
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, nullable=False, index=True)
    case_reference = db.Column(db.String(30), nullable=False, index=True)
    event_key = db.Column(db.String(40), nullable=False, default="new_request")
    recipient = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    sent_at = db.Column(db.DateTime)
    __table_args__ = (UniqueConstraint("case_id", "event_key", name="uq_email_notification_case_event"),)


class InAppNotification(db.Model):
    """In-portal alert created when a new cancellation/endorsement case is submitted."""
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case.id", ondelete="CASCADE"), nullable=False, index=True)
    case_reference = db.Column(db.String(30), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    case = db.relationship("Case", backref=db.backref("in_app_notifications", lazy=True, cascade="all, delete-orphan"))


class InAppNotificationRead(db.Model):
    """Per Tax Compliance actor read receipt; unread state is individual to the logged-in reviewer."""
    id = db.Column(db.Integer, primary_key=True)
    notification_id = db.Column(db.Integer, db.ForeignKey("in_app_notification.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_name = db.Column(db.String(120), nullable=False, index=True)
    read_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    notification = db.relationship("InAppNotification", backref=db.backref("read_receipts", lazy=True, cascade="all, delete-orphan"))
    __table_args__ = (UniqueConstraint("notification_id", "actor_name", name="uq_inapp_notification_actor_read"),)


class ReconciliationItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tax_type = db.Column(db.String(10), nullable=False, index=True)  # VAT or WHT
    period = db.Column(db.String(20), nullable=False, index=True)
    source = db.Column(db.String(80), nullable=False)
    reference = db.Column(db.String(120), index=True)
    counterparty = db.Column(db.String(180), index=True)
    category = db.Column(db.String(120))
    book_amount = db.Column(db.Numeric(16,2), nullable=False, default=0)
    tra_amount = db.Column(db.Numeric(16,2), nullable=False, default=0)
    notes = db.Column(db.Text)
    uploaded_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def variance(self):
        return Decimal(str(self.book_amount or 0)) - Decimal(str(self.tra_amount or 0))

class RegisterUpload(db.Model):
    """Monthly Premium Register upload history; stored separately to preserve existing V1.1 tables."""
    id = db.Column(db.Integer, primary_key=True)
    upload_batch = db.Column(db.String(80), unique=True, nullable=False, index=True)
    period = db.Column(db.String(7), nullable=False, index=True)  # YYYY-MM
    file_name = db.Column(db.String(255), nullable=False)
    source_rows = db.Column(db.Integer, default=0, nullable=False)
    negative_count = db.Column(db.Integer, default=0, nullable=False)
    matched_count = db.Column(db.Integer, default=0, nullable=False)
    different_count = db.Column(db.Integer, default=0, nullable=False)
    pure_count = db.Column(db.Integer, default=0, nullable=False)
    uploaded_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class CancellationTrack(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    upload_batch = db.Column(db.String(80), index=True)
    transaction_date = db.Column(db.Date)
    document_no = db.Column(db.String(120), index=True)
    policy_number = db.Column(db.String(80), nullable=False, index=True)
    endorsement_no = db.Column(db.String(120))
    insured_name = db.Column(db.String(180))
    product = db.Column(db.String(120))
    branch_name = db.Column(db.String(120))
    transaction_type = db.Column(db.String(100))
    negative_amount = db.Column(db.Numeric(16,2), nullable=False, default=0)
    matched_positive_amount = db.Column(db.Numeric(16,2), nullable=False, default=0)
    matched_document_no = db.Column(db.String(120))
    classification = db.Column(db.String(80), nullable=False)
    cancellation_reason = db.Column(db.Text)
    tra_status = db.Column(db.String(40), nullable=False, default="Not Submitted")
    tra_reference = db.Column(db.String(120))
    case_id = db.Column(db.Integer, db.ForeignKey("case.id"), index=True)
    imported_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    case = db.relationship("Case", foreign_keys=[case_id])

class CaseAttachment(db.Model):
    """Case-level document stored in the database to avoid ephemeral-host filesystem loss."""
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case.id", ondelete="CASCADE"), nullable=False, index=True)
    category = db.Column(db.String(40), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(120), nullable=False, default="application/octet-stream")
    file_size = db.Column(db.Integer, nullable=False)
    file_data = db.Column(db.LargeBinary, nullable=False)
    uploaded_by = db.Column(db.String(120), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    case = db.relationship("Case", backref=db.backref("attachments", lazy=True, cascade="all, delete-orphan"))


class CaseForm(FlaskForm):
    case_type = SelectField("Request type", choices=TYPES, validators=[DataRequired()])
    policy_number = StringField("Policy number", validators=[DataRequired(), Length(max=80)])
    customer_name = StringField("Customer / insured name", validators=[DataRequired(), Length(max=160)])
    customer_tin = StringField("Customer TIN", validators=[Optional(), Length(max=40)])
    product = StringField("Product / class", validators=[Optional(), Length(max=100)])
    branch = StringField("Branch", validators=[Optional(), Length(max=100)])
    underwriter = StringField("Underwriter", validators=[Optional(), Length(max=120)])
    initiator_name = StringField("Initiator name", validators=[DataRequired(), Length(max=120)])
    reason = TextAreaField("Reason for cancellation / endorsement", validators=[DataRequired(), Length(max=4000)])
    effective_date = DateField("Effective date", validators=[DataRequired()], format="%Y-%m-%d")
    original_invoice_no = StringField("Original invoice number", validators=[Optional(), Length(max=100)])
    original_invoice_amount = DecimalField("Original invoice amount (TZS)", validators=[Optional(), NumberRange(min=0)], places=2, default=0)
    adjustment_amount = DecimalField("Adjustment amount (TZS)", validators=[Optional()], places=2, default=0)
    vat_amount = DecimalField("VAT adjustment (TZS)", validators=[Optional()], places=2, default=0)
    submit = SubmitField("Submit request")

class TaxForm(FlaskForm):
    workflow_status = SelectField("Workflow status", choices=[(s,s) for s in STATUSES], validators=[DataRequired()])
    tra_status = SelectField("TRA status", choices=[(s,s) for s in TRA_STATUSES], validators=[DataRequired()])
    tax_owner = StringField("Tax owner", validators=[Optional(), Length(max=120)])
    tra_reference = StringField("TRA reference / acknowledgement", validators=[Optional(), Length(max=100)])
    credit_note_no = StringField("Credit note number", validators=[Optional(), Length(max=100)])
    replacement_invoice_no = StringField("Replacement invoice number", validators=[Optional(), Length(max=100)])
    adjustment_reference = StringField("Adjustment reference", validators=[Optional(), Length(max=100)])
    tax_notes = TextAreaField("Tax review notes", validators=[Optional(), Length(max=4000)])
    closure_notes = TextAreaField("Closure notes", validators=[Optional(), Length(max=2000)])
    submit = SubmitField("Save tax update")

def log_action(case, action, details=""):
    db.session.add(AuditLog(case_id=case.id, actor_role=session.get("role", "Unknown"),
                            actor_name=session.get("actor_name", ""), action=action, details=details))

def _notification_recipients():
    """Read shared mailbox recipient(s) from environment; allow semicolon/comma separated addresses."""
    raw = os.environ.get("CETTP_TAX_COMPLIANCE_EMAIL", "").strip()
    return [address.strip() for address in raw.replace(";", ",").split(",") if address.strip()]


def send_new_request_notification(case):
    """Log and send one notification per initiated case. Email failure never rolls back a submitted case."""
    recipients = _notification_recipients()
    recipient_label = ", ".join(recipients) if recipients else ""

    # A unique case/event record prevents duplicate sends if this function is invoked again.
    existing = EmailNotificationLog.query.filter_by(case_id=case.id, event_key="new_request").first()
    if existing:
        logger.info("New-request notification already logged for %s (status=%s)", case.reference, existing.status)
        return existing.status == "sent"

    if not recipients:
        log = EmailNotificationLog(case_id=case.id, case_reference=case.reference,
            event_key="new_request", recipient="NOT_CONFIGURED", status="not_configured",
            attempts=0, error_message="Set CETTP_TAX_COMPLIANCE_EMAIL to the shared Tax Compliance mailbox.")
        db.session.add(log)
        db.session.commit()
        logger.warning("Email notification not configured for case %s", case.reference)
        return False

    log = EmailNotificationLog(case_id=case.id, case_reference=case.reference,
        event_key="new_request", recipient=recipient_label, status="pending", attempts=0)
    db.session.add(log)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Another worker may have inserted the unique case/event row concurrently.
        existing = EmailNotificationLog.query.filter_by(case_id=case.id, event_key="new_request").first()
        if existing:
            logger.info("Concurrent duplicate notification suppressed for %s", case.reference)
            return existing.status == "sent"
        logger.exception("Unable to create notification log for %s", case.reference)
        return False

    host = os.environ.get("M365_SMTP_HOST", "smtp.office365.com").strip()
    try:
        port = int(os.environ.get("M365_SMTP_PORT", "587"))
    except ValueError:
        port = 587
    username = os.environ.get("M365_SMTP_USERNAME", "").strip()
    password = os.environ.get("M365_SMTP_PASSWORD", "")
    sender = os.environ.get("M365_SMTP_FROM", username).strip()

    if not username or not password or not sender:
        log.status = "not_configured"
        log.error_message = "Missing M365_SMTP_USERNAME, M365_SMTP_PASSWORD, or M365_SMTP_FROM environment variable."
        db.session.commit()
        logger.warning("Microsoft 365 SMTP settings incomplete for case %s", case.reference)
        return False

    subject = f"CETTP – New {case.case_type} Request – {case.reference}"
    amount = f"TZS {case.adjustment_amount:,.2f}" if case.adjustment_amount is not None else "Not provided"
    effective_date = case.effective_date.strftime("%d %b %Y") if case.effective_date else "Not provided"
    body = f"""A new cancellation/endorsement request has been submitted in Britam CETTP and requires Tax Compliance review.

Case reference: {case.reference}
Request type: {case.case_type}
Policy number: {case.policy_number}
Customer / insured: {case.customer_name}
Adjustment amount: {amount}
Effective date: {effective_date}
Initiator: {case.initiator_name}
Submitted (UTC): {case.created_at.strftime('%Y-%m-%d %H:%M UTC') if case.created_at else datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
Workflow status: {case.workflow_status}

Reason:
{case.reason or 'Not provided'}

Open the request in CETTP after signing in: {os.environ.get('CETTP_BASE_URL', request.url_root.rstrip('/')).rstrip('/')}{url_for('case_detail', case_id=case.id)}

This is an automated notification. Please do not reply to this message.
"""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipients[0]
    if len(recipients) > 1:
        message["Cc"] = ", ".join(recipients[1:])
    message.set_content(body)

    log.attempts = 1
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(username, password)
            server.send_message(message)
        log.status = "sent"
        log.sent_at = datetime.utcnow()
        log.error_message = None
        db.session.commit()
        logger.info("New-request email sent for %s to %s", case.reference, recipient_label)
        return True
    except Exception as exc:
        db.session.rollback()
        # Re-fetch log after rollback and persist a sanitized, bounded error detail.
        failed_log = EmailNotificationLog.query.filter_by(case_id=case.id, event_key="new_request").first()
        if failed_log:
            failed_log.status = "failed"
            failed_log.attempts = 1
            failed_log.error_message = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.session.commit()
        logger.exception("New-request email failed for case %s", case.reference)
        return False


def make_reference():
    year = datetime.utcnow().year
    latest = Case.query.filter(Case.reference.like(f"CETTP-{year}-%")).order_by(Case.id.desc()).first()
    seq = int(latest.reference.rsplit("-", 1)[1]) + 1 if latest else 1
    return f"CETTP-{year}-{seq:05d}"

def _normalise_policy(value):
    """Compare policy identifiers consistently despite spaces/case/punctuation."""
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())

def _case_action_signature(case_type, policy_number, effective_date, invoice_no, amount):
    try:
        amount_value = abs(Decimal(str(amount or 0))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        amount_value = Decimal("0.00")
    return (_normalise_policy(policy_number), str(case_type or "").strip().lower(),
            effective_date.isoformat() if effective_date else "", str(invoice_no or "").strip().upper(), amount_value)

def _find_duplicate_action(case_type, policy_number, effective_date, invoice_no, amount, exclude_case_id=None, exclude_tracker_id=None):
    """Find exact duplicate actions across initiator cases and Premium Register imports.

    Same-policy records of a different date/document/amount are surfaced as related
    actions, but are not blocked because one policy can legitimately have many endorsements.
    """
    target = _case_action_signature(case_type, policy_number, effective_date, invoice_no, amount)
    related = []
    for case in Case.query.all():
        if exclude_case_id and case.id == exclude_case_id:
            continue
        if _normalise_policy(case.policy_number) != target[0] or case.case_type.lower() != target[1]:
            continue
        related.append((case.reference, case.effective_date, case.original_invoice_no, case.adjustment_amount, case.workflow_status, case.tra_status))
        if _case_action_signature(case.case_type, case.policy_number, case.effective_date, case.original_invoice_no, case.adjustment_amount) == target:
            return ("case", case, related)
    # Tracker rows that have not yet been imported to CETTP also count as existing actions.
    tracker_rows = CancellationTrack.query.all()
    for row in tracker_rows:
        if exclude_tracker_id and row.id == exclude_tracker_id:
            continue
        inferred_type = "Endorsement" if (row.classification or "").startswith("Different amount") else "Cancellation"
        if _normalise_policy(row.policy_number) != target[0] or inferred_type.lower() != target[1]:
            continue
        related.append((f"Premium Register row #{row.id}", row.transaction_date, row.document_no, row.negative_amount, "Tracked", row.tra_status))
        if _case_action_signature(inferred_type, row.policy_number, row.transaction_date, row.document_no, row.negative_amount) == target:
            return ("tracker", row, related)
    return (None, None, related)

def _tax_actor_name():
    return (session.get("actor_name") or "Tax Compliance").strip()[:120]


def _notification_is_read(notification_id, actor_name=None):
    actor_name = actor_name or _tax_actor_name()
    return InAppNotificationRead.query.filter_by(notification_id=notification_id, actor_name=actor_name).first() is not None


@app.route("/notifications")
def notifications():
    if session.get("role") != "tax":
        return redirect(url_for("home"))
    actor = _tax_actor_name()
    rows = InAppNotification.query.order_by(InAppNotification.created_at.desc()).limit(250).all()
    read_ids = {r.notification_id for r in InAppNotificationRead.query.filter_by(actor_name=actor).all()}
    return render_template("notifications.html", notifications=rows, read_ids=read_ids, actor=actor, unread_count=sum(1 for row in rows if row.id not in read_ids))


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
def mark_notification_read(notification_id):
    if session.get("role") != "tax":
        return redirect(url_for("home"))
    notification = InAppNotification.query.get_or_404(notification_id)
    actor = _tax_actor_name()
    existing = InAppNotificationRead.query.filter_by(notification_id=notification.id, actor_name=actor).first()
    if not existing:
        db.session.add(InAppNotificationRead(notification_id=notification.id, actor_name=actor))
        db.session.commit()
    return redirect(url_for("case_detail", case_id=notification.case_id))


@app.route("/notifications/read-all", methods=["POST"])
def mark_all_notifications_read():
    if session.get("role") != "tax":
        return redirect(url_for("home"))
    actor = _tax_actor_name()
    read_ids = {r.notification_id for r in InAppNotificationRead.query.filter_by(actor_name=actor).all()}
    unread = InAppNotification.query.filter(~InAppNotification.id.in_(read_ids)).all() if read_ids else InAppNotification.query.all()
    now = datetime.utcnow()
    for item in unread:
        db.session.add(InAppNotificationRead(notification_id=item.id, actor_name=actor, read_at=now))
    db.session.commit()
    flash(f"Marked {len(unread)} notification(s) as read.", "success")
    return redirect(url_for("notifications"))


@app.route("/")
def home():
    return render_template("home.html")

@app.route("/enter/<role>", methods=["GET", "POST"])
def enter_role(role):
    if role not in ("initiator", "tax"):
        return redirect(url_for("home"))
    if role == "tax":
        if request.method == "POST":
            code = request.form.get("security_code", "")
            if code == os.environ.get("TAX_ACCESS_CODE", "4823"):
                session["role"] = "tax"
                session["actor_name"] = request.form.get("actor_name", "").strip() or "Tax Compliance"
                return redirect(url_for("dashboard"))
            flash("Access Denied", "danger")
        return render_template("tax_access.html")
    session["role"] = "initiator"
    session["actor_name"] = ""
    return redirect(url_for("new_case"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard")
def dashboard():
    if session.get("role") != "tax":
        return redirect(url_for("home"))
    total = Case.query.count()
    open_cases = Case.query.filter(Case.workflow_status != "Closed").count()
    awaiting_tra = Case.query.filter(Case.tra_status.in_(["Not Submitted", "Pending"])).count()
    closed = Case.query.filter_by(workflow_status="Closed").count()
    recent = Case.query.order_by(Case.created_at.desc()).limit(8).all()
    by_status = db.session.query(Case.workflow_status, func.count(Case.id)).group_by(Case.workflow_status).all()
    by_type = db.session.query(Case.case_type, func.count(Case.id)).group_by(Case.case_type).all()
    return render_template("dashboard.html", total=total, open_cases=open_cases, awaiting_tra=awaiting_tra,
                           closed=closed, recent=recent, by_status=by_status, by_type=by_type)

@app.route("/cases")
def cases():
    if session.get("role") not in ("tax", "initiator"):
        return redirect(url_for("home"))
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    case_type = request.args.get("type", "")
    query = Case.query
    if q:
        query = query.filter(or_(Case.reference.ilike(f"%{q}%"), Case.policy_number.ilike(f"%{q}%"),
                                 Case.customer_name.ilike(f"%{q}%"), Case.tra_reference.ilike(f"%{q}%")))
    if status:
        query = query.filter_by(workflow_status=status)
    if case_type:
        query = query.filter_by(case_type=case_type)
    records = query.order_by(Case.created_at.desc()).all()
    return render_template("cases.html", records=records, statuses=STATUSES, q=q, status=status, case_type=case_type)

@app.route("/cases/<int:case_id>/delete", methods=["POST"])
def delete_case(case_id):
    # Destructive actions are restricted to Tax Compliance and require a CSRF-protected POST.
    if not _tax_required(): return redirect(url_for("home"))
    case = Case.query.get_or_404(case_id)
    reference = case.reference
    # Remove linked tracker rows as well, so the deleted action cannot remain as stale tracking.
    CancellationTrack.query.filter_by(case_id=case.id).delete(synchronize_session=False)
    # Audit logs are cascade-deleted with the case.
    db.session.delete(case)
    db.session.commit()
    flash(f"Case {reference} and its linked tracking record(s) were deleted.", "success")
    return redirect(url_for("cases"))

@app.route("/cases/export.xlsx")
def cases_export():
    if session.get("role") not in ("tax", "initiator"):
        return redirect(url_for("home"))
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    q = request.args.get("q", "").strip(); status = request.args.get("status", ""); case_type = request.args.get("type", "")
    query = Case.query
    if q:
        query = query.filter(or_(Case.reference.ilike(f"%{q}%"), Case.policy_number.ilike(f"%{q}%"), Case.customer_name.ilike(f"%{q}%"), Case.tra_reference.ilike(f"%{q}%")))
    if status: query = query.filter_by(workflow_status=status)
    if case_type: query = query.filter_by(case_type=case_type)
    records = query.order_by(Case.created_at.desc()).all()
    wb = Workbook(); ws = wb.active; ws.title = "CETTP Case Register"
    ws.append(["Britam CETTP — Case Register"]); ws.append([])
    headers = ["Case Reference", "Type", "Policy Number", "Customer / Insured", "Customer TIN", "Product", "Branch", "Underwriter", "Reason", "Effective Date", "Original Invoice No.", "Original Invoice Amount (TZS)", "Adjustment Amount (TZS)", "VAT Amount (TZS)", "Credit Note No.", "Replacement Invoice No.", "Adjustment Reference", "TRA Status", "TRA Reference", "Workflow Status", "Initiator", "Tax Owner", "Tax Notes", "Closure Notes", "Created At", "Updated At"]
    ws.append(headers)
    for c in records:
        ws.append([c.reference,c.case_type,c.policy_number,c.customer_name,c.customer_tin,c.product,c.branch,c.underwriter,c.reason,c.effective_date,c.original_invoice_no,float(c.original_invoice_amount or 0),float(c.adjustment_amount or 0),float(c.vat_amount or 0),c.credit_note_no,c.replacement_invoice_no,c.adjustment_reference,c.tra_status,c.tra_reference,c.workflow_status,c.initiator_name,c.tax_owner,c.tax_notes,c.closure_notes,c.created_at,c.updated_at])
    for cell in ws[3]: cell.font=Font(bold=True,color="FFFFFF"); cell.fill=PatternFill("solid",fgColor="123B63"); cell.alignment=Alignment(wrap_text=True,vertical="center")
    ws.freeze_panes="A4"; ws.auto_filter.ref=f"A3:{get_column_letter(ws.max_column)}{ws.max_row}"
    for col in ws.columns:
        ws.column_dimensions[get_column_letter(col[0].column)].width=max(12,min(36,max(len(str(c.value or "")) for c in col)+2))
    for row in ws.iter_rows(min_row=4):
        for cell in row:
            if cell.column in (10,25,26) and cell.value: cell.number_format="yyyy-mm-dd hh:mm" if cell.column in (25,26) else "yyyy-mm-dd"
            if cell.column in (12,13,14) and cell.value is not None: cell.number_format='#,##0.00;[Red](#,##0.00)'
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return send_file(out,mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",as_attachment=True,download_name="Britam_CETTP_Case_Register.xlsx")

def _save_case_attachment(case, uploaded_file, category):
    """Validate and save an uploaded file; raises ValueError for user-correctable errors."""
    if not uploaded_file or not uploaded_file.filename:
        return None
    original_name = uploaded_file.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    safe_name = secure_filename(original_name)
    if not safe_name or "." not in safe_name:
        raise ValueError("Each attachment must have a valid file extension.")
    extension = safe_name.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise ValueError(f"File type .{extension} is not allowed. Allowed types: PDF, JPG/PNG, Excel, Word and CSV.")
    content = uploaded_file.read(MAX_ATTACHMENT_BYTES + 1)
    if not content:
        raise ValueError(f"{safe_name} is empty.")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"{safe_name} exceeds the 12 MB per-file limit.")
    if category not in dict(ATTACHMENT_CATEGORIES):
        raise ValueError("Select a valid document category.")
    item = CaseAttachment(case_id=case.id, category=category,
        original_filename=original_name[:255], stored_filename=safe_name[:255],
        content_type=(uploaded_file.mimetype or "application/octet-stream")[:120],
        file_size=len(content), file_data=content,
        uploaded_by=(session.get("actor_name") or "Unknown user")[:120])
    db.session.add(item)
    db.session.flush()
    log_action(case, "Supporting document uploaded", f"{dict(ATTACHMENT_CATEGORIES)[category]}: {safe_name} ({len(content)} bytes)")
    return item


@app.errorhandler(413)
def attachment_too_large(_error):
    flash("The upload was too large. Keep the total request below 40 MB and each attachment at or below 12 MB.", "danger")
    return redirect(request.referrer or url_for("cases"))


@app.route("/cases/<int:case_id>/attachments/upload", methods=["POST"])
def upload_case_attachments(case_id):
    if session.get("role") not in ("initiator", "tax"):
        return redirect(url_for("home"))
    case = Case.query.get_or_404(case_id)
    category = request.form.get("category", "")
    files = request.files.getlist("attachments")
    files = [f for f in files if f and f.filename]
    if not files:
        flash("Choose at least one file to upload.", "warning")
        return redirect(url_for("case_detail", case_id=case.id))
    try:
        for uploaded_file in files:
            _save_case_attachment(case, uploaded_file, category)
        db.session.commit()
        flash(f"Successfully uploaded {len(files)} document(s) to {case.reference}.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except Exception:
        db.session.rollback()
        logger.exception("Attachment upload failed for case %s", case.reference)
        flash("The documents could not be saved. No upload was committed; please try again or contact the administrator.", "danger")
    return redirect(url_for("case_detail", case_id=case.id))


@app.route("/cases/<int:case_id>/attachments/<int:attachment_id>/download")
def download_case_attachment(case_id, attachment_id):
    if session.get("role") not in ("initiator", "tax"):
        return redirect(url_for("home"))
    case = Case.query.get_or_404(case_id)
    attachment = CaseAttachment.query.filter_by(id=attachment_id, case_id=case.id, active=True).first_or_404()
    return send_file(BytesIO(attachment.file_data), mimetype=attachment.content_type,
        as_attachment=True, download_name=attachment.stored_filename, max_age=0)


@app.route("/cases/<int:case_id>/attachments/<int:attachment_id>/delete", methods=["POST"])
def delete_case_attachment(case_id, attachment_id):
    if not _tax_required():
        return redirect(url_for("home"))
    case = Case.query.get_or_404(case_id)
    attachment = CaseAttachment.query.filter_by(id=attachment_id, case_id=case.id, active=True).first_or_404()
    # Soft-delete so document metadata and the action remain auditable; binary is retained for audit recovery.
    attachment.active = False
    log_action(case, "Supporting document removed from case view", f"{attachment.original_filename} ({dict(ATTACHMENT_CATEGORIES).get(attachment.category, attachment.category)})")
    db.session.commit()
    flash("Document removed from the active case attachment list. Its audit record is retained.", "success")
    return redirect(url_for("case_detail", case_id=case.id))


@app.route("/cases/new", methods=["GET", "POST"])
def new_case():
    if session.get("role") not in ("initiator", "tax"):
        return redirect(url_for("home"))
    form = CaseForm()
    if form.validate_on_submit():
        duplicate_source, duplicate_record, related_actions = _find_duplicate_action(
            form.case_type.data, form.policy_number.data.strip(), form.effective_date.data,
            form.original_invoice_no.data.strip() or None, form.adjustment_amount.data or 0)
        if duplicate_source:
            if duplicate_source == "case":
                flash(f"DUPLICATE ALERT: This {form.case_type.data.lower()} for policy {form.policy_number.data.strip()} appears to have already been initiated as {duplicate_record.reference} (workflow: {duplicate_record.workflow_status}; TRA: {duplicate_record.tra_status}). Open the existing case instead of creating another.", "danger")
            else:
                flash(f"DUPLICATE ALERT: This action for policy {form.policy_number.data.strip()} is already recorded in the Premium Register tracker (row #{duplicate_record.id}, TRA status: {duplicate_record.tra_status}). Review the existing tracking record instead of creating a duplicate.", "danger")
            return render_template("case_form.html", form=form)
        if related_actions:
            refs = ", ".join(str(x[0]) for x in related_actions[:4])
            flash(f"POLICY ALERT: Existing {form.case_type.data.lower()} tracking for this policy was found ({refs}). Confirm this is a separate action before submitting; different dates, documents or amounts can represent legitimate separate endorsements.", "warning")
        case = Case(reference=make_reference(), case_type=form.case_type.data,
                    policy_number=form.policy_number.data.strip(), customer_name=form.customer_name.data.strip(),
                    customer_tin=form.customer_tin.data.strip() or None, product=form.product.data.strip() or None,
                    branch=form.branch.data.strip() or None, underwriter=form.underwriter.data.strip() or None,
                    reason=form.reason.data.strip(), effective_date=form.effective_date.data,
                    original_invoice_no=form.original_invoice_no.data.strip() or None,
                    original_invoice_amount=form.original_invoice_amount.data or 0,
                    adjustment_amount=form.adjustment_amount.data or 0, vat_amount=form.vat_amount.data or 0,
                    initiator_name=form.initiator_name.data.strip())
        db.session.add(case)
        db.session.flush()
        log_action(case, "Case initiated", f"{case.case_type} submitted for policy {case.policy_number}")
        try:
            for category, _label in ATTACHMENT_CATEGORIES:
                for uploaded_file in request.files.getlist(f"files_{category}"):
                    if uploaded_file and uploaded_file.filename:
                        _save_case_attachment(case, uploaded_file, category)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return render_template("case_form.html", form=form, attachment_categories=ATTACHMENT_CATEGORIES)
        except Exception:
            db.session.rollback()
            logger.exception("Initial attachment save failed")
            flash("The request could not be saved with its attachments. Please retry without files or contact the administrator.", "danger")
            return render_template("case_form.html", form=form, attachment_categories=ATTACHMENT_CATEGORIES)
        # In-app Tax Compliance alert works independently of email configuration.
        db.session.add(InAppNotification(
            case_id=case.id,
            case_reference=case.reference,
            title=f"New {case.case_type} request",
            message=f"{case.initiator_name} submitted a {case.case_type.lower()} for policy {case.policy_number} ({case.customer_name})."
        ))
        db.session.commit()
        email_sent = send_new_request_notification(case)
        if email_sent:
            flash(f"Request submitted successfully. Reference: {case.reference}. Tax Compliance email notification sent.", "success")
        else:
            flash(f"Request submitted successfully. Reference: {case.reference}. An in-app notification is available to Tax Compliance. Email delivery is not configured and has been logged.", "success")
        return redirect(url_for("case_detail", case_id=case.id))
    return render_template("case_form.html", form=form)

@app.route("/cases/<int:case_id>")
def case_detail(case_id):
    if session.get("role") not in ("tax", "initiator"):
        return redirect(url_for("home"))
    case = Case.query.get_or_404(case_id)
    return render_template("case_detail.html", case=case, attachment_categories=ATTACHMENT_CATEGORIES, attachment_category_labels=dict(ATTACHMENT_CATEGORIES))

@app.route("/cases/<int:case_id>/tax-update", methods=["GET", "POST"])
def tax_update(case_id):
    if session.get("role") != "tax":
        flash("Tax Compliance access is required.", "danger")
        return redirect(url_for("home"))
    case = Case.query.get_or_404(case_id)
    form = TaxForm(obj=case)
    if form.validate_on_submit():
        old_status = case.workflow_status
        for field in ("workflow_status", "tra_status", "tax_owner", "tra_reference", "credit_note_no",
                      "replacement_invoice_no", "adjustment_reference", "tax_notes", "closure_notes"):
            value = getattr(form, field).data
            setattr(case, field, value.strip() if isinstance(value, str) and value.strip() else None if isinstance(value, str) else value)
        if case.workflow_status == "Closed":
            case.closed_at = datetime.utcnow()
            if not case.closure_notes:
                case.closure_notes = "Closed by Tax Compliance."
        else:
            case.closed_at = None
        log_action(case, "Tax record updated", f"Workflow status: {old_status} → {case.workflow_status}; TRA status: {case.tra_status}")
        db.session.commit()
        flash("Tax tracking record updated.", "success")
        return redirect(url_for("case_detail", case_id=case.id))
    return render_template("tax_update.html", form=form, case=case)

@app.route("/cases/<int:case_id>/history")
def case_history(case_id):
    if session.get("role") != "tax":
        return redirect(url_for("home"))
    case = Case.query.get_or_404(case_id)
    return render_template("history.html", case=case)


RECON_COLUMNS = ["period", "source", "reference", "counterparty", "category", "book_amount", "tra_amount", "notes"]

def _tax_required():
    if session.get("role") != "tax":
        flash("Tax Compliance access is required.", "danger")
        return False
    return True

def _amount(value):
    raw = str(value or "0").replace(",", "").strip()
    try:
        return Decimal(raw or "0")
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid amount: {value}")

@app.route("/reconciliation")
def reconciliation_home():
    if not _tax_required():
        return redirect(url_for("home"))
    kind = request.args.get("type", "VAT").upper()
    if kind not in ("VAT", "WHT"):
        kind = "VAT"
    period = request.args.get("period", "").strip()
    query = ReconciliationItem.query.filter_by(tax_type=kind)
    if period:
        query = query.filter_by(period=period)
    items = query.order_by(ReconciliationItem.created_at.desc()).limit(1000).all()
    total_book = sum((Decimal(str(x.book_amount or 0)) for x in items), Decimal("0"))
    total_tra = sum((Decimal(str(x.tra_amount or 0)) for x in items), Decimal("0"))
    total_variance = total_book-total_tra
    return render_template("reconciliation.html", items=items, kind=kind, period=period,
                           total_book=total_book, total_tra=total_tra, total_variance=total_variance)

@app.route("/reconciliation/upload", methods=["POST"])
def reconciliation_upload():
    if not _tax_required():
        return redirect(url_for("home"))
    kind = request.form.get("tax_type", "VAT").upper()
    if kind not in ("VAT", "WHT"):
        flash("Select VAT or WHT reconciliation.", "danger")
        return redirect(url_for("reconciliation_home"))
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choose a CSV or Excel file to upload.", "warning")
        return redirect(url_for("reconciliation_home", type=kind))
    name = secure_filename(file.filename).lower()
    try:
        if name.endswith(".csv"):
            content = file.stream.read().decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(content)))
        elif name.endswith((".xlsx", ".xlsm")):
            from openpyxl import load_workbook
            wb = load_workbook(file, read_only=True, data_only=True)
            ws = wb.active
            vals = ws.iter_rows(values_only=True)
            headers = [str(v or "").strip().lower() for v in next(vals, [])]
            rows = [dict(zip(headers, row)) for row in vals]
        else:
            raise ValueError("File must be .csv or .xlsx")
        cleaned = []
        for index, row in enumerate(rows, start=2):
            row = {str(k or "").strip().lower(): v for k, v in row.items()}
            if not any(v not in (None, "") for v in row.values()):
                continue
            missing = [c for c in ("period", "source", "book_amount", "tra_amount") if c not in row]
            if missing:
                raise ValueError("Missing required column(s): " + ", ".join(missing) + ". Use the provided template.")
            period = str(row.get("period") or "").strip()
            source = str(row.get("source") or "").strip()
            if not period or not source:
                raise ValueError(f"Row {index}: period and source are required.")
            cleaned.append(ReconciliationItem(tax_type=kind, period=period, source=source[:80],
                reference=str(row.get("reference") or "").strip()[:120] or None,
                counterparty=str(row.get("counterparty") or "").strip()[:180] or None,
                category=str(row.get("category") or "").strip()[:120] or None,
                book_amount=_amount(row.get("book_amount")), tra_amount=_amount(row.get("tra_amount")),
                notes=str(row.get("notes") or "").strip() or None, uploaded_by=session.get("actor_name", "Tax Compliance")))
        if not cleaned:
            raise ValueError("The file contains no data rows.")
        db.session.add_all(cleaned)
        db.session.commit()
        flash(f"Imported {len(cleaned)} {kind} reconciliation row(s). Variances are calculated automatically.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Upload failed: {exc}", "danger")
    return redirect(url_for("reconciliation_home", type=kind))

@app.route("/reconciliation/template/<kind>")
def reconciliation_template(kind):
    if not _tax_required():
        return redirect(url_for("home"))
    kind = kind.upper()
    if kind not in ("VAT", "WHT"):
        return redirect(url_for("reconciliation_home"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(RECON_COLUMNS)
    if kind == "VAT":
        writer.writerow(["2026-09", "Premiums-General", "INV-0001", "Example Customer", "Premiums", "100000", "100000", "Replace sample row with your data"])
    else:
        writer.writerow(["2026-09", "Agents Commission", "AG-0001", "Example Agent", "Commission WHT", "15000", "15000", "Replace sample row with your data"])
    data = io.BytesIO(output.getvalue().encode("utf-8-sig")); data.seek(0)
    return send_file(data, mimetype="text/csv", as_attachment=True, download_name=f"{kind.lower()}_reconciliation_template.csv")

@app.route("/reconciliation/<int:item_id>/delete", methods=["POST"])
def reconciliation_delete(item_id):
    if not _tax_required():
        return redirect(url_for("home"))
    item = ReconciliationItem.query.get_or_404(item_id)
    kind = item.tax_type
    db.session.delete(item); db.session.commit()
    flash("Reconciliation row deleted.", "success")
    return redirect(url_for("reconciliation_home", type=kind))

# Premium Register cancellation / endorsement tracking
CANCEL_TRA_STATUSES = ["Not Submitted", "Pending", "Approved", "Rejected", "Not Applicable"]

def _normal_header(value):
    return " ".join(str(value or "").replace("\\n", " ").strip().upper().split())

def _parse_register_date(value):
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    raw = str(value or "").strip()
    if not raw: return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%y"):
        try: return datetime.strptime(raw[:10], fmt).date()
        except ValueError: pass
    try:
        from openpyxl.utils.datetime import from_ISO8601
        return from_ISO8601(raw).date()
    except Exception: return None

def _parse_register_amount(value):
    if value is None or str(value).strip() == "": return Decimal("0")
    if isinstance(value, (int, float, Decimal)): return Decimal(str(value)).quantize(Decimal("0.01"))
    raw = str(value).strip().replace(",", "").replace("TZS", "").replace(" ", "")
    if raw.startswith("(") and raw.endswith(")"): raw = "-" + raw[1:-1]
    try: return Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation: return Decimal("0")

def _register_rows(file):
    name = secure_filename(file.filename or "").lower()
    if name.endswith(".csv"):
        text_data = file.stream.read().decode("utf-8-sig", errors="replace")
        matrix = list(csv.reader(io.StringIO(text_data)))
    elif name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(file, read_only=True, data_only=True)
        except Exception as exc:
            raise ValueError("The Premium Register could not be opened as a standard Excel workbook. Please open it in Excel and Save As an unprotected .xlsx file, or upload a CSV export.") from exc
        ws = wb.active
        matrix = list(ws.iter_rows(values_only=True))
    else:
        raise ValueError("Upload the Premium Register as .xlsx or .csv.")
    header_index = None; headers = None
    for i, row in enumerate(matrix[:80]):
        normalized = [_normal_header(v) for v in row]
        if any("POLICY NO" in h for h in normalized) and any("GROSS PREMIUM" in h for h in normalized):
            header_index = i; headers = normalized; break
    if header_index is None:
        raise ValueError("Could not find the Premium Register header row. Required headings include POLICY NO and GROSS PREMIUM.")
    result=[]
    for row in matrix[header_index+1:]:
        values = list(row) + [None] * max(0, len(headers)-len(row))
        item = {headers[j]: values[j] for j in range(min(len(headers),len(values))) if headers[j]}
        policy = str(item.get("POLICY NO") or "").strip()
        if not policy or policy.upper() in ("NONE", "TOTAL", "GRAND TOTAL"):
            continue
        # Ignore repeated header lines and report totals.
        if policy.upper() == "POLICY NO": continue
        amount_value = item.get("GROSS PREMIUM")
        amount = _parse_register_amount(amount_value)
        if amount == 0:
            # Some exports place the signed transaction value in AMT or NETT PREMIUM.
            for alt in ("AMT", "NETT PREMIUM"):
                candidate = _parse_register_amount(item.get(alt))
                if candidate != 0: amount = candidate; break
        result.append({
            "transaction_date": _parse_register_date(item.get("TRANSACTION DATE")),
            "document_no": str(item.get("NO") or "").strip() or None,
            "policy_number": policy[:80],
            "endorsement_no": str(item.get("ENDORSEMENT NO") or "").strip() or None,
            "insured_name": str(item.get("NAME OF INSURED") or "").strip() or None,
            "product": str(item.get("PRODUCT") or "").strip() or None,
            "branch_name": str(item.get("BRANCH NAME") or "").strip() or None,
            "transaction_type": str(item.get("TRANSACTION TYPE") or "").strip() or None,
            "amount": amount,
        })
    if not result: raise ValueError("No Premium Register transactions were found under the detected headings.")
    return result

@app.route("/cancellation-tracker", methods=["GET"])
def cancellation_tracker():
    if not _tax_required(): return redirect(url_for("home"))
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    period = request.args.get("period", "").strip()
    query = CancellationTrack.query
    if q: query = query.filter(or_(CancellationTrack.policy_number.ilike(f"%{q}%"), CancellationTrack.insured_name.ilike(f"%{q}%"), CancellationTrack.document_no.ilike(f"%{q}%")))
    if status: query = query.filter_by(tra_status=status)
    if period:
        batches = [u.upload_batch for u in RegisterUpload.query.filter_by(period=period).all()]
        if batches: query = query.filter(CancellationTrack.upload_batch.in_(batches))
        else: query = query.filter(CancellationTrack.id == -1)
    rows = query.order_by(CancellationTrack.created_at.desc(), CancellationTrack.id.desc()).limit(5000).all()
    uploads = RegisterUpload.query.order_by(RegisterUpload.period.desc(), RegisterUpload.created_at.desc()).limit(120).all()
    periods = sorted({u.period for u in RegisterUpload.query.all()}, reverse=True)
    return render_template("cancellation_tracker.html", rows=rows, q=q, status=status, period=period,
                           periods=periods, uploads=uploads, current_period=date.today().strftime("%Y-%m"), tra_statuses=CANCEL_TRA_STATUSES,
                           total=len(rows), unreasoned=sum(1 for r in rows if not r.cancellation_reason), unsubmitted=sum(1 for r in rows if r.tra_status == "Not Submitted"))

@app.route("/cancellation-tracker/upload", methods=["POST"])
def cancellation_tracker_upload():
    if not _tax_required(): return redirect(url_for("home"))
    file = request.files.get("file")
    period = request.form.get("period", "").strip()
    if not period or len(period) != 7 or period[4] != "-" or not period[:4].isdigit() or not period[5:7].isdigit() or not (1 <= int(period[5:7]) <= 12):
        flash("Select a valid reconciliation month before uploading the Premium Register.", "warning"); return redirect(url_for("cancellation_tracker"))
    if not file or not file.filename:
        flash("Choose the AIMS Premium Register to upload.", "warning"); return redirect(url_for("cancellation_tracker", period=period))
    try:
        source_rows = _register_rows(file)
        positives = {}
        for row in source_rows:
            if row["amount"] > 0: positives.setdefault(row["policy_number"], []).append(row)
        used_positive = set()
        negatives = [r for r in source_rows if r["amount"] < 0]
        batch = datetime.utcnow().strftime("REG-%Y%m%d-%H%M%S-%f")
        imported = []
        duplicate_skipped = 0
        same_policy_alerts = set()
        seen_in_upload = set()
        for neg in negatives:
            candidates = positives.get(neg["policy_number"], [])
            available = [(idx, pos) for idx, pos in enumerate(candidates) if (neg["policy_number"], idx) not in used_positive]
            exact = next(((idx,pos) for idx,pos in available if pos["amount"] == abs(neg["amount"])), None)
            chosen = exact or (min(available, key=lambda pair: abs(pair[1]["amount"]-abs(neg["amount"]))) if available else None)
            if chosen:
                idx, pos = chosen; used_positive.add((neg["policy_number"], idx))
                classification = "Matched amount" if pos["amount"] == abs(neg["amount"]) else "Different amount - review as endorsement"
            else:
                pos = None; classification = "Pure cancellation"
            inferred_type = "Endorsement" if classification.startswith("Different amount") else "Cancellation"
            signature = _case_action_signature(inferred_type, neg["policy_number"], neg["transaction_date"], neg["document_no"], neg["amount"])
            if signature in seen_in_upload:
                duplicate_skipped += 1
                continue
            seen_in_upload.add(signature)
            dup_source, dup_record, related = _find_duplicate_action(inferred_type, neg["policy_number"], neg["transaction_date"], neg["document_no"], neg["amount"])
            if dup_source:
                duplicate_skipped += 1
                continue
            if related:
                same_policy_alerts.add((neg["policy_number"], inferred_type))
            track = CancellationTrack(upload_batch=batch, transaction_date=neg["transaction_date"], document_no=neg["document_no"],
                policy_number=neg["policy_number"], endorsement_no=neg["endorsement_no"], insured_name=neg["insured_name"],
                product=neg["product"], branch_name=neg["branch_name"], transaction_type=neg["transaction_type"],
                negative_amount=neg["amount"], matched_positive_amount=pos["amount"] if pos else 0,
                matched_document_no=pos["document_no"] if pos else None, classification=classification,
                cancellation_reason=None, tra_status="Not Submitted", imported_by=session.get("actor_name", "Tax Compliance"))
            db.session.add(track); imported.append(track)
        if not imported and not duplicate_skipped:
            raise ValueError("No negative premium transactions were found. No cancellation records were created.")
        matched=sum(1 for x in imported if x.classification=="Matched amount")
        different=sum(1 for x in imported if x.classification=="Different amount - review as endorsement")
        pure=sum(1 for x in imported if x.classification=="Pure cancellation")
        upload = RegisterUpload(upload_batch=batch, period=period, file_name=secure_filename(file.filename)[:255] or "Premium Register",
            source_rows=len(source_rows), negative_count=len(imported), matched_count=matched, different_count=different,
            pure_count=pure, uploaded_by=session.get("actor_name", "Tax Compliance"))
        db.session.add(upload); db.session.commit()
        summary = f"{period} register saved: {len(source_rows)} transactions; {len(imported)} new negative-premium tracking record(s). Exact matches: {matched}; different positive amounts: {different}; pure cancellations: {pure}. Monthly upload retained in history."
        if duplicate_skipped:
            summary += f" DUPLICATE ALERT: {duplicate_skipped} record(s) were skipped because the same policy/action/date/document/amount is already tracked in CETTP or an earlier register upload."
        if same_policy_alerts:
            sample = ", ".join(f"{policy} ({kind})" for policy, kind in sorted(same_policy_alerts)[:8])
            summary += f" POLICY REVIEW ALERT: related actions already exist for {len(same_policy_alerts)} policy/action combination(s): {sample}. Different documents, dates or amounts were retained for review."
        flash(summary, "warning" if duplicate_skipped or same_policy_alerts else "success")
    except Exception as exc:
        db.session.rollback(); flash(f"Premium Register upload failed: {exc}", "danger")
    return redirect(url_for("cancellation_tracker", period=period))

@app.route("/cancellation-tracker/<int:item_id>/delete", methods=["POST"])
def cancellation_tracker_delete(item_id):
    if not _tax_required(): return redirect(url_for("home"))
    row = CancellationTrack.query.get_or_404(item_id)
    period = next((u.period for u in RegisterUpload.query.filter_by(upload_batch=row.upload_batch).all()), "")
    db.session.delete(row)
    db.session.commit()
    flash(f"Tracking record #{item_id} deleted.", "success")
    return redirect(url_for("cancellation_tracker", period=period))

@app.route("/cancellation-tracker/batch/<int:upload_id>/delete", methods=["POST"])
def cancellation_tracker_delete_batch(upload_id):
    if not _tax_required(): return redirect(url_for("home"))
    upload = RegisterUpload.query.get_or_404(upload_id)
    batch = upload.upload_batch; period = upload.period; filename = upload.file_name
    rows = CancellationTrack.query.filter_by(upload_batch=batch).all()
    # Only delete CETTP cases created by this register import. Cases originally raised by initiators are preserved.
    register_case_ids = {r.case_id for r in rows if r.case_id}
    deleted_cases = 0
    for case_id in register_case_ids:
        case = db.session.get(Case, case_id)
        if case and case.initiator_name == "Premium Register Import":
            db.session.delete(case); deleted_cases += 1
    for row in rows:
        db.session.delete(row)
    db.session.delete(upload)
    db.session.commit()
    flash(f"Deleted upload batch '{filename}' for {period}, including {len(rows)} tracker row(s) and {deleted_cases} register-imported case(s). Cases originally raised by initiators were preserved.", "success")
    return redirect(url_for("cancellation_tracker", period=period))

@app.route("/cancellation-tracker/<int:item_id>/update", methods=["POST"])
def cancellation_tracker_update(item_id):
    if not _tax_required(): return redirect(url_for("home"))
    row = CancellationTrack.query.get_or_404(item_id)
    reason = request.form.get("cancellation_reason", "").strip()
    status = request.form.get("tra_status", "Not Submitted").strip()
    tra_ref = request.form.get("tra_reference", "").strip()
    if not reason:
        flash("A cancellation / endorsement reason is required for tracking.", "danger")
    elif status not in CANCEL_TRA_STATUSES:
        flash("Invalid TRA status selected.", "danger")
    else:
        row.cancellation_reason = reason[:4000]; row.tra_status = status; row.tra_reference = tra_ref[:120] or None
        if row.case_id:
            case = db.session.get(Case, row.case_id)
            if case:
                case.reason = row.cancellation_reason; case.tra_status = status; case.tra_reference = row.tra_reference
                case.tax_notes = (case.tax_notes or "") + f"\\nPremium Register tracker updated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}."
                log_action(case, "Register cancellation tracking updated", f"Reason updated; TRA status: {status}")
        db.session.commit(); flash("Cancellation tracking record updated.", "success")
    return redirect(url_for("cancellation_tracker"))

@app.route("/cancellation-tracker/import-to-cettp", methods=["POST"])
def cancellation_tracker_import_to_cettp():
    if not _tax_required(): return redirect(url_for("home"))
    selected = request.form.getlist("item_ids")
    query = CancellationTrack.query.filter(CancellationTrack.case_id.is_(None))
    if selected:
        try: ids=[int(x) for x in selected]
        except ValueError: ids=[]
        query=query.filter(CancellationTrack.id.in_(ids))
    rows=query.order_by(CancellationTrack.id.asc()).all()
    if not rows:
        flash("No unimported records were selected. Select records or use Auto-fill all unimported records.", "warning")
        return redirect(url_for("cancellation_tracker"))
    created=0
    duplicate_skipped=0
    for row in rows:
        case_type = "Endorsement" if row.classification.startswith("Different amount") else "Cancellation"
        effective = row.transaction_date or date.today()
        dup_source, dup_record, related = _find_duplicate_action(case_type, row.policy_number, effective, row.document_no, row.negative_amount, exclude_tracker_id=row.id)
        if dup_source:
            # Link the register row to the already existing CETTP case where possible,
            # so the same action is not imported as a second case.
            if dup_source == "case": row.case_id = dup_record.id
            duplicate_skipped += 1
            continue
        case = Case(reference=make_reference(), case_type=case_type, policy_number=row.policy_number,
            customer_name=row.insured_name or "Insured name not provided", customer_tin=None, product=row.product,
            branch=row.branch_name, underwriter=None,
            reason=row.cancellation_reason or "REASON REQUIRED — Tax Compliance to complete before TRA submission.",
            effective_date=effective, original_invoice_no=row.matched_document_no or row.document_no,
            original_invoice_amount=abs(row.matched_positive_amount) if row.matched_positive_amount else 0,
            adjustment_amount=row.negative_amount, vat_amount=0, initiator_name="Premium Register Import",
            tra_status=row.tra_status, tra_reference=row.tra_reference,
            workflow_status="Initiated")
        db.session.add(case); db.session.flush(); row.case_id=case.id
        log_action(case, "Imported from Premium Register", f"Classification: {row.classification}; negative premium {row.negative_amount}; positive match {row.matched_positive_amount}")
        created+=1
    db.session.commit()
    message = f"Imported {created} cancellation/endorsement record(s) into the CETTP case workflow. Records without reasons remain flagged for completion."
    if duplicate_skipped:
        message += f" DUPLICATE ALERT: {duplicate_skipped} already tracked action(s) were not imported again; existing CETTP cases were linked where available."
    flash(message, "warning" if duplicate_skipped else "success")
    return redirect(url_for("cancellation_tracker"))

@app.route("/cancellation-tracker/export.xlsx")
def cancellation_tracker_export():
    if not _tax_required(): return redirect(url_for("home"))
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    period = request.args.get("period", "").strip()
    query = CancellationTrack.query
    if period:
        batches = [u.upload_batch for u in RegisterUpload.query.filter_by(period=period).all()]
        query = query.filter(CancellationTrack.upload_batch.in_(batches)) if batches else query.filter(CancellationTrack.id == -1)
    rows=query.order_by(CancellationTrack.policy_number.asc(), CancellationTrack.transaction_date.asc()).all()
    period_by_batch = {u.upload_batch: u.period for u in RegisterUpload.query.all()}
    wb=Workbook(); ws=wb.active; ws.title="Cancellation Tracker"
    ws.append(["Reporting month", period or "All months"])
    ws.append([])
    headers=["Reporting Month","Tracker ID","Transaction Date","Document No.","Policy No.","Endorsement No.","Insured Name","Product","Branch","Transaction Type","Negative Premium (TZS)","Matched Positive Premium (TZS)","Matched Positive Document No.","Classification","Cancellation / Endorsement Reason","TRA Status","TRA Reference","CETTP Case Reference","Imported By","Created At"]
    ws.append(headers)
    for r in rows:
        ws.append([period_by_batch.get(r.upload_batch, "Legacy / Unknown"), r.id, r.transaction_date, r.document_no, r.policy_number, r.endorsement_no, r.insured_name, r.product, r.branch_name, r.transaction_type, float(r.negative_amount or 0), float(r.matched_positive_amount or 0), r.matched_document_no, r.classification, r.cancellation_reason, r.tra_status, r.tra_reference, r.case.reference if r.case else None, r.imported_by, r.created_at])
    for cell in ws[3]: cell.font=Font(bold=True,color="FFFFFF"); cell.fill=PatternFill("solid",fgColor="123B63"); cell.alignment=Alignment(wrap_text=True,vertical="center")
    ws.freeze_panes="A4"; ws.auto_filter.ref=f"A3:{get_column_letter(ws.max_column)}{ws.max_row}"
    for col in ws.columns:
        width=max(12,min(34,max(len(str(c.value or "")) for c in col)+2)); ws.column_dimensions[get_column_letter(col[0].column)].width=width
    for row in ws.iter_rows(min_row=4):
        for cell in row:
            if cell.column in (3,20) and cell.value: cell.number_format="yyyy-mm-dd hh:mm" if cell.column==20 else "yyyy-mm-dd"
            if cell.column in (11,12) and cell.value is not None: cell.number_format='#,##0.00;[Red](#,##0.00)'
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return send_file(out,mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",as_attachment=True,download_name=(f"Britam_Cancellation_Endorsement_Tracker_{period}.xlsx" if period else "Britam_Cancellation_Endorsement_Tracker_All_Months.xlsx"))

@app.route("/cancellation-tracker/template.csv")
def cancellation_tracker_template():
    if not _tax_required(): return redirect(url_for("home"))
    headers=["TRANSACTION DATE","NO","CLASS","PRODUCT","PERIOD","POLICY NO","ENDORSEMENT NO","TOTAL","TRANSACTION TYPE","OLD POLICY RENEWAL","OLD POLICY NO","BUSINESS TYPE","CLASSIFICATION","NAME OF INSURED","BRANCH","BRANCH NAME","INTERMEDIARY CODE","INTERMEDIARY NAME","CHANNEL","EFFECTIVE DATE","PERIOD FROM","EXPIRING","CURRENCY","CURRENCY RATE","SUM INSURED","EFFECTIVE SUM INSURED","GROSS PREMIUM","AMT","NETT PREMIUM","COMM AMT","PREMIUM STATUS"]
    out=io.StringIO(); csv.writer(out).writerow(headers); data=io.BytesIO(out.getvalue().encode("utf-8-sig")); data.seek(0)
    return send_file(data,mimetype="text/csv",as_attachment=True,download_name="AIMS_Premium_Register_import_template.csv")

@app.route("/health")
def health():
    return jsonify(status="ok", application="CETTP Integrated Tax Reconciliation Portal", version="1.1")

@app.context_processor
def inject_globals():
    unread_notification_count = 0
    if session.get("role") == "tax":
        try:
            actor = _tax_actor_name()
            read_ids = db.session.query(InAppNotificationRead.notification_id).filter_by(actor_name=actor)
            unread_notification_count = InAppNotification.query.filter(~InAppNotification.id.in_(read_ids)).count()
        except Exception:
            # Keep the portal usable during first-time database initialization.
            unread_notification_count = 0
    return {"current_role": session.get("role"), "current_actor": session.get("actor_name"),
            "unread_notification_count": unread_notification_count}

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
