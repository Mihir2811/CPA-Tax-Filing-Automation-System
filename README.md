# Tax Document Organizer Extraction System

## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [Technology Stack](#technology-stack)
- [Prerequisites](#prerequisites)
- [Quick Start with Docker](#quick-start-with-docker)
- [Local Development Setup](#local-development-setup)
- [Usage](#usage)
- [Management Commands](#management-commands)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Admin Interface](#admin-interface)
- [Contributing](#contributing)
- [License](#license)

## Overview
The Tax Document Organizer Extraction System is a comprehensive Django-based web application designed for tax professionals and preparers. It automates the processing of tax organizer PDFs by leveraging AI-powered form extraction, intelligent document sorting, and automated client communication workflows.

Key functionalities include:
- AI extraction of IRS tax forms (W-2, 1099 series, 1098, etc.) from multi-page PDFs.
- Automatic creation of client-specific organizer folders with sorted/unsorted form categorization.
- Asynchronous processing queues, S3 integration, and email automation with open tracking.
- Role-based user management (admin, tax preparer, client) with activity logging.

The system processes uploaded tax organizers, identifies taxpayer information, extracts form details (form number, tax year, payer/recipient names), generates summaries and logic reports, and organizes files into standardized folder structures for tax preparation workflows.

## Features
- **AI-Powered Form Extraction**: Uses OpenAI Vision API to accurately extract IRS form data from PDF pages, distinguishing tax year from revision dates.
- **Multi-Page PDF Processing**: Handles complex tax organizers with page-wise extraction, error handling, and summary generation.
- **Automatic Organizer Folders**: Creates client-named folders and sorts forms into `sorted/` and `unsorted/` subdirectories with standardized naming.
- **S3 Integration**: Direct uploads/downloads with presigned URLs; temporary local processing for S3 files.
- **Asynchronous Queue Processing**: Celery/Redis-powered background jobs for scalable document processing.
- **Email Automation**: Azure Communication Services integration with open tracking, pending documents reminders, and customizable summaries.
- **Progress Tracking**: Completion percentage based on required vs. sorted forms; real-time status updates.
- **User Management**: Admin CRUD for users with roles (admin, tax preparer, client); profile extension.
- **Activity Logging**: Comprehensive audit trail of all user actions and system events.
- **Custom Fields**: Add/edit custom metadata per document.
- **Folder Monitoring**: Automated detection of new files in client folders.
- **Admin Dashboard**: Full Django admin for documents, extractions, emails, logs.
- **Duplicate Prevention**: File hash tracking to avoid reprocessing identical PDFs.

## Technology Stack
- **Backend**: Django 5.2.8
- **Database**: PostgreSQL
- **Task Queue**: Celery 5.5.3 + Redis 7.0.1
- **AI/ML**: OpenAI API (vision model for form extraction)
- **PDF Processing**: PyMuPDF 1.26.6, pdf2image 1.17.0, Pillow 12.0.0
- **Storage**: AWS S3 (boto3), Local media files
- **Email**: Azure Communication Services
- **Deployment**: Docker (Python 3.10-slim, Gunicorn)
- **Frontend**: Django templates (Bootstrap-styled tax_extractor views)
- **Other**: Django REST serializers, JSONField for extracted data

## Prerequisites
- Python 3.10+
- PostgreSQL 13+ (default: `organizer_extraction` DB, `postgres/root@localhost:5432`)
- Redis (default: `localhost:6379/0`)
- AWS credentials (S3 access for uploads)
- OpenAI API key
- Azure Communication Services endpoint/access key
- Git

## Quick Start with Docker
```bash
# Clone repository
git clone <repo-url>
cd AI-Automation/AI-Automation

# Build and run
docker build -t tax-extractor .
docker run -p 8092:8092 --env-file .env tax-extractor
```
Access at `http://localhost:8092`.

**Note**: Configure `.env` with AWS/OpenAI/Azure creds before running.

## Local Development Setup
```bash
# Clone and setup virtualenv
git clone <repo-url>
cd AI-Automation/AI-Automation
python -m venv venv
source venv/bin/activate  # Windows: venv\\Scripts\\activate

# Install dependencies
pip install -r requirements.txt

# Database setup (create DB first)
python manage.py migrate
python manage.py createsuperuser

# Start services (separate terminals)
redis-server  # or brew services start redis
celery -A organizer_extraction worker -l info
python manage.py runserver
```
Access at `http://localhost:8000`.

**Configure `organizer_extraction/local_settings.py`** for production keys.

## Usage
1. **Login/Register** (`/accounts/login/` redirects to `/documents/`).
2. **Upload Documents** (`/upload/`): Multi-PDF upload (auto-processes).
3. **View Queue** (`/documents/`): Paginated list, search by taxpayer, progress %.
4. **Process** (auto/background), view **Results** (`/results/<id>/`), **Summary** (`/summary/<id>/`).
5. **Download**: JSON/text/PDF.
6. **Sort Forms**: View sorted/unsorted PDFs, move between folders (`tax_extractor/forms_comparison.html`).
7. **Email**: Send summaries, toggle automation/monitoring.
8. **Admin** (`/admin/`): Manage users/docs/emails/logs.

Key endpoints:
- `POST /upload/` - Upload/process
- `GET /documents/` - List with search/pagination
- `GET/POST /results/<id>/` - View/process results
- `POST /send-email/<id>/` - Send extraction email

## Management Commands
```bash
python manage.py process_queue --continuous  # Process pending docs continuously
python manage.py populate_logic              # Generate logic data
python manage.py start_celery                 # Start Celery worker
python manage.py migrate                     # DB migrations
```

## Architecture
```
Upload (S3/Local) → Celery Queue → PDF Processing (PyMuPDF) → OpenAI Extraction → 
JSON Summary/Logic → DB (ExtractedData) → Organizer Folders → Email (Azure)
                          ↓
Activity Logs → User Dashboard → Admin Panel
```
- Extraction prompt: Structured JSON output per page.
- Folder structure: `<Taxpayer>/sorted/<FormPrefix>_<TYPE>_<TAXPAYER>_Unknown.pdf` | `unsorted/`.

## Configuration
Copy `organizer_extraction/local_settings.py.example` → `local_settings.py`:
```
AWS_ACCESS_KEY_ID=...
OPENAI_API_KEY=...
EMAIL_SERVICE="endpoint=...;accesskey=..."
SENDER_ADDRESS="..."
```
Redis/Postgres in `settings.py`.

## Admin Interface
`/admin/` - Full access to:
- TaxDocument/ExtractedData (statuses, previews)
- Users/Profiles
- EmailTracking/ActivityLog
- ProcessedFileHash/OrganizerFolder

## Contributing
1. Fork repository.
2. Create feature branch (`git checkout -b feature/AmazingFeature`).
3. Commit (`git commit -m 'Add some AmazingFeature'`).
4. Push (`git push origin feature/AmazingFeature`).
5. Open Pull Request.

Follow Django PEP8 style.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

