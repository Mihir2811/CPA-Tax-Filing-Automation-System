# Tax Document Organizer Extraction System

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Technology Stack](#technology-stack)
- [Django Applications](#django-applications)
- [Data Models](#data-models)
- [AI Extraction Pipeline](#ai-extraction-pipeline)
- [Supported Tax Forms](#supported-tax-forms)
- [Folder Structure and File Management](#folder-structure-and-file-management)
- [Asynchronous Task System](#asynchronous-task-system)
- [Email Automation](#email-automation)
- [User Management and Roles](#user-management-and-roles)
- [API Endpoints](#api-endpoints)
- [Configuration](#configuration)
- [Prerequisites](#prerequisites)
- [Quick Start with Docker](#quick-start-with-docker)
- [Local Development Setup](#local-development-setup)
- [Management Commands](#management-commands)
- [Admin Interface](#admin-interface)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

The Tax Document Organizer Extraction System is a Django-based web application built for tax professionals and preparers. It automates the end-to-end processing of tax organizer PDFs by combining AI-powered form extraction, intelligent document classification, structured folder management, and automated client communication.

The system accepts multi-page tax organizer PDFs uploaded by users, processes each page using OpenAI's GPT-4o Vision API to identify and extract IRS form data, generates structured summaries and logic reports, organizes the resulting files into client-specific folder hierarchies, and sends automated email notifications to clients regarding pending documents.

### Core Workflow

```
PDF Upload (S3 or Local)
        |
        v
Celery / Background Thread
        |
        v
Page-by-Page PDF Processing (PyMuPDF + pdf2image)
        |
        v
OpenAI GPT-4o Vision: Form Identification per Page
        |
        v
OpenAI GPT-4o Vision: Structured Data Extraction per Page
        |
        v
JSON Cleaning and Normalization
        |
        v
Summary Generation (generate_summary_dict)
        |
        v
Required Forms JSON Generation
        |
        v
Logic Report Generation (sorted vs. required comparison)
        |
        v
Organizer Folder Creation (Sorted / Unsorted)
        |
        v
Database Persistence (ExtractedData, TaxDocument)
        |
        v
Email Notification (Azure Communication Services)
        |
        v
User Dashboard / Admin Panel
```

---

## System Architecture

The system is composed of two Django applications within a single project:

| Application | Purpose |
|---|---|
| `organizer_extraction_app` | Core application: document upload, AI extraction, folder management, email automation, user management |
| `forms` | Secondary application: individual IRS form detection and extraction from uploaded client PDFs, form sorting, binder generation |

The project configuration lives in `organizer_extraction/` (settings, URLs, Celery configuration, WSGI/ASGI).

### High-Level Component Diagram

```
Browser / Client
      |
      v
Django (Gunicorn on port 8092)
      |
      +---> organizer_extraction_app (main logic)
      |           |
      |           +---> services.py     (OpenAI extraction, email service, required forms)
      |           +---> utils.py        (S3, folder management, summary generation, file ops)
      |           +---> tasks.py        (Celery async tasks: email, folder monitoring)
      |           +---> views.py        (HTTP request handlers)
      |           +---> models.py       (Database models)
      |
      +---> forms (individual form detection)
                  |
                  +---> views.py        (detect_form_type, form extractors, upload_and_extract)
                  +---> models.py       (FormProcessingStat)

External Services:
  - AWS S3          (document storage)
  - OpenAI API      (GPT-4o Vision for extraction)
  - Azure Communication Services (email delivery)
  - Redis           (Celery broker and result backend)
  - PostgreSQL      (primary database)
```

---

## Technology Stack

| Category | Technology | Version |
|---|---|---|
| Backend Framework | Django | 5.2.8 |
| Database | PostgreSQL | 13+ |
| Task Queue | Celery | 5.5.3 |
| Message Broker | Redis | 7.0.1 |
| AI / Vision | OpenAI API (GPT-4o) | openai 2.7.1 |
| PDF Rendering | PyMuPDF | 1.26.6 |
| PDF to Image | pdf2image | 1.17.0 |
| Image Processing | Pillow | 12.0.0 |
| Cloud Storage | AWS S3 (boto3) | 1.40.70 |
| Email Delivery | Azure Communication Services | 1.1.0 |
| Web Server | Gunicorn | latest |
| Containerization | Docker (python:3.10-slim-bullseye) | - |
| Frontend | Django Templates (Bootstrap-styled) | - |

---

## Django Applications

### organizer_extraction_app

This is the primary application. It handles:

- Multi-PDF upload to AWS S3
- Background processing via threads and Celery
- Page-by-page AI extraction using OpenAI GPT-4o Vision
- Summary and logic report generation
- Client folder creation and file organization
- Email sending and open tracking
- User CRUD with role-based access control
- Activity logging and audit trail
- Custom metadata fields per document
- Folder monitoring for new client uploads

Key modules:

| Module | Responsibility |
|---|---|
| `models.py` | All database models |
| `views.py` | HTTP views for upload, results, summary, email, user management, PDF sorting |
| `services.py` | OpenAI extraction pipeline, email service, required forms generation |
| `utils.py` | S3 operations, folder management, summary generation, file renaming, logic creation |
| `tasks.py` | Celery tasks for email automation and folder monitoring |
| `serializers.py` | Django REST serializers for user validation |
| `decorators.py` | Role-based access control decorators |
| `middleware.py` | Request tracking middleware |
| `constants.py` | Role and action choices |

### forms

This secondary application handles individual IRS form PDFs uploaded directly by clients (not organizer PDFs). It:

- Detects the IRS form type from a PDF using GPT-4o Vision
- Dispatches to a form-specific extractor class
- Sorts classified forms into client-specific sorted folders
- Moves unclassified forms to unsorted folders
- Generates a combined PDF binder of sorted forms
- Tracks cumulative sorted/unsorted counts via `FormProcessingStat`

---

## Data Models

### TaxDocument

Represents an uploaded tax organizer PDF.

| Field | Type | Description |
|---|---|---|
| `user` | ForeignKey(User) | Uploading user |
| `file` | FileField | Local file path (if not S3) |
| `file_name` | CharField | Original filename |
| `s3_key` | CharField | S3 object key |
| `uploaded_at` | DateTimeField | Upload timestamp |
| `status` | CharField | `pending`, `processing`, `completed`, `failed` |

### ExtractedData

Stores all AI-extracted data for a document (one-to-one with TaxDocument).

| Field | Type | Description |
|---|---|---|
| `data` | JSONField | Raw per-page extraction results |
| `summary_data` | JSONField | Aggregated summary across all pages |
| `logic` | JSONField | Document status report (sorted vs. required) |
| `custom_fields` | JSONField | User-added metadata fields |
| `pages_processed` | IntegerField | Successfully extracted pages |
| `pages_skipped` | IntegerField | Skipped pages (blank, unreadable) |
| `pages_with_errors` | IntegerField | Pages with extraction errors |
| `sorted_forms_count` | PositiveIntegerField | Number of forms moved to sorted folder |
| `unsorted_forms_count` | PositiveIntegerField | Number of forms in unsorted folder |
| `completion_percentage` | FloatField | Sorted / required forms ratio |
| `required_forms_json` | JSONField | List of individually required form instances |

### UserProfile

Extends Django's built-in User model with role information.

| Field | Type | Description |
|---|---|---|
| `user` | OneToOneField(User) | Associated Django user |
| `role` | CharField | `admin` or `tax_preparer` |
| `is_active` | BooleanField | Profile active status |

### EmailTracking

Tracks email delivery and open events per recipient.

| Field | Type | Description |
|---|---|---|
| `document` | ForeignKey(TaxDocument) | Associated document |
| `email` | EmailField | Recipient address |
| `tracking_id` | UUIDField | Unique tracking identifier |
| `is_opened` | BooleanField | Whether email was opened |
| `opened_at` | DateTimeField | Timestamp of first open |

### EmailAutomation

Controls automated periodic email sending for a document.

| Field | Type | Description |
|---|---|---|
| `document` | OneToOneField(TaxDocument) | Associated document |
| `is_active` | BooleanField | Automation enabled flag |
| `client_email` | EmailField | Recipient email |
| `last_sent_at` | DateTimeField | Last send timestamp |
| `celery_task_id` | CharField | Active Celery task ID |

### FolderMonitoring

Controls automated monitoring of a client's upload folder for new files.

| Field | Type | Description |
|---|---|---|
| `document` | OneToOneField(TaxDocument) | Associated document |
| `client_name` | CharField | Client name for folder lookup |
| `is_active` | BooleanField | Monitoring enabled flag |
| `last_checked_at` | DateTimeField | Last check timestamp |
| `celery_task_id` | CharField | Active Celery task ID |

### OrganizerFolder

Tracks created client organizer folders.

| Field | Type | Description |
|---|---|---|
| `client_name` | CharField | Client name (unique) |
| `folder_path` | CharField | Absolute folder path |

### ProcessedFileHash

Prevents duplicate processing of identical PDFs.

| Field | Type | Description |
|---|---|---|
| `client_name` | CharField | Client name |
| `file_hash` | CharField | SHA-256 hash of file |
| `file_name` | CharField | Original filename |

### ActivityLog

Audit trail for all user and system actions.

| Field | Type | Description |
|---|---|---|
| `user` | ForeignKey(User) | Acting user (nullable for system) |
| `document` | ForeignKey(TaxDocument) | Related document |
| `action` | CharField | `document_upload`, `email_sent`, `document_downloaded` |
| `description` | TextField | Human-readable description |
| `timestamp` | DateTimeField | Event timestamp |

---

## AI Extraction Pipeline

The extraction pipeline in `services.py` operates in two GPT-4o Vision API calls per page.

### Step 1: Form Identification

Each PDF page is converted to a base64-encoded PNG at 300 DPI using `pdf2image`. The image is sent to GPT-4o with a structured prompt that returns:

```json
{
  "forms_identified": ["personal_information", "dependents_and_wages"],
  "can_extract": true,
  "is_stop_page": false,
  "detected_tax_year": 2024,
  "reason_if_cannot": ""
}
```

The pipeline automatically stops processing when it encounters the "Itemized Deductions - Contributions" page (`is_stop_page: true`), which marks the end of the relevant organizer section.

### Step 2: Data Extraction

Based on the identified forms, a composite extraction prompt is assembled from modular sub-prompts (one per form type). The prompt instructs GPT-4o to return a structured JSON object containing only the sections present on that page.

### Extraction Output Schema (per page)

The extracted JSON per page can contain any combination of the following sections:

| Section Key | IRS Form |
|---|---|
| `personal_information` | Taxpayer / Spouse info |
| `dependents_and_wages` | W-2 |
| `interest_income` | 1099-INT |
| `dividend_income` | 1099-DIV |
| `brokerage_statement_details` | Brokerage / 1099-B |
| `ira_distributions` | 1099-R |
| `partnership_income` | Schedule K-1 (Partnership) |
| `s_corp_income` | Schedule K-1 (S-Corp) |
| `estate_trust_income` | Schedule K-1 (Estate/Trust) |
| `remic_income` | Schedule K-1 (REMIC) |
| `mortgage_interest` | Form 1098 |
| `medical_dental_expenses` | Itemized Deductions |
| `real_estate_taxes` | Itemized Deductions |
| `other_taxes_paid` | Itemized Deductions |
| `state_tax_refunds` | 1099-G |
| `other_income` | 1099-MISC / 1099-NEC |
| `social_security_benefits` | SSA-1099 |
| `student_loan_interest` | 1098-E |
| `education_expenses` | 1098-T |
| `rental_and_royalty_expenses` | Schedule E |
| `rental_and_royalty_property_equipment_depletion` | Schedule E |
| `contributions` | Itemized Deductions - Contributions |
| `business_income_and_cost_of_goods_sold` | Schedule C |
| `business_expenses_and_property_equipment` | Schedule C |
| `business_vehicle_and_listed_property` | Schedule C |

### Post-Extraction Processing

After raw extraction, the pipeline:

1. Cleans and normalizes the JSON (removes nulls, trims whitespace, validates TSJ codes for dividend rows)
2. Generates a flat `summary_data` dictionary aggregating all pages
3. Generates `required_forms_json`: a list of individually required form instances derived from the summary (one entry per employer, payer, entity, or account)
4. Generates a `logic` report comparing required forms against files present in the sorted folder using fuzzy filename matching

---

## Supported Tax Forms

### Organizer Extraction (organizer_extraction_app)

These forms are extracted from multi-page tax organizer PDFs:

| Form | Description |
|---|---|
| W-2 | Wages and Tax Statement |
| 1099-INT | Interest Income |
| 1099-DIV | Dividends and Distributions |
| 1099-R | IRA / Pension / Annuity Distributions |
| 1099-MISC | Miscellaneous Income |
| 1099-NEC | Nonemployee Compensation |
| 1099-K | Payment Card Transactions |
| 1099-G | State Tax Refunds |
| 1099-Q | Qualified Education Payments |
| 1098 | Mortgage Interest |
| 1098-E | Student Loan Interest |
| 1098-T | Tuition Statement |
| SSA-1099 | Social Security Benefits |
| Schedule K-1 | Partnership / S-Corp / Estate / Trust / REMIC |
| Schedule E | Rental and Royalty Income and Expenses |
| Schedule C | Business Income and Expenses |

### Individual Form Detection (forms app)

These forms are detected and extracted from individually uploaded client PDFs:

| Form | Extractor Class |
|---|---|
| W-2 | FormW2Extractor |
| 1099-INT / 1099-B | Form1099INTExtractor |
| 1099-DIV | Form1099DIVExtractor |
| 1099-R | Form1099RExtractor |
| 5498 | Form5498Extractor |
| SSA-1099 | FormSSA1099Extractor |
| 8949 | Form8949Extractor |
| 1099-NEC | Form1099NECExtractor |
| 1099-MISC | Form1099MISCExtractor |
| 1099-K | Form1099KExtractor |
| 1098 | Form1098Extractor |
| 1098-T | Form1098TExtractor |
| 1098-E | Form1098EExtractor |
| 1099-Q | Form1099QExtractor |

---

## Folder Structure and File Management

The system creates and manages client-specific folders under `MEDIA_ROOT`:

```
media/
  upload_<FirstName>_<LastName>/       # Client's uploaded individual PDFs
  Sorted_<FirstName>_<LastName>/       # AI-classified and renamed sorted forms
  unsorted_<FirstName>_<LastName>/     # Unclassified or manually moved forms
```

### Sorted Filename Convention

Files moved to the sorted folder are renamed using a standardized format:

```
<NumericPrefix>_<FORM_TYPE>_<TAXPAYER_NAME>_<PAYER_NAME>.pdf
```

Example: `01_W-2_MANUBHAI_PATEL_MUNNI_LLC.pdf`

The numeric prefix follows the canonical IRS form ordering:

| Prefix | Form |
|---|---|
| 01 | W-2 |
| 02 | 1099-INT |
| 03 | 1099-DIV |
| 04 | 1099-R |
| 05 | 1099-MISC |
| 06 | 1099-NEC |
| 07 | 1099-K |
| 08 | 1099-G |
| 09 | 1099-Q |
| 10 | 1098 |
| 11 | 1098-E |
| 12 | 1098-T |
| 13 | Brokerage |
| 14 | Partnership K-1 |
| 15 | S-Corp K-1 |
| 16 | Estate/Trust K-1 |
| 17 | REMIC K-1 |
| 18 | SSA-1099 |
| 19 | 5498 |

### Duplicate Prevention

SHA-256 file hashes are stored in `ProcessedFileHash`. Before processing any file, the system checks whether the hash already exists for that client, preventing reprocessing of identical PDFs.

### Logic Report

After extraction, the system generates a `logic` JSON field on `ExtractedData` that maps each expected document (derived from the summary) to its status:

```json
{
  "status": "success",
  "sorted_folder": "Sorted_John_Doe",
  "total_documents": 12,
  "sorted_count": 8,
  "required_count": 4,
  "data": {
    "W-2 (Wages) - John Doe (Taxpayer) - ABC Corp": {
      "status": "sorted",
      "matched_file": "01_W-2_JOHN_DOE_ABC_CORP.pdf",
      "confidence": 0.87
    },
    "1099-INT (Interest Income) - John Doe (Taxpayer) - Heritage Bank": {
      "status": "required",
      "matched_file": null,
      "confidence": 0.0
    }
  }
}
```

Matching uses a combined fuzzy scoring algorithm (keyword overlap + SequenceMatcher similarity).

---

## Asynchronous Task System

Celery workers connected to Redis handle background tasks defined in `tasks.py`:

| Task | Trigger | Description |
|---|---|---|
| `send_document_reminder_email` | Manual or scheduled | Sends reminder email listing unsorted files; reschedules itself every 60 seconds if automation is active |
| `send_pending_documents_email` | Manual or scheduled | Sends email listing missing required forms and unprocessed files; reschedules itself every 60 seconds if active |
| `process_client_upload_folder` | Manual via view | Processes all PDFs in a client's upload folder using the forms app; stops at 85% completion threshold |
| `monitor_client_folder` | Toggle via UI | Polls client upload folder every 30 seconds for new files and processes them |
| `cleanup_inactive_automations` | Periodic | Logs count of inactive email automations |

Document processing on upload uses a Python daemon thread (not Celery) for immediate background execution without requiring a running worker.

---

## Email Automation

Email delivery uses Azure Communication Services (`azure-communication-email`).

### Email Types

| Type | Template | Trigger |
|---|---|---|
| Tax Data Summary | `email/send_email.html` | Manual send from summary page |
| Pending Documents Reminder | `email/pending_reminder_email.html` | Manual or automated via `EmailAutomation` |

### Open Tracking

Every outgoing email includes a 1x1 transparent GIF pixel with a unique UUID-based tracking URL:

```
/tax/track-email/<tracking_id>/
```

When the pixel is loaded, `EmailTracking.is_opened` is set to `True` and `opened_at` is recorded.

### Pending Documents Email Content

The pending documents email dynamically generates:

- A list of missing required forms (from `required_forms_json`) with owner (Taxpayer/Spouse/Joint) and payer/entity names
- A list of unprocessed files remaining in the client's upload folder (files not yet hashed in `ProcessedFileHash`)

---

## User Management and Roles

The system extends Django's built-in authentication with a `UserProfile` model.

### Roles

| Role | Permissions |
|---|---|
| `admin` | Full access: user CRUD, all documents, all admin features |
| `tax_preparer` | View all documents; cannot manage users |
| (no profile / client) | View own documents only |

Role enforcement is implemented via the `@role_required` decorator in `decorators.py`.

### User Management Views (Admin Only)

| Action | URL |
|---|---|
| List users | `/users/` |
| Create user | `/users/create/` |
| Edit user | `/users/<id>/edit/` |
| View user detail | `/users/<id>/` |
| Delete user | `/users/<id>/delete/` |

User creation and editing use Django REST serializers for validation before database writes.

---

## API Endpoints

All endpoints are prefixed under the application URL configuration. Authentication is required for all endpoints unless noted.

### Document Management

| Method | URL | Description |
|---|---|---|
| GET, POST | `/upload/` | Upload one or more PDF files to S3 |
| GET, POST | `/process/<id>/` | Trigger or check processing for a document |
| GET | `/results/<id>/` | View per-page extraction results |
| GET | `/summary/<id>/` | View aggregated summary and logic report |
| GET | `/download/<id>/` | Download extracted data as JSON |
| GET | `/download-summary/<id>/` | Download summary as plain text |
| GET | `/download-pdf/<id>/` | Download original PDF (S3 presigned URL or local) |
| GET | `/` | Paginated document list with taxpayer name search |
| GET | `/status/` | Check processing status of recent documents |

### Email

| Method | URL | Description |
|---|---|---|
| POST | `/send-email/<id>/` | Send extraction summary email via Azure |
| POST | `/send-pending-docs/<id>/` | Send pending documents email immediately |
| POST | `/toggle-automation/<id>/` | Enable or disable periodic email automation |
| GET | `/track-email/<tracking_id>/` | Email open tracking pixel (no auth required) |

### PDF Sorting

| Method | URL | Description |
|---|---|---|
| GET | `/required-forms-json/<id>/` | Get required forms list for a document |
| POST | `/process-forms-app/<id>/` | Trigger forms app processing for client upload folder |
| GET | `/client-required-forms/<taxpayer_name>/` | Get required form types for a client |
| GET | `/get-pdf-lists/<taxpayer_name>/` | List sorted and unsorted PDFs for a client |
| POST | `/move-pdf/` | Move a PDF between sorted and unsorted folders |
| POST | `/toggle-folder-monitoring/<id>/` | Enable or disable folder monitoring |

### Custom Fields

| Method | URL | Description |
|---|---|---|
| POST | `/add-custom-field/<id>/` | Add a custom metadata field to extracted data |
| POST | `/delete-custom-field/<id>/` | Remove a custom metadata field by index |

### User Management (Admin Only)

| Method | URL | Description |
|---|---|---|
| GET | `/users/` | List all users |
| GET, POST | `/users/create/` | Create a new user |
| GET, POST | `/users/<id>/edit/` | Edit an existing user |
| GET | `/users/<id>/` | View user details |
| GET, POST | `/users/<id>/delete/` | Delete a user |

### Forms App

| Method | URL | Description |
|---|---|---|
| GET | `/forms/stats/` | Get global sorted/unsorted processing statistics |
| GET | `/forms/pdf-lists/<client_name>/` | Get PDF lists for a client |
| GET | `/forms/client-summary/` | Get summary data and form requirements for a client |
| POST | `/forms/move-pdf/` | Move PDF between folders |
| POST | `/forms/upload/` | Upload and extract individual IRS form PDFs |

---

## Configuration

### Environment Variables

Configure these in `organizer_extraction/local_settings.py` (copy from `local_settings.py.example`):

| Variable | Description |
|---|---|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | Debug mode flag |
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `AWS_REGION` | AWS region (e.g., `us-east-1`) |
| `AWS_STORAGE_BUCKET_NAME` | S3 bucket name |
| `OPENAI_API_KEY` | OpenAI API key |
| `EMAIL_SERVICE` | Azure Communication Services connection string |
| `SENDER_ADDRESS` | Verified sender email address |
| `SITE_URL` | Base URL for email tracking links (e.g., `https://yourdomain.com`) |

### Database (settings.py)

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "organizer_extraction",
        "USER": "postgres",
        "PASSWORD": "root",
        "HOST": "localhost",
        "PORT": "5432",
    }
}
```

### Celery (settings.py)

```python
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
```

---

## Prerequisites

- Python 3.10+
- PostgreSQL 13+
- Redis (for Celery broker)
- AWS account with S3 bucket
- OpenAI API key with GPT-4o access
- Azure Communication Services resource with a verified sender domain
- `poppler` system library (required by `pdf2image` for PDF rendering)

---

## Quick Start with Docker

```bash
git clone <repo-url>
cd AI-Automation/AI-Automation

docker build -t tax-extractor .
docker run -p 8092:8092 --env-file .env tax-extractor
```

Access at `http://localhost:8092`.

Configure `.env` with all required credentials before running. The Docker image uses `python:3.10-slim-bullseye` with `wkhtmltopdf` and Pango libraries pre-installed. Gunicorn runs with 2 workers and a 300-second timeout.

---

## Local Development Setup

```bash
git clone <repo-url>
cd AI-Automation/AI-Automation

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt

# Create the PostgreSQL database
# psql -U postgres -c "CREATE DATABASE organizer_extraction;"

python manage.py migrate
python manage.py createsuperuser
```

Start required services in separate terminals:

```bash
# Terminal 1: Redis
redis-server

# Terminal 2: Celery worker
celery -A organizer_extraction worker -l info

# Terminal 3: Django development server
python manage.py runserver
```

Access at `http://localhost:8000`.

---

## Management Commands

| Command | Description |
|---|---|
| `python manage.py migrate` | Apply database migrations |
| `python manage.py createsuperuser` | Create an admin user |
| `python manage.py process_queue --continuous` | Process all pending documents continuously |
| `python manage.py populate_logic` | Regenerate logic data for all completed documents |
| `python manage.py start_celery` | Start the Celery worker via management command |

---

## Admin Interface

Access at `/admin/` with superuser credentials.

| Model | Admin Features |
|---|---|
| `TaxDocument` | View status, file info, S3 key, upload timestamp |
| `ExtractedData` | View raw JSON data, summary, logic, custom fields, form counts |
| `EmailTracking` | View open status, tracking ID, timestamps |
| `EmailAutomation` | View active automations, last sent timestamps |
| `ActivityLog` | Full audit trail with user, action, document, timestamp |
| `ProcessedFileHash` | View processed file hashes per client |
| `OrganizerFolder` | View created client folders |
| `FolderMonitoring` | View active monitoring configurations |
| `UserProfile` | View and edit user roles |
| `FormProcessingStat` | View global sorted/unsorted counts from forms app |

---

## Contributing

1. Fork the repository.
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Commit your changes: `git commit -m 'Add your feature description'`
4. Push to the branch: `git push origin feature/your-feature-name`
5. Open a Pull Request.

Follow Django PEP 8 style conventions. All new views must use `@login_required` and appropriate role decorators. New Celery tasks must include `bind=True` and `max_retries=3` with retry on exception.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
