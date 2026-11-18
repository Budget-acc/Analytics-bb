# BookieBuddy Backend

A Django REST Framework backend for managing rental operations, direct product sales, and customization-based orders for shop-based businesses. The system supports multi-shop management with role-based access control, inventory tracking, financial ledger, and integrations with WhatsApp, Firebase, AWS S3, and Discord.

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation & Setup](#installation--setup)
- [Environment Variables](#environment-variables)
- [Running the Project Locally](#running-the-project-locally)
- [Celery & Background Tasks](#celery--background-tasks)
- [Scheduled Jobs (Celery Beat)](#scheduled-jobs-celery-beat)
- [External Integrations](#external-integrations)
  - [AWS S3 (File Storage)](#aws-s3-file-storage)
  - [Firebase (Push Notifications & Storage)](#firebase-push-notifications--storage)
  - [WhatsApp (Customer Messaging)](#whatsapp-customer-messaging)
  - [Discord (Error Logging)](#discord-error-logging)
- [API Versioning](#api-versioning)
- [Core Modules](#core-modules)
- [Role-Based Access Control](#role-based-access-control)
- [Deployment](#deployment)

---

## Tech Stack

| Category           | Technology                                            |
|--------------------|-------------------------------------------------------|
| Framework          | Django 5.1, Django REST Framework                     |
| Database           | PostgreSQL                                            |
| Task Queue         | Celery + Redis (broker), django-celery-beat (scheduler) |
| File Storage       | AWS S3 (via boto3 / django-storages)                  |
| Push Notifications | Firebase Cloud Messaging (FCM)                        |
| WhatsApp Messaging | WACTO Live API                                        |
| PDF Generation     | ReportLab                                             |
| Error Logging      | Discord Webhooks                                      |
| Authentication     | JWT (SimpleJWT with token blacklisting)               |
| Deployment         | Gunicorn                                              |

---

## Project Structure

```
booking-buddy-backend/
├── ERP/                     # Django project settings, URLs, middleware, utilities
│   ├── settings.py
│   ├── urls.py
│   ├── celery.py
│   ├── middleware/           # Custom JWT auth, thread-local, request logging
│   └── utils/               # Pagination, Discord logging, logging filters
├── authentication/          # User authentication, JWT tokens, refresh logic
├── booking_v2/              # Rental booking management (versioned APIs)
├── sales/                   # Direct product sales
├── services/                # Product/inventory management, S3 service
├── shop/                    # Shop management, multi-shop support
├── expenses_v2/             # Expense tracking
├── subscriptions/           # Subscription plans & feature access control
├── notifications/           # Firebase push notifications, device token management
├── whatsapp_integration/    # WhatsApp messaging via WACTO API
├── pdf_service/             # Async PDF report generation (ledger, transactions)
├── tailored/                # Customization/tailoring orders
├── manage.py
├── requirements.txt
└── overview.md              # Detailed business logic documentation
```

---

## Prerequisites

- **Python 3.10+**
- **PostgreSQL** (running instance)
- **Redis** (for Celery broker)
- **pip** (Python package manager)

---

## Installation & Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd booking-buddy-backend
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
```

Activate the virtual environment:

- **Windows:**
  ```bash
  venv\Scripts\activate
  ```
- **Linux/macOS:**
  ```bash
  source venv/bin/activate
  ```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Set Up Environment Variables

Create a `.env` file in the project root (see [Environment Variables](#environment-variables) section below).

### 5. Set Up the Database

Make sure PostgreSQL is running, then:

```bash
python manage.py migrate
```

### 6. Create a Superuser (Optional)

```bash
python manage.py createsuperuser
```

### 7. Collect Static Files

```bash
python manage.py collectstatic
```

---

## Environment Variables

Create a `.env` file in the project root.

---

## Running the Project Locally

### Start the Django Development Server

```bash
python manage.py runserver
```

The server will be available at `http://localhost:8000/`.

### Start Redis (required for Celery)

Make sure Redis is running on your machine:

- **Windows:** Use [Memurai](https://www.memurai.com/) or WSL with Redis installed
- **Linux/macOS:**
  ```bash
  redis-server
  ```

### Start Celery Worker

Open a new terminal (with the virtual environment activated):

```bash
celery -A ERP worker --loglevel=info
```

For PDF generation tasks, start workers with specific queues:

```bash
celery -A ERP worker --loglevel=info -Q pdf_generation,pdf_chunks,pdf_merge,pdf_main
```

### Start Celery Beat (Task Scheduler)

Open another terminal:

```bash
celery -A ERP beat --loglevel=info
```

---

## Celery & Background Tasks

The project uses **Celery** with **Redis** as the message broker and **Django DB** as the result backend.

### Task Queues

| Queue            | Purpose                          |
|------------------|----------------------------------|
| `default`        | Standard tasks                   |
| `pdf_generation` | Ledger & transaction PDF tasks   |
| `pdf_chunks`     | PDF chunk generation             |
| `pdf_merge`      | PDF chunk merging                |
| `pdf_main`       | Main PDF parallel task           |

### Key Celery Tasks

| Task                                            | Module                | Description                                    |
|-------------------------------------------------|-----------------------|------------------------------------------------|
| `notify_daily_bookings`                         | notifications         | Send daily pickup reminder notifications       |
| `notify_daily_returns`                          | notifications         | Send daily return reminder notifications       |
| `send_pickup_ready_notifications`               | whatsapp_integration  | WhatsApp pickup reminders                      |
| `send_return_reminder_notifications`            | whatsapp_integration  | WhatsApp return reminders                      |
| `send_booking_invoice_task`                     | whatsapp_integration  | Send booking invoice via WhatsApp              |
| `send_sales_invoice_task`                       | whatsapp_integration  | Send sales invoice via WhatsApp                |
| `generate_ledger_pdf_task`                      | pdf_service           | Generate ledger report PDFs                    |
| `generate_transaction_pdf_task`                 | pdf_service           | Generate transaction report PDFs               |
| `generate_pdf_parallel_task`                    | services              | Parallel product report PDF generation         |
| `cleanup_old_temp_files`                        | booking_v2            | Clean up temporary files (daily at 3 AM)       |
| `cleanup_old_booking_documents`                 | booking_v2            | Clean up old booking documents (daily at 2:30 AM) |

### Celery Configuration

- **Time zone:** Asia/Kolkata
- **Task time limit:** 5 minutes (hard), 4 min 40 sec (soft)
- **Worker prefetch multiplier:** 1 (fair task distribution)
- **Max tasks per child:** 50 (prevents memory leaks)
- **Result expiry:** 1 hour

---

## Scheduled Jobs (Celery Beat)

| Job Name                            | Schedule        | Description                                          |
|-------------------------------------|-----------------|------------------------------------------------------|
| `notify-daily-bookings`             | 8:00 AM daily   | Push notifications for today's pickups               |
| `notify-daily-returns`              | 7:00 PM daily   | Push notifications for today's returns               |
| `whatsapp-pickup-ready-notifications` | 8:30 AM daily | WhatsApp messages for pickup-ready orders            |
| `whatsapp-return-reminder-notifications` | 8:35 AM daily | WhatsApp reminders for upcoming returns           |
| `cleanup-temp-files-daily`          | 3:00 AM daily   | Remove old temporary files                           |
| `cleanup-old-booking-documents`     | 2:30 AM daily   | Remove expired booking documents (retention: 30 days)|

> Notification timings are configurable via environment variables.

---

## External Integrations

### AWS S3 (File Storage)

Used for storing uploaded files, product images, and generated PDFs.

- **Library:** `boto3` / `django-storages`
- **Features:** Multipart upload, presigned URLs (24-hour expiry for PDFs), connection pooling
- **Configuration:** Set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, and `AWS_S3_REGION_NAME` in `.env`
- **Service:** `services/s3_service.py` — Singleton S3 client with thread-safe initialization

### Firebase (Push Notifications & Storage)

Used for sending push notifications to iOS and Android devices via Firebase Cloud Messaging (FCM).

- **Library:** `firebase-admin`
- **Features:**
  - Device token management (register/remove per user/shop/platform)
  - Concurrent message delivery using `ThreadPoolExecutor`
  - Platform-specific configs for Android and APNS
  - Notification audit logging
- **Configuration:** Place `firebase_service_account.json` in the project root. Set `FIREBASE_SERVICE_ACCOUNT_PATH` in `.env`.

### WhatsApp (Customer Messaging)

Sends automated WhatsApp messages to customers using the **WACTO Live API** with pre-approved templates.

- **Features:**
  - Booking and sales invoice delivery (with PDF attachments)
  - Daily pickup-ready and return-reminder notifications
  - Template-based messaging with dynamic parameters
  - Message sending limits enforced via subscriptions
- **Flow:** API request → Celery task → WhatsAppService → WACTO API → Customer's WhatsApp
- **Configuration:** Set all `WHATSAPP_*` variables in `.env`. Enable reminders with `WHATSAPP_REMINDERS_ENABLED=true`.

### Discord (Error Logging)

Sends `WARNING` and above log messages to a Discord channel for real-time error monitoring.

- **Implementation:** Custom `DiscordHandler` logging handler in `ERP/utils/discord_logging.py`
- **Features:**
  - Messages formatted as code blocks
  - Chunked at 1900 characters (Discord limit)
  - 5-second timeout to prevent blocking
  - Attached to both `api_requests` and root loggers
- **Configuration:** Set `DISCORD_WEBHOOK_URL` in `.env`. If not set, Discord logging is disabled.

### Email (SMTP)

Email notifications are sent via Gmail SMTP.

- **Configuration:** Set `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` (app password) in `.env`.

---

## API Versioning

The API uses URL-based versioning:

| Version | Base Path   | Notes                     |
|---------|-------------|---------------------------|
| v1      | `/api/v1/`  | Initial API version       |
| v2      | `/api/v2/`  | Booking updates           |
| v3      | `/api/v3/`  | Current primary version   |
| v4      | `/api/v4/`  | Latest endpoints          |
| v5      | `/api/v5/`  | Booking-specific updates  |

### Key API Endpoints

| Prefix                         | Module               |
|--------------------------------|----------------------|
| `/api/v3/auth/`                | Authentication       |
| `/api/v3/service/`             | Services/Inventory   |
| `/api/v3/shop/`                | Shop Management      |
| `/api/v3/bookings/`            | Bookings             |
| `/api/v3/expenses/`            | Expenses             |
| `/api/v3/subscriptions/`       | Subscriptions        |
| `/api/v3/notifications/`       | Notifications        |
| `/api/v3/sales/`               | Sales                |
| `/api/v3/tailored/`            | Customization Orders |
| `/api/v3/pdf/`                 | PDF Reports          |
| `/api/token/refresh/`          | Token Refresh        |
| `/admin-dashboard/`            | Django Admin          |

---

## Core Modules

| Module               | Description                                                              |
|----------------------|--------------------------------------------------------------------------|
| **Bookings**         | Rental lifecycle management with pickup/return dates and cooling periods  |
| **Sales**            | Direct product sales with inventory deduction                            |
| **Tailored**         | Customization orders (customer-driven or shop-driven)                    |
| **Services**         | Product catalog, variants, inventory hierarchy                           |
| **Shop**             | Multi-shop management, shop settings, transfer history                   |
| **Expenses**         | Expense tracking, reflected in the financial ledger                      |
| **Subscriptions**    | Plan management, feature access control, usage limits                    |
| **Notifications**    | Firebase push notifications with device token management                 |
| **PDF Service**      | Async PDF generation (ledger, transactions) with task tracking           |
| **WhatsApp**         | Automated customer messaging via WACTO API                               |

---

## Role-Based Access Control

| Role      | Scope             | Capabilities                                                   |
|-----------|-------------------|----------------------------------------------------------------|
| **Owner** | All shops         | Full system access, financial visibility, staff restrictions    |
| **Manager** | Assigned shops  | Shop operations, performance data                              |
| **Staff** | Single shop       | Limited access, permissions controlled by the Owner            |

---

## Deployment

```
web: gunicorn ERP.wsgi
```

For production, ensure:

1. `DEBUG=False` in `.env`
2. Set proper `ALLOWED_HOSTS`
3. Configure `COOKIE_SECURE=True` and `COOKIE_SAMESITE=Lax`
4. Run Celery worker and beat as separate processes
5. Use a process manager (e.g., Supervisor) for Celery workers
6. Collect static files: `python manage.py collectstatic`

---

## Logging

The project uses Django's logging framework with multiple handlers:

| Handler             | Output                          | Level   |
|---------------------|---------------------------------|---------|
| Console             | Terminal (debug mode only)      | INFO    |
| File                | `logs/django.log` (1MB rotate)  | WARNING |
| API Requests File   | `logs/api_requests.log` (2MB)   | DEBUG   |
| Discord             | Discord webhook (if configured) | WARNING |

Log files are stored in the `logs/` directory (auto-created on startup).
