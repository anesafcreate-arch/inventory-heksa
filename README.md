# HEKSA Inventory

Internal inventory management application for Heksa Instrumen Sinergi calibration standard equipment.

V1 includes authenticated inventory entry, list/search/filter, equipment detail/history, barcode print/download, barcode scan/manual lookup, dashboard summaries, and admin-only settings for user and lab management.

## Tech Stack

- Backend: Python FastAPI
- Templates: Jinja2
- Frontend: HTML5, CSS3, Vanilla JavaScript
- Database: SQLite
- Authentication: secure session cookie with hashed session token
- Password hashing: PBKDF2-SHA256

No React, Vue, Next.js, or heavy frontend framework is used.

## Requirements

- Python 3.11+
- Packages listed in `requirements.txt`
- A modern browser for barcode scanning. Native camera scan uses the browser `BarcodeDetector` API when available.

## Installation

```powershell
pip install -r requirements.txt
```

## Run

```powershell
python -m uvicorn app:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

## Default Admin

The database initializes automatically on first startup.

- Username: `admin`
- Password: `Heksa123!`

Change this password before production use.

## Folder Structure

```text
inventory-heksa/
  app.py
  auth.py
  barcode_utils.py
  database.py
  heksa_inventory.db
  requirements.txt
  templates/
  static/
    css/
    img/
    js/
  modules/
```

## Database Notes

SQLite database file:

```text
heksa_inventory.db
```

Tables:

- `users`
- `labs`
- `equipment`
- `activity_logs`
- `sessions`

The app creates required tables and indexes automatically without destroying existing data. Important unique constraints include `users.username`, `equipment.equipment_no`, and `equipment.barcode`. `equipment.certificate_no` is indexed for search performance.

## Roles

`ADMIN` can:

- use dashboard, input, list, detail, edit, barcode print/download, scan barcode
- delete equipment
- access settings
- manage users
- manage labs

`USER` can:

- use dashboard, input, list, detail, edit, barcode print/download, scan barcode

`USER` cannot:

- delete equipment
- access settings
- manage users or labs

## Barcode

New generated barcodes use:

```text
HIS-CAL-000001
```

Existing stored barcode values are preserved and continue to work.

## Important Limitations

- Certificate PDF upload is not part of V1.
- Native camera scanning depends on browser support for `BarcodeDetector`. Manual barcode lookup is always available.
- SQLite is suitable for internal/lightweight deployment. For multi-branch or high-concurrency production use, consider PostgreSQL in V1.1+.
- HTTPS should be used in production so camera permissions and secure cookies work as expected.
