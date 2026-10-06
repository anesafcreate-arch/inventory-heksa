from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import re

from fastapi import Cookie, Depends, FastAPI, Form, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from auth import (
    create_session_token,
    from_db_datetime,
    hash_password,
    hash_session_token,
    session_expiry,
    to_db_datetime,
    utc_now,
    verify_password,
)
from barcode_utils import generate_barcode_png
from database import get_db, init_db


BASE_DIR = Path(__file__).resolve().parent
SESSION_COOKIE = "heksa_session"

app = FastAPI(title="HEKSA Inventory")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
APP_TIMEZONE = ZoneInfo("Asia/Jakarta")
BARCODE_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
MAX_SPECIFICATION_LENGTH = 500
PAGE_SIZE = 10


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in {403, 404}:
        title = "Akses Ditolak" if exc.status_code == 403 else "Halaman Tidak Ditemukan"
        message = (
            "Anda tidak memiliki akses ke halaman ini."
            if exc.status_code == 403
            else "Halaman yang dicari tidak tersedia atau sudah dipindahkan."
        )
        return templates.TemplateResponse(
            "error_page.html",
            {"request": request, "status_code": exc.status_code, "title": title, "message": message},
            status_code=exc.status_code,
        )
    return templates.TemplateResponse(
        "error_page.html",
        {
            "request": request,
            "status_code": exc.status_code,
            "title": "Terjadi Kesalahan",
            "message": "Permintaan tidak dapat diproses saat ini.",
        },
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return templates.TemplateResponse(
        "error_page.html",
        {
            "request": request,
            "status_code": 500,
            "title": "Terjadi Kesalahan",
            "message": "Aplikasi mengalami kendala. Silakan coba lagi beberapa saat.",
        },
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def get_current_user(
    heksa_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
):
    if not heksa_session:
        return None

    token_hash = hash_session_token(heksa_session)
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                users.id,
                users.name,
                users.username,
                users.role,
                users.status,
                sessions.expires_at
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

    if row is None or row["status"] != "active":
        return None

    expires_at = from_db_datetime(row["expires_at"])
    if expires_at is None or expires_at <= utc_now():
        with get_db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        return None

    return row


def require_user(user=Depends(get_current_user)):
    if user is None:
        return RedirectResponse(
            url="/login",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return user


def jakarta_today():
    return datetime.now(APP_TIMEZONE).date()


def parse_date(value):
    if not value:
        return None
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def calculate_equipment_status(expiry_date_value):
    expiry_date = parse_date(expiry_date_value)
    if expiry_date is None:
        return "active"

    today = jakarta_today()
    if expiry_date < today:
        return "expired"
    if expiry_date <= today + timedelta(days=30):
        return "expiring_soon"
    return "active"


def status_label(status_value):
    labels = {
        "active": "Active",
        "expiring_soon": "Expiring Soon",
        "expired": "Expired",
    }
    return labels.get(status_value, status_value.replace("_", " ").title())


templates.env.filters["status_label"] = status_label


def format_date_id(value):
    parsed_date = parse_date(value)
    if parsed_date is None:
        return "-"
    return parsed_date.strftime("%d %b %Y")


def format_datetime_id(value):
    if not value:
        return "-"
    try:
        parsed_datetime = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return parsed_datetime.strftime("%d %b %Y %H:%M")


templates.env.filters["date_id"] = format_date_id
templates.env.filters["datetime_id"] = format_datetime_id


def get_labs():
    with get_db() as conn:
        return conn.execute(
            "SELECT id, lab_name FROM labs WHERE status = ? ORDER BY lab_name",
            ("active",),
        ).fetchall()


def empty_equipment_form():
    return {
        "equipment_no": "",
        "equipment_name": "",
        "certificate_no": "",
        "specification": "",
        "lab_id": "",
        "calibration_date": "",
        "expiry_date": "",
        "barcode": "",
    }


def normalize_equipment_form(
    equipment_no,
    equipment_name,
    certificate_no,
    specification,
    lab_id,
    calibration_date,
    expiry_date,
    barcode,
):
    return {
        "equipment_no": equipment_no.strip(),
        "equipment_name": equipment_name.strip(),
        "certificate_no": certificate_no.strip(),
        "specification": specification.strip(),
        "lab_id": lab_id.strip(),
        "calibration_date": calibration_date.strip(),
        "expiry_date": expiry_date.strip(),
        "barcode": barcode.strip().upper(),
    }


def validate_equipment_form(form_data):
    errors = {}
    required_fields = {
        "equipment_no": "No Alat wajib diisi.",
        "equipment_name": "Nama Alat wajib diisi.",
        "certificate_no": "No Sertifikat wajib diisi.",
        "specification": "Spesifikasi Alat wajib diisi.",
        "lab_id": "Lab wajib dipilih.",
        "barcode": "Barcode wajib diisi.",
    }
    for field, message in required_fields.items():
        if not form_data[field]:
            errors[field] = message

    if len(form_data["specification"]) > MAX_SPECIFICATION_LENGTH:
        errors["specification"] = "Spesifikasi maksimal 500 karakter."

    if form_data["barcode"] and not BARCODE_PATTERN.fullmatch(form_data["barcode"]):
        errors["barcode"] = "Barcode hanya boleh memakai huruf, angka, dan tanda hubung."

    if form_data["lab_id"]:
        try:
            int(form_data["lab_id"])
        except ValueError:
            errors["lab_id"] = "Lab tidak valid."

    for field in ("calibration_date", "expiry_date"):
        if form_data[field] and parse_date(form_data[field]) is None:
            errors[field] = "Format tanggal tidak valid."

    return errors


def form_from_equipment(equipment):
    return {
        "equipment_no": equipment["equipment_no"] or "",
        "equipment_name": equipment["equipment_name"] or "",
        "certificate_no": equipment["certificate_no"] or "",
        "specification": equipment["specification"] or "",
        "lab_id": str(equipment["lab_id"] or ""),
        "calibration_date": equipment["calibration_date"] or "",
        "expiry_date": equipment["expiry_date"] or "",
        "barcode": equipment["barcode"] or "",
    }


def render_equipment_form(
    request,
    user,
    form_data=None,
    errors=None,
    success_equipment=None,
    mode="create",
    equipment=None,
):
    is_edit = mode == "edit"
    return templates.TemplateResponse(
        "equipment_form.html",
        {
            "request": request,
            "user": user,
            "active_page": "list" if is_edit else "input",
            "page_title": "Edit Data Alat" if is_edit else "Input Data Alat",
            "labs": get_labs(),
            "form": form_data or empty_equipment_form(),
            "errors": errors or {},
            "success_equipment": success_equipment,
            "mode": mode,
            "equipment": equipment,
            "form_action": f"/equipment/{equipment['id']}/edit" if is_edit and equipment else "/equipment/new",
            "cancel_url": f"/equipment/{equipment['id']}/detail" if is_edit and equipment else "/dashboard",
            "form_eyebrow": "List Alat / Edit Data" if is_edit else "Input Alat / Tambah Data",
            "submit_label": "Simpan Perubahan" if is_edit else "Simpan Data Alat",
        },
    )


def generate_unique_barcode():
    with get_db() as conn:
        last_id = conn.execute("SELECT COALESCE(MAX(id), 0) AS last_id FROM equipment").fetchone()["last_id"]
        candidate_number = last_id + 1
        while True:
            barcode = f"HIS-CAL-{candidate_number:06d}"
            exists = conn.execute(
                "SELECT id FROM equipment WHERE barcode = ?",
                (barcode,),
            ).fetchone()
            if exists is None:
                return barcode
            candidate_number += 1


def refresh_equipment_statuses():
    with get_db() as conn:
        rows = conn.execute("SELECT id, expiry_date, status FROM equipment").fetchall()
        for row in rows:
            calculated_status = calculate_equipment_status(row["expiry_date"])
            if calculated_status != row["status"]:
                conn.execute(
                    "UPDATE equipment SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (calculated_status, row["id"]),
                )


def build_equipment_list(filters):
    refresh_equipment_statuses()
    page = max(filters["page"], 1)
    where = []
    params = []

    if filters["q"]:
        search = f"%{filters['q']}%"
        where.append(
            """
            (
                equipment.equipment_no LIKE ?
                OR equipment.equipment_name LIKE ?
                OR equipment.certificate_no LIKE ?
                OR equipment.barcode LIKE ?
                OR labs.lab_name LIKE ?
            )
            """
        )
        params.extend([search, search, search, search, search])

    if filters["lab_id"]:
        where.append("equipment.lab_id = ?")
        params.append(filters["lab_id"])

    if filters["status_filter"]:
        where.append("equipment.status = ?")
        params.append(filters["status_filter"])

    if filters["calibration_date"]:
        where.append("equipment.calibration_date = ?")
        params.append(filters["calibration_date"])

    if filters["expiry_date"]:
        where.append("equipment.expiry_date = ?")
        params.append(filters["expiry_date"])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    offset = (page - 1) * PAGE_SIZE

    with get_db() as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM equipment
            LEFT JOIN labs ON labs.id = equipment.lab_id
            {where_sql}
            """,
            params,
        ).fetchone()["total"]
        rows = conn.execute(
            f"""
            SELECT
                equipment.id,
                equipment.equipment_no,
                equipment.equipment_name,
                equipment.certificate_no,
                equipment.specification,
                equipment.calibration_date,
                equipment.expiry_date,
                equipment.barcode,
                equipment.status,
                labs.lab_name
            FROM equipment
            LEFT JOIN labs ON labs.id = equipment.lab_id
            {where_sql}
            ORDER BY datetime(equipment.created_at) DESC, equipment.id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, PAGE_SIZE, offset],
        ).fetchall()

    total_pages = max(math.ceil(total / PAGE_SIZE), 1)
    page = min(page, total_pages)
    page_numbers = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))

    return {
        "equipment_rows": rows,
        "total_records": total,
        "page": page,
        "total_pages": total_pages,
        "page_numbers": page_numbers,
    }


def list_query_url(filters, **updates):
    query = {
        "q": filters["q"],
        "lab_id": filters["lab_id"],
        "status": filters["status_filter"],
        "calibration_date": filters["calibration_date"],
        "expiry_date": filters["expiry_date"],
        "page": filters["page"],
    }
    query.update(updates)
    query = {key: value for key, value in query.items() if value not in ("", None)}
    return f"/equipment?{urlencode(query)}" if query else "/equipment"


def is_admin(user):
    return str(user["role"]).lower() in {"admin", "administrator"}


def require_admin_user(request, user):
    if isinstance(user, RedirectResponse):
        return user
    if not is_admin(user):
        return templates.TemplateResponse(
            "unauthorized.html",
            {
                "request": request,
                "user": user,
                "active_page": "",
                "page_title": "Akses Ditolak",
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return None


def role_label(role_value):
    if str(role_value).lower() in {"admin", "administrator"}:
        return "ADMIN"
    return "USER"


templates.env.filters["role_label"] = role_label


def active_admin_count(conn):
    return conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM users
        WHERE lower(role) IN ('admin', 'administrator') AND status = 'active'
        """
    ).fetchone()["total"]


def would_remove_last_active_admin(conn, target_user):
    return (
        target_user is not None
        and str(target_user["role"]).lower() in {"admin", "administrator"}
        and target_user["status"] == "active"
        and active_admin_count(conn) <= 1
    )


def add_admin_activity(conn, user_id, action, old_value=None, new_value=None):
    conn.execute(
        """
        INSERT INTO activity_logs (user_id, equipment_id, action, old_value, new_value)
        VALUES (?, NULL, ?, ?, ?)
        """,
        (
            user_id,
            action,
            json.dumps(old_value, default=str) if isinstance(old_value, (dict, list)) else old_value,
            json.dumps(new_value, default=str) if isinstance(new_value, (dict, list)) else new_value,
        ),
    )


def settings_context(request, user, active_tab="users", message=None, error=None):
    with get_db() as conn:
        users = conn.execute(
            """
            SELECT id, name, username, role, status, created_at, updated_at
            FROM users
            ORDER BY lower(name), id
            """
        ).fetchall()
        labs = conn.execute(
            """
            SELECT
                labs.id,
                labs.lab_name,
                labs.status,
                COUNT(equipment.id) AS equipment_count
            FROM labs
            LEFT JOIN equipment ON equipment.lab_id = labs.id
            GROUP BY labs.id
            ORDER BY lower(labs.lab_name), labs.id
            """
        ).fetchall()

    return {
        "request": request,
        "user": user,
        "active_page": "settings",
        "page_title": "Settings",
        "active_tab": active_tab,
        "users": users,
        "labs": labs,
        "message": message,
        "error": error,
        "roles": ("admin", "user"),
        "statuses": ("active", "inactive"),
    }


def render_settings(request, user, active_tab="users", message=None, error=None):
    return templates.TemplateResponse(
        "settings.html",
        settings_context(
            request,
            user,
            active_tab=active_tab,
            message=message,
            error=error,
        ),
    )


def get_equipment_or_none(equipment_id):
    refresh_equipment_statuses()
    with get_db() as conn:
        return conn.execute(
            """
            SELECT equipment.*, labs.lab_name, users.name AS created_by_name
            FROM equipment
            LEFT JOIN labs ON labs.id = equipment.lab_id
            LEFT JOIN users ON users.id = equipment.created_by
            WHERE equipment.id = ?
            """,
            (equipment_id,),
        ).fetchone()


def equipment_field_label(field_name):
    labels = {
        "equipment_no": "No Alat",
        "equipment_name": "Nama Alat",
        "certificate_no": "No Sertifikat",
        "specification": "Spesifikasi",
        "lab_id": "Lab",
        "calibration_date": "Tanggal Kalibrasi",
        "expiry_date": "Masa Berlaku",
        "barcode": "Barcode",
        "status": "Status",
    }
    return labels.get(field_name, field_name.replace("_", " ").title())


def parse_json_value(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def build_activity_items(equipment_id):
    with get_db() as conn:
        lab_lookup = {
            row["id"]: row["lab_name"]
            for row in conn.execute("SELECT id, lab_name FROM labs").fetchall()
        }
        rows = conn.execute(
            """
            SELECT
                activity_logs.id,
                activity_logs.action,
                activity_logs.old_value,
                activity_logs.new_value,
                activity_logs.created_at,
                users.name AS user_name,
                users.role AS user_role
            FROM activity_logs
            LEFT JOIN users ON users.id = activity_logs.user_id
            WHERE activity_logs.equipment_id = ?
            ORDER BY datetime(activity_logs.created_at) DESC, activity_logs.id DESC
            """,
            (equipment_id,),
        ).fetchall()

    activity_items = []
    for row in rows:
        old_value = parse_json_value(row["old_value"])
        new_value = parse_json_value(row["new_value"])
        changes = []
        title = {
            "CREATE": "Data alat dibuat",
            "UPDATE": "Data alat diperbarui",
            "DELETE": "Data alat dihapus",
        }.get(row["action"], row["action"].title())

        if row["action"] == "UPDATE" and isinstance(old_value, dict) and isinstance(new_value, dict):
            for field_name, old_field_value in old_value.items():
                new_field_value = new_value.get(field_name)
                if field_name == "lab_id":
                    old_field_value = lab_lookup.get(old_field_value, old_field_value)
                    new_field_value = lab_lookup.get(new_field_value, new_field_value)
                changes.append(
                    {
                        "label": equipment_field_label(field_name),
                        "old": old_field_value if old_field_value not in (None, "") else "-",
                        "new": new_field_value if new_field_value not in (None, "") else "-",
                    }
                )
            if len(changes) == 1:
                title = f"{changes[0]['label']} changed"
        elif row["action"] == "CREATE":
            changes.append({"label": "No Alat", "old": "-", "new": new_value or "-"})
        elif row["action"] == "DELETE":
            deleted_label = old_value.get("equipment_no") if isinstance(old_value, dict) else old_value
            changes.append({"label": "Data", "old": deleted_label or "-", "new": "-"})

        activity_items.append(
            {
                "title": title,
                "action": row["action"],
                "created_at": row["created_at"],
                "user_name": row["user_name"] or "System",
                "user_role": row["user_role"] or "",
                "changes": changes,
            }
        )

    return activity_items


def build_dashboard_data():
    refresh_equipment_statuses()
    today = jakarta_today()
    soon_limit = today + timedelta(days=30)

    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) AS total FROM equipment").fetchone()["total"]
        equipment_rows = conn.execute("SELECT expiry_date FROM equipment").fetchall()
        recent_equipment = conn.execute(
            """
            SELECT
                equipment.equipment_no,
                equipment.equipment_name,
                equipment.created_at,
                labs.lab_name
            FROM equipment
            LEFT JOIN labs ON labs.id = equipment.lab_id
            ORDER BY datetime(equipment.created_at) DESC, equipment.id DESC
            LIMIT 5
            """
        ).fetchall()

    active = 0
    expiring = 0
    expired = 0
    for row in equipment_rows:
        expiry_date = parse_date(row["expiry_date"])
        if expiry_date is None:
            continue
        if expiry_date < today:
            expired += 1
        elif today <= expiry_date <= soon_limit:
            expiring += 1
        else:
            active += 1

    status_total = active + expiring + expired
    if status_total:
        active_degrees = round((active / status_total) * 360, 2)
        expiring_degrees = round((expiring / status_total) * 360, 2)
    else:
        active_degrees = 0
        expiring_degrees = 0

    return {
        "stats": {
            "total": total,
            "active": active,
            "expiring": expiring,
            "expired": expired,
        },
        "recent_equipment": recent_equipment,
        "chart": {
            "status_total": status_total,
            "active_degrees": active_degrees,
            "expiring_degrees": expiring_degrees,
        },
    }


@app.get("/")
def index(user=Depends(get_current_user)):
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/login")
def login_page(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None},
    )


@app.post("/login")
def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    clean_username = username.strip()
    with get_db() as conn:
        user = conn.execute(
            """
            SELECT id, name, username, password_hash, role, status
            FROM users
            WHERE username = ?
            """,
            (clean_username,),
        ).fetchone()

        if (
            user is None
            or user["status"] != "active"
            or not verify_password(password, user["password_hash"])
        ):
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Username atau password tidak sesuai.",
                    "username": clean_username,
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        session_token = create_session_token()
        conn.execute(
            """
            INSERT INTO sessions (user_id, token_hash, expires_at)
            VALUES (?, ?, ?)
            """,
            (
                user["id"],
                hash_session_token(session_token),
                to_db_datetime(session_expiry()),
            ),
        )

    redirect = RedirectResponse(
        url="/dashboard",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    redirect.set_cookie(
        key=SESSION_COOKIE,
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24,
    )
    return redirect


@app.get("/dashboard")
def dashboard(request: Request, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user

    dashboard_data = build_dashboard_data()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "active_page": "dashboard",
            "page_title": "Dashboard",
            **dashboard_data,
        },
    )


@app.get("/equipment/new")
def input_equipment_page(
    request: Request,
    saved: int | None = None,
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user

    success_equipment = None
    if saved:
        with get_db() as conn:
            success_equipment = conn.execute(
                """
                SELECT id, equipment_no, equipment_name, barcode
                FROM equipment
                WHERE id = ?
                """,
                (saved,),
            ).fetchone()

    return render_equipment_form(request, user, success_equipment=success_equipment)


@app.post("/equipment/new")
def save_equipment(
    request: Request,
    equipment_no: Annotated[str, Form()],
    equipment_name: Annotated[str, Form()],
    certificate_no: Annotated[str, Form()],
    specification: Annotated[str, Form()],
    lab_id: Annotated[str, Form()],
    calibration_date: Annotated[str, Form()] = "",
    expiry_date: Annotated[str, Form()] = "",
    barcode: Annotated[str, Form()] = "",
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user

    form_data = normalize_equipment_form(
        equipment_no,
        equipment_name,
        certificate_no,
        specification,
        lab_id,
        calibration_date,
        expiry_date,
        barcode,
    )
    errors = validate_equipment_form(form_data)

    with get_db() as conn:
        if form_data["equipment_no"]:
            duplicate_equipment = conn.execute(
                "SELECT id FROM equipment WHERE equipment_no = ?",
                (form_data["equipment_no"],),
            ).fetchone()
            if duplicate_equipment:
                errors["equipment_no"] = "No Alat sudah terdaftar."

        if form_data["barcode"]:
            duplicate_barcode = conn.execute(
                "SELECT id FROM equipment WHERE barcode = ?",
                (form_data["barcode"],),
            ).fetchone()
            if duplicate_barcode:
                errors["barcode"] = "Barcode sudah digunakan."

        if form_data["lab_id"] and "lab_id" not in errors:
            lab = conn.execute(
                "SELECT id FROM labs WHERE id = ? AND status = ?",
                (int(form_data["lab_id"]), "active"),
            ).fetchone()
            if lab is None:
                errors["lab_id"] = "Lab tidak ditemukan."

        if errors:
            return render_equipment_form(
                request,
                user,
                form_data=form_data,
                errors=errors,
            )

        equipment_status = calculate_equipment_status(form_data["expiry_date"])
        cursor = conn.execute(
            """
            INSERT INTO equipment (
                equipment_no,
                equipment_name,
                certificate_no,
                specification,
                lab_id,
                calibration_date,
                expiry_date,
                barcode,
                status,
                created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                form_data["equipment_no"],
                form_data["equipment_name"],
                form_data["certificate_no"],
                form_data["specification"],
                int(form_data["lab_id"]),
                form_data["calibration_date"] or None,
                form_data["expiry_date"] or None,
                form_data["barcode"],
                equipment_status,
                user["id"],
            ),
        )
        equipment_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO activity_logs (user_id, equipment_id, action, old_value, new_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                equipment_id,
                "CREATE",
                None,
                form_data["equipment_no"],
            ),
        )

    return RedirectResponse(
        url=f"/equipment/new?saved={equipment_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/equipment/{equipment_id}/edit")
def edit_equipment_page(request: Request, equipment_id: int, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    equipment = get_equipment_or_none(equipment_id)
    if equipment is None:
        return Response("Data alat tidak ditemukan.", status_code=status.HTTP_404_NOT_FOUND)
    return render_equipment_form(
        request,
        user,
        form_data=form_from_equipment(equipment),
        mode="edit",
        equipment=equipment,
    )


@app.post("/equipment/{equipment_id}/edit")
def update_equipment(
    request: Request,
    equipment_id: int,
    equipment_no: Annotated[str, Form()],
    equipment_name: Annotated[str, Form()],
    certificate_no: Annotated[str, Form()],
    specification: Annotated[str, Form()],
    lab_id: Annotated[str, Form()],
    calibration_date: Annotated[str, Form()] = "",
    expiry_date: Annotated[str, Form()] = "",
    barcode: Annotated[str, Form()] = "",
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user

    old_equipment = get_equipment_or_none(equipment_id)
    if old_equipment is None:
        return Response("Data alat tidak ditemukan.", status_code=status.HTTP_404_NOT_FOUND)

    form_data = normalize_equipment_form(
        equipment_no,
        equipment_name,
        certificate_no,
        specification,
        lab_id,
        calibration_date,
        expiry_date,
        barcode,
    )
    errors = validate_equipment_form(form_data)

    with get_db() as conn:
        if form_data["equipment_no"]:
            duplicate_equipment = conn.execute(
                "SELECT id FROM equipment WHERE equipment_no = ? AND id != ?",
                (form_data["equipment_no"], equipment_id),
            ).fetchone()
            if duplicate_equipment:
                errors["equipment_no"] = "No Alat sudah terdaftar."

        if form_data["barcode"]:
            duplicate_barcode = conn.execute(
                "SELECT id FROM equipment WHERE barcode = ? AND id != ?",
                (form_data["barcode"], equipment_id),
            ).fetchone()
            if duplicate_barcode:
                errors["barcode"] = "Barcode sudah digunakan."

        if form_data["lab_id"] and "lab_id" not in errors:
            lab = conn.execute(
                "SELECT id FROM labs WHERE id = ? AND status = ?",
                (int(form_data["lab_id"]), "active"),
            ).fetchone()
            if lab is None:
                errors["lab_id"] = "Lab tidak ditemukan."

        if errors:
            return render_equipment_form(
                request,
                user,
                form_data=form_data,
                errors=errors,
                mode="edit",
                equipment=old_equipment,
            )

        changed_fields = {}
        comparable_fields = (
            "equipment_no",
            "equipment_name",
            "certificate_no",
            "specification",
            "lab_id",
            "calibration_date",
            "expiry_date",
            "barcode",
        )
        for field in comparable_fields:
            old_value = old_equipment[field]
            new_value = int(form_data[field]) if field == "lab_id" else (form_data[field] or None)
            if str(old_value or "") != str(new_value or ""):
                changed_fields[field] = {"old": old_value, "new": new_value}

        equipment_status = calculate_equipment_status(form_data["expiry_date"])
        if str(old_equipment["status"] or "") != equipment_status:
            changed_fields["status"] = {"old": old_equipment["status"], "new": equipment_status}

        conn.execute(
            """
            UPDATE equipment
            SET
                equipment_no = ?,
                equipment_name = ?,
                certificate_no = ?,
                specification = ?,
                lab_id = ?,
                calibration_date = ?,
                expiry_date = ?,
                barcode = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                form_data["equipment_no"],
                form_data["equipment_name"],
                form_data["certificate_no"],
                form_data["specification"],
                int(form_data["lab_id"]),
                form_data["calibration_date"] or None,
                form_data["expiry_date"] or None,
                form_data["barcode"],
                equipment_status,
                equipment_id,
            ),
        )

        if changed_fields:
            conn.execute(
                """
                INSERT INTO activity_logs (user_id, equipment_id, action, old_value, new_value)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    equipment_id,
                    "UPDATE",
                    json.dumps({field: values["old"] for field, values in changed_fields.items()}, default=str),
                    json.dumps({field: values["new"] for field, values in changed_fields.items()}, default=str),
                ),
            )

    return RedirectResponse(
        url=f"/equipment/{equipment_id}/detail",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/equipment/barcode/generate")
def generate_barcode(user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    return {"barcode": generate_unique_barcode()}


@app.get("/equipment/barcode/{barcode}.png")
def barcode_png(barcode: str, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    if not BARCODE_PATTERN.fullmatch(barcode):
        return Response("Barcode tidak valid.", status_code=status.HTTP_400_BAD_REQUEST)
    return Response(content=generate_barcode_png(barcode), media_type="image/png")


@app.get("/equipment/{equipment_id}/barcode/download")
def download_barcode(equipment_id: int, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    with get_db() as conn:
        equipment = conn.execute(
            "SELECT equipment_no, barcode FROM equipment WHERE id = ?",
            (equipment_id,),
        ).fetchone()
    if equipment is None:
        return Response("Data alat tidak ditemukan.", status_code=status.HTTP_404_NOT_FOUND)

    filename = f"{equipment['equipment_no']}-barcode.png"
    return Response(
        content=generate_barcode_png(equipment["barcode"]),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/equipment/{equipment_id}/barcode/print")
def print_barcode(request: Request, equipment_id: int, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    with get_db() as conn:
        equipment = conn.execute(
            """
            SELECT equipment.id, equipment.equipment_no, equipment.equipment_name, equipment.barcode, labs.lab_name
            FROM equipment
            LEFT JOIN labs ON labs.id = equipment.lab_id
            WHERE equipment.id = ?
            """,
            (equipment_id,),
        ).fetchone()
    if equipment is None:
        return Response("Data alat tidak ditemukan.", status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        "barcode_print.html",
        {"request": request, "equipment": equipment},
    )


@app.get("/equipment/{equipment_id}/detail")
def equipment_detail(request: Request, equipment_id: int, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    equipment = get_equipment_or_none(equipment_id)
    if equipment is None:
        return Response("Data alat tidak ditemukan.", status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        "equipment_detail.html",
        {
            "request": request,
            "user": user,
            "active_page": "list",
            "page_title": "Detail Alat",
            "equipment": equipment,
            "activity_items": build_activity_items(equipment_id),
        },
    )


@app.get("/api/equipment/barcode/{barcode}")
def lookup_equipment_by_barcode(barcode: str, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return JSONResponse(
            {"message": "Silakan login untuk menggunakan fitur scan barcode."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    clean_barcode = barcode.strip()
    if not clean_barcode or not BARCODE_PATTERN.fullmatch(clean_barcode):
        return JSONResponse(
            {"message": "Barcode tidak valid."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    with get_db() as conn:
        equipment = conn.execute(
            """
            SELECT id, equipment_no, equipment_name, barcode
            FROM equipment
            WHERE barcode = ?
            """,
            (clean_barcode,),
        ).fetchone()

    if equipment is None:
        return JSONResponse(
            {"message": "Barcode tidak ditemukan."},
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return {
        "id": equipment["id"],
        "equipment_no": equipment["equipment_no"],
        "equipment_name": equipment["equipment_name"],
        "barcode": equipment["barcode"],
        "detail_url": f"/equipment/{equipment['id']}/detail",
    }


@app.get("/equipment")
def list_equipment(
    request: Request,
    q: str = "",
    lab_id: str = "",
    status: str = "",
    calibration_date: str = "",
    expiry_date: str = "",
    page: int = 1,
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user
    filters = {
        "q": q.strip(),
        "lab_id": lab_id.strip(),
        "status_filter": status.strip(),
        "calibration_date": calibration_date.strip(),
        "expiry_date": expiry_date.strip(),
        "page": max(page, 1),
    }
    list_data = build_equipment_list(filters)
    return templates.TemplateResponse(
        "equipment_list.html",
        {
            "request": request,
            "user": user,
            "active_page": "list",
            "page_title": "List Alat",
            "labs": get_labs(),
            "filters": filters,
            "is_admin": is_admin(user),
            "page_url": lambda next_page: list_query_url(filters, page=next_page),
            "clear_url": "/equipment",
            **list_data,
        },
    )


@app.post("/equipment/{equipment_id}/delete")
def delete_equipment(equipment_id: int, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    if not is_admin(user):
        return Response("Hanya admin yang dapat menghapus data alat.", status_code=status.HTTP_403_FORBIDDEN)

    with get_db() as conn:
        equipment = conn.execute(
            "SELECT id, equipment_no, equipment_name FROM equipment WHERE id = ?",
            (equipment_id,),
        ).fetchone()
        if equipment is None:
            return Response("Data alat tidak ditemukan.", status_code=status.HTTP_404_NOT_FOUND)

        conn.execute(
            """
            INSERT INTO activity_logs (user_id, equipment_id, action, old_value, new_value)
            VALUES (?, NULL, ?, ?, ?)
            """,
            (
                user["id"],
                "DELETE",
                json.dumps(
                    {
                        "equipment_no": equipment["equipment_no"],
                        "equipment_name": equipment["equipment_name"],
                    }
                ),
                None,
            ),
        )
        conn.execute("DELETE FROM equipment WHERE id = ?", (equipment_id,))

    return RedirectResponse(url="/equipment", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/scan")
def scan_redirect(user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    return RedirectResponse(url="/scan-barcode", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/scan-barcode")
def scan_barcode_page(request: Request, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        "scan_barcode.html",
        {
            "request": request,
            "user": user,
            "active_page": "scan",
            "page_title": "Scan Barcode",
        },
    )


@app.get("/settings")
def settings_page(
    request: Request,
    tab: str = "users",
    message: str | None = None,
    error: str | None = None,
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized
    active_tab = "labs" if tab == "labs" else "users"
    return render_settings(request, user, active_tab=active_tab, message=message, error=error)


@app.post("/settings/users")
def create_user(
    request: Request,
    name: Annotated[str, Form()],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
    role: Annotated[str, Form()],
    user_status: Annotated[str, Form(alias="status")],
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized

    clean_name = name.strip()
    clean_username = username.strip()
    clean_role = role.strip().lower()
    clean_status = user_status.strip().lower()
    if not clean_name or not clean_username or not password:
        return render_settings(request, user, error="Nama, username, dan password wajib diisi.")
    if password != confirm_password:
        return render_settings(request, user, error="Konfirmasi password tidak sesuai.")
    if clean_role not in {"admin", "user"}:
        return render_settings(request, user, error="Role user tidak valid.")
    if clean_status not in {"active", "inactive"}:
        return render_settings(request, user, error="Status user tidak valid.")

    with get_db() as conn:
        duplicate = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (clean_username,),
        ).fetchone()
        if duplicate:
            return render_settings(request, user, error="Username sudah digunakan.")
        conn.execute(
            """
            INSERT INTO users (name, username, password_hash, role, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (clean_name, clean_username, hash_password(password), clean_role, clean_status),
        )
        add_admin_activity(
            conn,
            user["id"],
            "USER_CREATE",
            None,
            {"name": clean_name, "username": clean_username, "role": clean_role, "status": clean_status},
        )
    return RedirectResponse(
        url="/settings?tab=users&message=User berhasil ditambahkan.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/users/{target_user_id}/edit")
def update_user(
    request: Request,
    target_user_id: int,
    name: Annotated[str, Form()],
    username: Annotated[str, Form()],
    role: Annotated[str, Form()],
    user_status: Annotated[str, Form(alias="status")],
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized

    clean_name = name.strip()
    clean_username = username.strip()
    clean_role = role.strip().lower()
    clean_status = user_status.strip().lower()
    if not clean_name or not clean_username:
        return render_settings(request, user, error="Nama dan username wajib diisi.")
    if clean_role not in {"admin", "user"} or clean_status not in {"active", "inactive"}:
        return render_settings(request, user, error="Role atau status user tidak valid.")

    with get_db() as conn:
        target_user = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if target_user is None:
            return render_settings(request, user, error="User tidak ditemukan.")
        duplicate = conn.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?",
            (clean_username, target_user_id),
        ).fetchone()
        if duplicate:
            return render_settings(request, user, error="Username sudah digunakan.")
        updated_user = {
            "name": clean_name,
            "username": clean_username,
            "role": clean_role,
            "status": clean_status,
        }
        if would_remove_last_active_admin(conn, target_user) and (
            clean_role == "user" or clean_status != "active"
        ):
            return render_settings(
                request,
                user,
                error="Tidak boleh mengubah admin aktif terakhir menjadi non-admin atau nonaktif.",
            )
        old_user = {
            "name": target_user["name"],
            "username": target_user["username"],
            "role": target_user["role"],
            "status": target_user["status"],
        }
        conn.execute(
            """
            UPDATE users
            SET name = ?, username = ?, role = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (clean_name, clean_username, clean_role, clean_status, target_user_id),
        )
        add_admin_activity(conn, user["id"], "USER_UPDATE", old_user, updated_user)

    return RedirectResponse(
        url="/settings?tab=users&message=User berhasil diperbarui.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/users/{target_user_id}/password")
def reset_user_password(
    request: Request,
    target_user_id: int,
    password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized
    if not password:
        return render_settings(request, user, error="Password baru wajib diisi.")
    if password != confirm_password:
        return render_settings(request, user, error="Konfirmasi password tidak sesuai.")

    with get_db() as conn:
        target_user = conn.execute("SELECT id, username FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if target_user is None:
            return render_settings(request, user, error="User tidak ditemukan.")
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (hash_password(password), target_user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_user_id,))
        add_admin_activity(conn, user["id"], "USER_PASSWORD_RESET", None, {"username": target_user["username"]})

    return RedirectResponse(
        url="/settings?tab=users&message=Password user berhasil diubah.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/users/{target_user_id}/toggle")
def toggle_user_status(request: Request, target_user_id: int, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized
    with get_db() as conn:
        target_user = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if target_user is None:
            return render_settings(request, user, error="User tidak ditemukan.")
        if would_remove_last_active_admin(conn, target_user):
            return render_settings(request, user, error="Tidak boleh menonaktifkan admin aktif terakhir.")
        next_status = "inactive" if target_user["status"] == "active" else "active"
        conn.execute(
            "UPDATE users SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (next_status, target_user_id),
        )
        if next_status == "inactive":
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_user_id,))
        add_admin_activity(
            conn,
            user["id"],
            "USER_STATUS_CHANGE",
            {"username": target_user["username"], "status": target_user["status"]},
            {"username": target_user["username"], "status": next_status},
        )
    return RedirectResponse(
        url="/settings?tab=users&message=Status user berhasil diperbarui.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/users/{target_user_id}/delete")
def delete_user(request: Request, target_user_id: int, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized
    with get_db() as conn:
        target_user = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if target_user is None:
            return render_settings(request, user, error="User tidak ditemukan.")
        if target_user_id == user["id"]:
            return render_settings(request, user, error="Tidak boleh menghapus akun yang sedang digunakan.")
        if would_remove_last_active_admin(conn, target_user):
            return render_settings(request, user, error="Tidak boleh menghapus admin aktif terakhir.")
        references = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM equipment WHERE created_by = ?) AS equipment_count,
                (SELECT COUNT(*) FROM activity_logs WHERE user_id = ?) AS log_count
            """,
            (target_user_id, target_user_id),
        ).fetchone()
        if references["equipment_count"] or references["log_count"]:
            return render_settings(
                request,
                user,
                error="User sudah memiliki riwayat data. Nonaktifkan user sebagai pilihan aman.",
            )
        add_admin_activity(conn, user["id"], "USER_DELETE", {"username": target_user["username"]}, None)
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (target_user_id,))
    return RedirectResponse(
        url="/settings?tab=users&message=User berhasil dihapus.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/labs")
def create_lab(
    request: Request,
    lab_name: Annotated[str, Form()],
    lab_status: Annotated[str, Form(alias="status")],
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized
    clean_name = lab_name.strip()
    clean_status = lab_status.strip().lower()
    if not clean_name:
        return render_settings(request, user, active_tab="labs", error="Nama lab wajib diisi.")
    if clean_status not in {"active", "inactive"}:
        return render_settings(request, user, active_tab="labs", error="Status lab tidak valid.")
    with get_db() as conn:
        duplicate = conn.execute("SELECT id FROM labs WHERE lower(lab_name) = lower(?)", (clean_name,)).fetchone()
        if duplicate:
            return render_settings(request, user, active_tab="labs", error="Nama lab sudah digunakan.")
        conn.execute("INSERT INTO labs (lab_name, status) VALUES (?, ?)", (clean_name, clean_status))
        add_admin_activity(conn, user["id"], "LAB_CREATE", None, {"lab_name": clean_name, "status": clean_status})
    return RedirectResponse(
        url="/settings?tab=labs&message=Lab berhasil ditambahkan.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/labs/{lab_id}/edit")
def update_lab(
    request: Request,
    lab_id: int,
    lab_name: Annotated[str, Form()],
    lab_status: Annotated[str, Form(alias="status")],
    user=Depends(require_user),
):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized
    clean_name = lab_name.strip()
    clean_status = lab_status.strip().lower()
    if not clean_name:
        return render_settings(request, user, active_tab="labs", error="Nama lab wajib diisi.")
    if clean_status not in {"active", "inactive"}:
        return render_settings(request, user, active_tab="labs", error="Status lab tidak valid.")
    with get_db() as conn:
        lab = conn.execute("SELECT * FROM labs WHERE id = ?", (lab_id,)).fetchone()
        if lab is None:
            return render_settings(request, user, active_tab="labs", error="Lab tidak ditemukan.")
        duplicate = conn.execute(
            "SELECT id FROM labs WHERE lower(lab_name) = lower(?) AND id != ?",
            (clean_name, lab_id),
        ).fetchone()
        if duplicate:
            return render_settings(request, user, active_tab="labs", error="Nama lab sudah digunakan.")
        conn.execute("UPDATE labs SET lab_name = ?, status = ? WHERE id = ?", (clean_name, clean_status, lab_id))
        add_admin_activity(
            conn,
            user["id"],
            "LAB_UPDATE",
            {"lab_name": lab["lab_name"], "status": lab["status"]},
            {"lab_name": clean_name, "status": clean_status},
        )
    return RedirectResponse(
        url="/settings?tab=labs&message=Lab berhasil diperbarui.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/labs/{lab_id}/toggle")
def toggle_lab_status(request: Request, lab_id: int, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized
    with get_db() as conn:
        lab = conn.execute("SELECT * FROM labs WHERE id = ?", (lab_id,)).fetchone()
        if lab is None:
            return render_settings(request, user, active_tab="labs", error="Lab tidak ditemukan.")
        next_status = "inactive" if lab["status"] == "active" else "active"
        conn.execute("UPDATE labs SET status = ? WHERE id = ?", (next_status, lab_id))
        add_admin_activity(
            conn,
            user["id"],
            "LAB_STATUS_CHANGE",
            {"lab_name": lab["lab_name"], "status": lab["status"]},
            {"lab_name": lab["lab_name"], "status": next_status},
        )
    return RedirectResponse(
        url="/settings?tab=labs&message=Status lab berhasil diperbarui.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/settings/labs/{lab_id}/delete")
def delete_lab(request: Request, lab_id: int, user=Depends(require_user)):
    if isinstance(user, RedirectResponse):
        return user
    unauthorized = require_admin_user(request, user)
    if unauthorized:
        return unauthorized
    with get_db() as conn:
        lab = conn.execute("SELECT * FROM labs WHERE id = ?", (lab_id,)).fetchone()
        if lab is None:
            return render_settings(request, user, active_tab="labs", error="Lab tidak ditemukan.")
        equipment_count = conn.execute(
            "SELECT COUNT(*) AS total FROM equipment WHERE lab_id = ?",
            (lab_id,),
        ).fetchone()["total"]
        if equipment_count:
            return render_settings(
                request,
                user,
                active_tab="labs",
                error="Lab masih digunakan oleh data alat. Nonaktifkan lab sebagai pilihan aman.",
            )
        add_admin_activity(conn, user["id"], "LAB_DELETE", {"lab_name": lab["lab_name"]}, None)
        conn.execute("DELETE FROM labs WHERE id = ?", (lab_id,))
    return RedirectResponse(
        url="/settings?tab=labs&message=Lab berhasil dihapus.",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/logout")
def logout(
    heksa_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
):
    if heksa_session:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (hash_session_token(heksa_session),),
            )

    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(key=SESSION_COOKIE)
    return redirect
