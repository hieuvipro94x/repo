# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from release_policy import PUBLIC_ARTIFACT_SUFFIXES, resolve_public_release_file
from werkzeug.utils import secure_filename

try:
    import psutil
except ImportError:
    psutil = None


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = Path(
    os.environ.get("PIWEB_UPLOAD_DIR", str(Path.home() / "personal_storage" / "uploads"))
)
RELEASES_DIR = Path(os.environ.get("PIWEB_RELEASES_DIR", str(BASE_DIR / "releases")))
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
NEXTCLOUD_PORT = 8080
NEXTCLOUD_URL = f"http://localhost:{NEXTCLOUD_PORT}"
PUBLIC_RELEASE_BASE_URL = os.environ.get(
    "PIWEB_PUBLIC_RELEASE_BASE_URL", "http://100.121.199.45:8000/releases"
).rstrip("/")
RELEASE_APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
RELEASE_STATUS_FILENAME = ".release_status.json"
RELEASE_STATUS_PENDING_UPDATE = "PendingUpdate"
RELEASE_STATUS_RELEASED = "Released"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.config["SECRET_KEY"] = os.environ.get("PIWEB_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("PIWEB_SECURE_COOKIES", "0") == "1"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RELEASES_DIR.mkdir(parents=True, exist_ok=True)


def csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def require_csrf_token():
    submitted = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not submitted or not expected or not hmac.compare_digest(submitted, expected):
        abort(400, description="CSRF token không hợp lệ.")


app.jinja_env.globals["csrf_token"] = csrf_token


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def bytes_to_human(value):
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{value} B"


def percent(part, total):
    if not total:
        return 0
    return round((part / total) * 100, 1)


def get_lan_ip():
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        if sock is not None:
            sock.close()


def get_tailscale_ip():
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    ip = result.stdout.strip().splitlines()
    return ip[0] if ip else None


def get_uptime():
    if psutil is not None:
        seconds = int(time.time() - psutil.boot_time())
    else:
        try:
            seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
        except (OSError, ValueError, IndexError):
            return "Không xác định"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days} Ngày")
    if hours:
        parts.append(f"{hours} Giờ")
    parts.append(f"{minutes} Phút")

    return " ".join(parts)


def get_system_info():
    disk = psutil.disk_usage("/") if psutil else None
    memory = psutil.virtual_memory() if psutil else None

    return {
        "cpu_usage": psutil.cpu_percent(interval=0.1) if psutil else None,
        "ram_usage": memory.percent if memory else None,
        "ram_used": bytes_to_human(memory.used) if memory else None,
        "ram_total": bytes_to_human(memory.total) if memory else None,
        "disk_usage": disk.percent if disk else None,
        "disk_used": bytes_to_human(disk.used) if disk else None,
        "disk_total": bytes_to_human(disk.total) if disk else None,
        "uptime": get_uptime(),
        "psutil_available": psutil is not None,
    }


def get_storage_info(
    q="", sort="uploaded", order="desc", file_type="all", date_from="", date_to=""
):
    q, sort, order, file_type, date_from, date_to = normalize_storage_filters(
        q, sort, order, file_type, date_from, date_to
    )
    usage = psutil.disk_usage(str(UPLOAD_DIR)) if psutil else None
    files = list_storage_files(
        q=q,
        sort=sort,
        order=order,
        file_type=file_type,
        date_from=date_from,
        date_to=date_to,
    )
    total_size = sum(item["size_bytes"] for item in files)

    return {
        "upload_dir": str(UPLOAD_DIR),
        "file_count": len(files),
        "total_upload_size": bytes_to_human(total_size),
        "files": files,
        "disk_usage": usage.percent if usage else None,
        "disk_free": bytes_to_human(usage.free) if usage else None,
        "filters": {
            "q": q,
            "sort": sort,
            "order": order,
            "type": file_type,
            "date_from": date_from,
            "date_to": date_to,
        },
    }

def get_status_info():
    return {
        "status": "ONLINE",
        "hostname": socket.gethostname(),
        "ip_lan": get_lan_ip(),
        "ip_tailscale": get_tailscale_ip(),
        "time": datetime.now().strftime("%H:%M:%S %d/%m/%Y"),
        "current_time": datetime.now().strftime("%H:%M:%S %d/%m/%Y"),
        "nextcloud_port": NEXTCLOUD_PORT,
        "nextcloud_url": NEXTCLOUD_URL,
    }


def build_dashboard_context():
    status = get_status_info()
    system = get_system_info()
    storage = get_storage_info()
    releases = list_app_releases()
    return {**status, **system, "storage": storage, "release_count": len(releases)}


def release_updated_at(*paths):
    timestamps = []
    for path in paths:
        if path is None:
            continue
        try:
            timestamps.append(path.stat().st_mtime)
        except OSError:
            continue
    if not timestamps:
        return None
    return datetime.fromtimestamp(max(timestamps)).astimezone().isoformat(timespec="seconds")


def current_timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def release_status_path(app_dir):
    return app_dir / RELEASE_STATUS_FILENAME


def load_release_status(app_dir, version):
    try:
        status_data = json.loads(release_status_path(app_dir).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(status_data, dict) or status_data.get("version") != version:
        return None
    if status_data.get("status") not in {
        RELEASE_STATUS_PENDING_UPDATE,
        RELEASE_STATUS_RELEASED,
    }:
        return None
    return status_data


def save_release_status(app_dir, status_data):
    token = secrets.token_hex(8)
    temp_path = app_dir / f".release_status.{token}.upload"
    try:
        temp_path.write_text(
            json.dumps(status_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(release_status_path(app_dir))
    finally:
        temp_path.unlink(missing_ok=True)


def empty_release(app_id):
    return {
        "app_id": app_id,
        "app_name": app_id,
        "latest_version": "N/A",
        "app_type": "wpf",
        "install_mode": "N/A",
        "required": False,
        "release_notes": "",
        "artifact_name": "N/A",
        "artifact_size": "N/A",
        "artifact_size_bytes": None,
        "sha256": "",
        "sha256_short": "",
        "manifest_url": f"{PUBLIC_RELEASE_BASE_URL}/{app_id}/version.json",
        "download_url": "",
        "updated_at": None,
        "state": "pending",
        "state_label": "Chưa phát hành",
        "error": None,
        "has_public_manifest": False,
        "release_status": None,
        "published_at": None,
        "confirmed_at": None,
        "confirmed_device_id": None,
    }


def valid_sha256(value):
    return (
        len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
        and set(value) != {"0"}
    )


def resolve_pending_artifact(app_dir, filename):
    if (
        not filename
        or secure_filename(filename) != filename
        or Path(filename).suffix.lower() not in PUBLIC_ARTIFACT_SUFFIXES
    ):
        return None
    pending_dir = app_dir / "pending"
    candidate = pending_dir / filename
    if pending_dir.is_symlink() or candidate.is_symlink():
        return None
    target = candidate.resolve()
    if target.parent != pending_dir.resolve() or not target.is_file():
        return None
    return target


def read_release_manifest(app_dir, manifest_path, staged=False):
    release = empty_release(app_dir.name)
    if not manifest_path.is_file():
        return release
    if not staged:
        release["has_public_manifest"] = True
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError("Nội dung JSON phải là một object.")
    except OSError:
        release.update(
            state="invalid",
            state_label="Manifest lỗi",
            updated_at=release_updated_at(manifest_path),
            error="Không đọc được version.json.",
        )
        return release
    except json.JSONDecodeError as exc:
        release.update(
            state="invalid",
            state_label="Manifest lỗi",
            updated_at=release_updated_at(manifest_path),
            error=f"JSON lỗi tại dòng {exc.lineno}, cột {exc.colno}.",
        )
        return release
    except ValueError as exc:
        release.update(
            state="invalid",
            state_label="Manifest lỗi",
            updated_at=release_updated_at(manifest_path),
            error=str(exc),
        )
        return release

    download_url = str(manifest.get("download_url", ""))
    artifact_name = Path(urlparse(download_url).path).name if download_url else ""
    artifact_path = (
        resolve_pending_artifact(app_dir, artifact_name)
        if staged
        else resolve_public_release_file(RELEASES_DIR, f"{app_dir.name}/{artifact_name}")
    )
    sha256 = str(manifest.get("sha256", ""))
    digest_ok = valid_sha256(sha256)
    is_complete = artifact_path is not None and digest_ok
    artifact_size_bytes = artifact_path.stat().st_size if artifact_path else None
    status_data = None if staged or not is_complete else load_release_status(
        app_dir, manifest.get("latest_version") or "N/A"
    )
    if staged and is_complete:
        state = "waiting"
        state_label = "Chờ phát hành"
        release_status = None
    elif is_complete:
        release_status = status_data["status"] if status_data else None
        if release_status == RELEASE_STATUS_PENDING_UPDATE:
            state = "pending_update"
            state_label = "Đang chờ người dùng cập nhật..."
        elif release_status == RELEASE_STATUS_RELEASED:
            state = "released"
            state_label = "Đã phát hành"
        else:
            state = "public"
            state_label = "Bản public hiện có"
    else:
        state = "pending"
        state_label = "Chưa phát hành"
        release_status = None
    release.update(
        app_name=manifest.get("app_name") or app_dir.name,
        latest_version=manifest.get("latest_version") or "N/A",
        app_type=manifest.get("app_type") or "wpf",
        install_mode=manifest.get("install_mode") or "N/A",
        required=bool(manifest.get("required")),
        release_notes=manifest.get("release_notes") or "",
        artifact_name=artifact_name or "N/A",
        artifact_size=bytes_to_human(artifact_size_bytes) if artifact_size_bytes is not None else "N/A",
        artifact_size_bytes=artifact_size_bytes,
        sha256=sha256,
        sha256_short=f"{sha256[:12]}..." if digest_ok else "",
        download_url=download_url,
        updated_at=release_updated_at(manifest_path, artifact_path),
        state=state,
        state_label=state_label,
        is_complete=is_complete,
        release_status=release_status,
        published_at=status_data.get("published_at") if status_data else None,
        confirmed_at=status_data.get("confirmed_at") if status_data else None,
        confirmed_device_id=status_data.get("confirmed_device_id") if status_data else None,
    )
    return release


def list_app_releases():
    releases = []
    for app_dir in sorted(RELEASES_DIR.iterdir(), key=lambda path: path.name.lower()):
        if not app_dir.is_dir() or app_dir.is_symlink():
            continue
        manifest_path = app_dir / "version.json"
        pending_manifest_path = app_dir / "pending" / "version.json"
        if not manifest_path.is_file() and not pending_manifest_path.is_file():
            continue

        release = read_release_manifest(app_dir, manifest_path)
        if pending_manifest_path.is_file():
            pending = read_release_manifest(app_dir, pending_manifest_path, staged=True)
            release["has_pending"] = True
            release["pending"] = pending
            release["pending_version"] = pending["latest_version"]
            release["pending_artifact"] = pending["artifact_name"]
            release["pending_sha256_short"] = pending["sha256_short"]
            release["pending_release_notes"] = pending["release_notes"]
            release["pending_updated_at"] = pending["updated_at"]
            if not manifest_path.is_file():
                release["app_name"] = pending["app_name"]
                release["app_type"] = pending["app_type"]
        else:
            release.update(
                has_pending=False,
                pending=None,
                pending_version=None,
                pending_artifact=None,
                pending_sha256_short=None,
                pending_release_notes=None,
                pending_updated_at=None,
            )
        releases.append(release)
    return releases


def normalize_release_app_id(value):
    """Return a safe app id used as the folder name under RELEASES_DIR."""
    raw = (value or "").strip().replace("\\", "/").strip("/")
    if "/" in raw:
        raw = raw.split("/")[-1]
    safe = secure_filename(raw).lower()
    if not safe or ".." in safe or not RELEASE_APP_ID_PATTERN.fullmatch(safe):
        return None
    return safe


def infer_release_app_id(manifest, artifact_filename, requested_app_id=""):
    """Infer app_id from form, manifest URL/fields, or artifact name."""
    app_id = normalize_release_app_id(requested_app_id)
    if app_id:
        return app_id

    for key in ("app_id", "id", "slug"):
        app_id = normalize_release_app_id(manifest.get(key))
        if app_id:
            return app_id

    download_url = str(manifest.get("download_url", ""))
    path_parts = [part for part in urlparse(download_url).path.split("/") if part]
    if "releases" in path_parts:
        index = path_parts.index("releases")
        if len(path_parts) > index + 1:
            app_id = normalize_release_app_id(path_parts[index + 1])
            if app_id:
                return app_id

    stem = Path(artifact_filename).stem
    bits = stem.split("-")
    while bits and bits[-1].replace(".", "").isdigit():
        bits.pop()
    return normalize_release_app_id("-".join(bits) or stem)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_release_upload(manifest_file, artifact_file):
    if not manifest_file or not manifest_file.filename:
        return "Vui lòng chọn file version.json."
    if not artifact_file or not artifact_file.filename:
        return "Vui lòng chọn file ZIP/installer."
    if secure_filename(manifest_file.filename) != "version.json":
        return "File manifest phải có tên đúng là version.json."
    artifact_name = secure_filename(artifact_file.filename)
    if not artifact_name:
        return "Tên artifact không hợp lệ."
    if Path(artifact_name).suffix.lower() not in PUBLIC_ARTIFACT_SUFFIXES:
        return "Artifact phải là .zip, .exe hoặc .msi."
    return None


def save_release_upload(manifest_file, artifact_file, requested_app_id=""):
    """Stage a release bundle until it is explicitly published."""
    validation_error = validate_release_upload(manifest_file, artifact_file)
    if validation_error:
        raise ValueError(validation_error)

    try:
        manifest = json.loads(manifest_file.read().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("version.json không phải JSON hợp lệ.") from exc
    if not isinstance(manifest, dict):
        raise ValueError("version.json phải chứa một đối tượng JSON.")

    artifact_name = secure_filename(artifact_file.filename)
    app_id = infer_release_app_id(manifest, artifact_name, requested_app_id)
    if not app_id:
        raise ValueError("Không xác định được app_id. Hãy nhập app_id, ví dụ: sx3-scanner.")

    app_dir = (RELEASES_DIR / app_id).resolve()
    releases_root = RELEASES_DIR.resolve()
    if app_dir == releases_root or releases_root not in app_dir.parents:
        raise ValueError("app_id không hợp lệ.")

    app_dir.mkdir(parents=True, exist_ok=True)
    pending_dir = app_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    if pending_dir.is_symlink() or pending_dir.resolve().parent != app_dir:
        raise ValueError("Thư mục bản chờ không hợp lệ.")
    artifact_path = pending_dir / artifact_name
    manifest_path = pending_dir / "version.json"
    upload_token = secrets.token_hex(8)
    artifact_temp_path = pending_dir / f".{artifact_name}.{upload_token}.upload"
    manifest_temp_path = pending_dir / f".version.json.{upload_token}.upload"
    try:
        artifact_file.save(artifact_temp_path)
        if artifact_temp_path.stat().st_size == 0:
            raise ValueError("File ứng dụng rỗng, không thể phát hành.")

        digest = sha256_file(artifact_temp_path)
        manifest["sha256"] = digest
        manifest["download_url"] = f"{PUBLIC_RELEASE_BASE_URL}/{app_id}/{artifact_name}"
        manifest["app_id"] = app_id
        manifest_temp_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        artifact_temp_path.replace(artifact_path)
        manifest_temp_path.replace(manifest_path)
        for old_file in pending_dir.iterdir():
            if old_file.name not in {"version.json", artifact_name} and old_file.is_file():
                old_file.unlink()
    finally:
        artifact_temp_path.unlink(missing_ok=True)
        manifest_temp_path.unlink(missing_ok=True)

    return read_release_manifest(app_dir, manifest_path, staged=True)


def resolve_release_app_dir(app_id, allow_missing=False):
    if (
        not app_id
        or not RELEASE_APP_ID_PATTERN.fullmatch(app_id)
        or ".." in app_id
    ):
        return None
    releases_root = RELEASES_DIR.resolve()
    raw_app_dir = RELEASES_DIR / app_id
    if raw_app_dir.is_symlink():
        return None
    app_dir = raw_app_dir.resolve()
    if app_dir == releases_root or app_dir.parent != releases_root:
        return None
    if not allow_missing and not app_dir.is_dir():
        return None
    return app_dir


def get_release_app_dir(app_id, allow_missing=False):
    app_dir = resolve_release_app_dir(app_id, allow_missing=allow_missing)
    if app_dir is None:
        abort(404)
    return app_dir


def publish_pending_release(app_id):
    app_dir = get_release_app_dir(app_id, allow_missing=True)
    pending_dir = app_dir / "pending"
    pending_manifest = pending_dir / "version.json"
    if not pending_manifest.is_file():
        raise ValueError("Không có bản chờ.")

    pending = read_release_manifest(app_dir, pending_manifest, staged=True)
    if pending["state"] == "invalid" or not pending.get("is_complete"):
        raise ValueError("Bản chờ lỗi.")
    pending_artifact = resolve_pending_artifact(app_dir, pending["artifact_name"])
    if pending_artifact is None or sha256_file(pending_artifact).lower() != pending["sha256"].lower():
        raise ValueError("Bản chờ lỗi.")

    token = secrets.token_hex(8)
    artifact_target = app_dir / pending["artifact_name"]
    artifact_temp = app_dir / f".{pending['artifact_name']}.{token}.publish"
    manifest_temp = app_dir / f".version.json.{token}.publish"
    try:
        shutil.copy2(pending_artifact, artifact_temp)
        shutil.copy2(pending_manifest, manifest_temp)
        artifact_temp.replace(artifact_target)
        manifest_temp.replace(app_dir / "version.json")
        save_release_status(
            app_dir,
            {
                "status": RELEASE_STATUS_PENDING_UPDATE,
                "version": pending["latest_version"],
                "published_at": current_timestamp(),
                "confirmed_at": None,
                "confirmed_device_id": None,
            },
        )
    finally:
        artifact_temp.unlink(missing_ok=True)
        manifest_temp.unlink(missing_ok=True)
    shutil.rmtree(pending_dir)


def confirm_release_update(version, device_id, requested_app_id=None):
    releases = list_app_releases()
    candidates = [
        release
        for release in releases
        if release["latest_version"] == version
        and release.get("release_status") in {
            RELEASE_STATUS_PENDING_UPDATE,
            RELEASE_STATUS_RELEASED,
        }
    ]
    if requested_app_id:
        candidates = [
            release for release in candidates if release["app_id"] == requested_app_id
        ]
    pending_candidates = [
        release
        for release in candidates
        if release["release_status"] == RELEASE_STATUS_PENDING_UPDATE
    ]
    if not pending_candidates:
        if len(candidates) == 1 and candidates[0]["release_status"] == RELEASE_STATUS_RELEASED:
            return candidates[0], False
        raise ValueError("Không có bản phát hành đang chờ phiên bản này.")
    if len(pending_candidates) != 1:
        raise ValueError("Có nhiều app cùng phiên bản; cần gửi app_id.")

    release = pending_candidates[0]
    app_dir = get_release_app_dir(release["app_id"])
    status_data = load_release_status(app_dir, release["latest_version"])
    if not status_data or status_data["status"] != RELEASE_STATUS_PENDING_UPDATE:
        raise ValueError("Bản phát hành không còn chờ xác nhận.")
    status_data.update(
        status=RELEASE_STATUS_RELEASED,
        confirmed_at=current_timestamp(),
        confirmed_device_id=device_id,
    )
    save_release_status(app_dir, status_data)
    return read_release_manifest(app_dir, app_dir / "version.json"), True


def render_releases_page(message=None, error=None, uploaded=None, releases=None):
    return render_template(
        "releases.html",
        public_release_base_url=PUBLIC_RELEASE_BASE_URL,
        releases=releases if releases is not None else list_app_releases(),
        message=message,
        error=error,
        uploaded=uploaded,
    )


def classify_storage_file(path):
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}:
        return "image"
    if suffix in {".zip", ".rar", ".7z", ".tar", ".gz", ".exe", ".msi"}:
        return "archive"
    if suffix in {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".json", ".xml"}:
        return "document"
    return "other"


def normalize_storage_filters(
    q="", sort="uploaded", order="desc", file_type="all", date_from="", date_to=""
):
    q = (q or "").strip().lower()
    sort = (sort or "uploaded").strip().lower()
    order = (order or "desc").strip().lower()
    file_type = (file_type or "all").strip().lower()
    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()

    if sort not in {"name", "size", "uploaded", "modified", "type"}:
        sort = "uploaded"
    if order not in {"asc", "desc"}:
        order = "desc"
    if file_type not in {"all", "pdf", "image", "archive", "document", "other"}:
        file_type = "all"

    for value_name, value in (("date_from", date_from), ("date_to", date_to)):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            if value_name == "date_from":
                date_from = ""
            else:
                date_to = ""

    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    return q, sort, order, file_type, date_from, date_to


def list_storage_files(
    q="", sort="uploaded", order="desc", file_type="all", date_from="", date_to=""
):
    q, sort, order, file_type, date_from, date_to = normalize_storage_filters(
        q, sort, order, file_type, date_from, date_to
    )

    files = []
    for item in UPLOAD_DIR.rglob("*"):
        if item.is_symlink() or not item.is_file():
            continue

        stat = item.stat()
        relative_name = item.relative_to(UPLOAD_DIR).as_posix()
        storage_type = classify_storage_file(item)

        if q and q not in relative_name.lower():
            continue
        if file_type != "all" and storage_type != file_type:
            continue

        uploaded_timestamp = stat.st_mtime
        uploaded_date = datetime.fromtimestamp(uploaded_timestamp).strftime("%Y-%m-%d")
        if date_from and uploaded_date < date_from:
            continue
        if date_to and uploaded_date > date_to:
            continue

        files.append(
            {
                "name": relative_name,
                "size": bytes_to_human(stat.st_size),
                "size_bytes": stat.st_size,
                "is_pdf": item.suffix.lower() == ".pdf",
                "type": storage_type,
                "extension": item.suffix.lower(),
                "uploaded_timestamp": uploaded_timestamp,
                "modified_timestamp": uploaded_timestamp,
                "uploaded": datetime.fromtimestamp(uploaded_timestamp).strftime(
                    "%H:%M:%S %d/%m/%Y"
                ),
                "modified": datetime.fromtimestamp(uploaded_timestamp).strftime(
                    "%H:%M:%S %d/%m/%Y"
                ),
            }
        )

    if sort == "name":
        key = lambda file: file["name"].lower()
    elif sort == "size":
        key = lambda file: file["size_bytes"]
    elif sort == "type":
        key = lambda file: (file["type"], file["name"].lower())
    else:
        key = lambda file: file["uploaded_timestamp"]

    files.sort(key=key, reverse=(order == "desc"))
    return files

def resolve_upload_path(filename):
    candidate = (UPLOAD_DIR / filename).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if candidate != upload_root and upload_root not in candidate.parents:
        abort(404)
    return candidate


def resolve_upload_file(filename):
    candidate = resolve_upload_path(filename)
    if not candidate.is_file():
        abort(404)
    return candidate


def build_safe_upload_path(filename):
    parts = []
    for part in filename.replace("\\", "/").split("/"):
        if not part or part in {".", ".."}:
            continue
        safe_part = secure_filename(part)
        if safe_part:
            parts.append(safe_part)

    if not parts:
        return None

    target = (UPLOAD_DIR.joinpath(*parts)).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if upload_root not in target.parents:
        abort(400)
    return target


def save_uploaded_files(uploaded_files):
    saved = []
    for uploaded_file in uploaded_files:
        if not uploaded_file or not uploaded_file.filename:
            continue

        target = build_safe_upload_path(uploaded_file.filename)
        if target is None:
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        uploaded_file.save(target)
        saved.append(target.relative_to(UPLOAD_DIR).as_posix())

    return saved


def remove_empty_parent_dirs(path):
    upload_root = UPLOAD_DIR.resolve()
    parent = path.parent.resolve()
    while parent != upload_root and upload_root in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent.resolve()


@app.route("/")
def index():
    return render_template("index.html", **build_dashboard_context())


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/files", methods=["GET", "POST"])
def files():
    message = None
    error = None
    q = request.args.get("q", "")
    sort = request.args.get("sort", "uploaded")
    order = request.args.get("order", "desc")
    file_type = request.args.get("type", "all")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    q, sort, order, file_type, date_from, date_to = normalize_storage_filters(
        q, sort, order, file_type, date_from, date_to
    )

    if request.method == "POST":
        require_csrf_token()
        uploaded_files = request.files.getlist("uploads")
        saved_files = save_uploaded_files(uploaded_files)
        if not saved_files:
            error = "Vui lòng chọn file hoặc thư mục để tải lên."
        else:
            if len(saved_files) == 1:
                message = f"Đã tải lên {saved_files[0]}."
            else:
                message = f"Đã tải lên {len(saved_files)} file."

    return render_template(
        "files.html",
        files=list_storage_files(
            q=q,
            sort=sort,
            order=order,
            file_type=file_type,
            date_from=date_from,
            date_to=date_to,
        ),
        upload_dir=str(UPLOAD_DIR),
        message=message,
        error=error,
        max_upload="1 GB",
        q=q,
        sort=sort,
        order=order,
        file_type=file_type,
        date_from=date_from,
        date_to=date_to,
    )


@app.route("/files/download/<path:filename>")
def download_file(filename):
    file_path = resolve_upload_file(filename)
    relative_path = file_path.relative_to(UPLOAD_DIR).as_posix()
    return send_from_directory(UPLOAD_DIR, relative_path, as_attachment=True)


@app.route("/files/preview/<path:filename>")
def preview_file(filename):
    file_path = resolve_upload_file(filename)
    if file_path.suffix.lower() != ".pdf":
        abort(404)
    relative_path = file_path.relative_to(UPLOAD_DIR).as_posix()
    return send_from_directory(
        UPLOAD_DIR,
        relative_path,
        as_attachment=False,
        mimetype="application/pdf",
    )


@app.route("/files/delete/<path:filename>", methods=["POST"])
def delete_file(filename):
    require_csrf_token()
    file_path = resolve_upload_file(filename)
    file_path.unlink()
    remove_empty_parent_dirs(file_path)
    return redirect(url_for("files"))


@app.route("/api/status")
def api_status():
    return jsonify(get_status_info())


@app.route("/api/system")
def api_system():
    status = get_status_info()
    system = get_system_info()
    return jsonify(
        {
            **system,
            "hostname": status["hostname"],
            "ip_lan": status["ip_lan"],
            "ip_tailscale": status["ip_tailscale"],
            "cpu_percent": system["cpu_usage"],
            "ram_percent": system["ram_usage"],
            "disk_percent": system["disk_usage"],
            "current_time": status["current_time"],
            "status": status["status"],
        }
    )


@app.route("/api/storage")
def api_storage():
    q = request.args.get("q", "")
    sort = request.args.get("sort", "uploaded")
    order = request.args.get("order", "desc")
    file_type = request.args.get("type", "all")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    storage_info = get_storage_info(
        q=q,
        sort=sort,
        order=order,
        file_type=file_type,
        date_from=date_from,
        date_to=date_to,
    )

    if request.args.get("format") == "json":
        return jsonify(storage_info)

    accept_header = request.headers.get("Accept", "")
    if "text/html" in accept_header and "application/json" not in accept_header:
        return render_template(
            "api_storage.html",
            storage=storage_info,
            storage_json=json.dumps(storage_info, ensure_ascii=False, indent=2),
        )

    return jsonify(storage_info)


@app.route("/api/releases/upload", methods=["POST"])
def api_releases_upload():
    require_csrf_token()
    try:
        uploaded = save_release_upload(
            request.files.get("manifest"),
            request.files.get("artifact"),
            request.form.get("app_id", ""),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": f"Không thể lưu release: {exc}"}), 500
    return jsonify({"ok": True, "release": uploaded})


@app.route("/api/releases")
def api_releases():
    return jsonify({"releases": list_app_releases(), "public_base_url": PUBLIC_RELEASE_BASE_URL})


@app.route("/api/release/confirm-update", methods=["POST"])
def api_confirm_release_update():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Dữ liệu xác nhận không hợp lệ."}), 400
    device_id = str(payload.get("deviceId") or payload.get("device_id") or "").strip()
    version = str(payload.get("version") or "").strip()
    app_id_value = payload.get("appId") or payload.get("app_id")
    app_id = normalize_release_app_id(str(app_id_value)) if app_id_value else None
    if not device_id or not version:
        return jsonify({"ok": False, "error": "Thiếu deviceId hoặc version."}), 400
    if app_id_value and app_id is None:
        return jsonify({"ok": False, "error": "app_id không hợp lệ."}), 400
    try:
        release, changed = confirm_release_update(version, device_id, app_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify(
        {
            "ok": True,
            "changed": changed,
            "app_id": release["app_id"],
            "version": release["latest_version"],
            "status": release["release_status"],
            "state_label": release["state_label"],
        }
    )


@app.route("/releases", methods=["GET", "POST"])
def releases_index():
    message = None
    error = None
    uploaded = None

    if request.method == "POST":
        require_csrf_token()
        try:
            uploaded = save_release_upload(
                request.files.get("manifest"),
                request.files.get("artifact"),
                request.form.get("app_id", ""),
            )
            return redirect(
                url_for("releases_index", staged="1", app_id=uploaded["app_id"])
            )
        except ValueError as exc:
            error = str(exc)
        except OSError as exc:
            error = f"Không thể lưu release: {exc}"

    releases = list_app_releases()
    if request.method == "GET":
        result_app_id = normalize_release_app_id(request.args.get("app_id", ""))
        if request.args.get("staged") == "1" and result_app_id:
            message = "Đã tải lên bản chờ. Bấm Phát hành để người dùng nhận cập nhật."
            release = next(
                (item for item in releases if item["app_id"] == result_app_id),
                None,
            )
            uploaded = release["pending"] if release and release["has_pending"] else None
        elif request.args.get("pending_update") == "1" and result_app_id:
            message = f"{result_app_id} đang chờ người dùng cập nhật."
            uploaded = next(
                (
                    release
                    for release in releases
                    if release["app_id"] == result_app_id
                ),
                None,
            )
        elif request.args.get("discarded") == "1" and result_app_id:
            message = f"Đã hủy bản chờ {result_app_id}."
        elif request.args.get("deleted") == "1" and result_app_id:
            message = f"Đã xóa {result_app_id}."

    return render_releases_page(
        message=message, error=error, uploaded=uploaded, releases=releases
    )


@app.route("/releases/<app_id>/publish", methods=["POST"])
def publish_release(app_id):
    require_csrf_token()
    try:
        publish_pending_release(app_id)
    except ValueError as exc:
        return render_releases_page(error=str(exc)), 400
    except OSError as exc:
        return render_releases_page(error=f"Không thể phát hành: {exc}"), 500
    return redirect(url_for("releases_index", pending_update="1", app_id=app_id))


@app.route("/releases/<app_id>/discard", methods=["POST"])
def discard_release(app_id):
    require_csrf_token()
    app_dir = get_release_app_dir(app_id, allow_missing=True)
    pending_dir = app_dir / "pending"
    if pending_dir.is_dir() and not pending_dir.is_symlink():
        shutil.rmtree(pending_dir)
    return redirect(url_for("releases_index", discarded="1", app_id=app_id))


@app.route("/releases/<app_id>/delete", methods=["POST"])
def delete_release_app(app_id):
    require_csrf_token()
    app_dir = resolve_release_app_dir(app_id)
    if app_dir is None:
        return render_releases_page(error="Không tìm thấy app."), 404
    try:
        shutil.rmtree(app_dir)
    except OSError as exc:
        return render_releases_page(error=f"Không thể xóa app: {exc}"), 500
    return redirect(url_for("releases_index", deleted="1", app_id=app_id))


@app.route("/releases/<path:filename>")
def release_file(filename):
    public_file = resolve_public_release_file(RELEASES_DIR, filename)
    if public_file is None:
        abort(404)
    relative_path = public_file.relative_to(RELEASES_DIR).as_posix()
    return send_from_directory(RELEASES_DIR, relative_path)


@app.errorhandler(413)
def file_too_large(_error):
    if request.path == "/api/releases/upload":
        return jsonify({"ok": False, "error": "File quá lớn. Giới hạn tối đa là 1 GB."}), 413
    if request.path == "/releases":
        return render_releases_page(error="File quá lớn. Giới hạn tối đa là 1 GB."), 413
    return (
        render_template(
            "files.html",
            files=list_storage_files(),
            upload_dir=str(UPLOAD_DIR),
            message=None,
            error="File quá lớn. Giới hạn tối đa là 1 GB.",
            max_upload="1 GB",
        ),
        413,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
