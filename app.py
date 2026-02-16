from flask import Flask, render_template, request, redirect, url_for, flash, send_file, abort, session, make_response, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime, date
from sqlalchemy import text, case, or_, inspect, func
from sqlalchemy.orm import deferred
import os
from fpdf import FPDF
from werkzeug.utils import secure_filename
from io import BytesIO
import zipfile
import hashlib
from PIL import Image, ImageOps
from functools import wraps
import csv
import threading
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side
import re


import uuid
import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
# --------- Util: compress/resize uploaded photos ---------
def process_uploaded_photo(file_storage, max_size=(1600, 1600), quality=80, thumb_max_size=(480, 480), thumb_quality=65):
    """Resize/compress an uploaded image to keep DB small and fast.

    Returns (filename, content_type, data_bytes, thumb_bytes, thumb_content_type)
    or (None, None, None, None, None) on failure.
    """
    if not file_storage or not getattr(file_storage, "filename", None):
        return None, None, None, None, None

    try:
        raw = file_storage.read()
        if not raw:
            return None, None, None, None, None

        img = Image.open(BytesIO(raw))
        # Corrige orientação baseada no EXIF (fotos de celular)
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        # garante formato compatível
        img = img.convert("RGB")

        # FULL (já comprimida)
        full = img.copy()
        full.thumbnail(max_size)
        buf = BytesIO()
        full.save(buf, format="JPEG", quality=quality, optimize=True)
        full_bytes = buf.getvalue()

        # THUMB
        thumb = img.copy()
        thumb.thumbnail(thumb_max_size)
        buf2 = BytesIO()
        thumb.save(buf2, format="JPEG", quality=thumb_quality, optimize=True)
        thumb_bytes = buf2.getvalue()

        base_name, _ = os.path.splitext(file_storage.filename or "foto.jpg")
        safe_name = secure_filename(base_name) or "foto"
        filename = f"{safe_name}.jpg"

        return filename[:255], "image/jpeg", full_bytes, thumb_bytes, "image/jpeg"

    except Exception:
        # Se der qualquer erro ao processar a imagem, tenta salvar o arquivo original (sem thumb)
        try:
            try:
                file_storage.seek(0)
            except Exception:
                pass
            data = file_storage.read()
            if not data:
                return None, None, None, None, None
            filename = secure_filename(file_storage.filename) or "arquivo.bin"
            return filename[:255], (file_storage.mimetype or "application/octet-stream"), data, None, None
        except Exception:
            return None, None, None, None, None



# --------- App & DB setup ---------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-key")

# Database configuration: prefer DATABASE_URL/RENDER_DATABASE_URL (e.g. Render PostgreSQL),
# fallback to local SQLite for development.
db_url = os.environ.get("DATABASE_URL") or os.environ.get("RENDER_DATABASE_URL") or "sqlite:///data.db"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# --------- R2 (S3-compatible) storage helpers ---------
_r2_client = None

def r2_bucket_name() -> str:
    """Bucket name for Cloudflare R2.

    The Render dashboard screenshots show env var name `R2_BUCKET_NAME`, while older versions used `R2_BUCKET`.
    We support both.
    """
    return (os.environ.get("R2_BUCKET_NAME") or os.environ.get("R2_BUCKET") or "").strip()

def r2_enabled() -> bool:
    return bool(
        os.environ.get("R2_ACCESS_KEY_ID")
        and os.environ.get("R2_SECRET_ACCESS_KEY")
        and os.environ.get("R2_ENDPOINT")
        and r2_bucket_name()
    )

def r2_client():
    global _r2_client
    if _r2_client is not None:
        return _r2_client
    if not r2_enabled():
        return None
    _r2_client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("R2_ENDPOINT"),
        aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("R2_REGION", "auto"),
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return _r2_client

def r2_bucket() -> str:
    # Backwards compatible wrapper
    return r2_bucket_name()

def r2_put_bytes(key: str, data: bytes, content_type: str | None = None) -> dict:
    c = r2_client()
    if c is None:
        raise RuntimeError("R2 is not enabled")
    extra = {}
    if content_type:
        extra["ContentType"] = content_type
    resp = c.put_object(Bucket=r2_bucket(), Key=key, Body=data, **extra)
    return resp or {}

def r2_get_bytes(key: str) -> bytes:
    c = r2_client()
    if c is None:
        raise RuntimeError("R2 is not enabled")
    obj = c.get_object(Bucket=r2_bucket(), Key=key)
    return obj["Body"].read()

def r2_key_for_record_photo(record_id: int, filename: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", (filename or "photo"))
    return f"records/{record_id}/{uuid.uuid4().hex}_{safe}"


def optimize_upload_bytes(file_bytes: bytes, original_content_type: str) -> tuple[bytes, str]:
    """Reduce payload size for uploads.

    - If image: auto-rotate (EXIF), downscale, and re-encode to JPEG.
    - Otherwise: return as-is.
    """
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")

        max_size = int(os.environ.get("UPLOAD_IMAGE_MAX_SIDE", "1600"))
        img.thumbnail((max_size, max_size))

        out = io.BytesIO()
        quality = int(os.environ.get("UPLOAD_IMAGE_JPEG_QUALITY", "75"))
        img.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return file_bytes, (original_content_type or "application/octet-stream")


def enqueue_r2_upload(photo_id: int, key: str, data: bytes, content_type: str):
    """Upload to R2 in a background thread (NOLOSS).

    We keep the compressed bytes in Postgres as a fallback. When upload succeeds we set r2_key.
    Optionally clear DB bytes with CLEAR_DB_AFTER_R2=1.
    """
    if not r2_enabled():
        return

    def _worker():
        try:
            with app.app_context():
                p = RecordPhoto.query.get(photo_id)
                if not p or p.r2_key:
                    return
                r2_put_bytes(key, data, content_type=content_type)
                p.r2_key = key
                if os.environ.get("CLEAR_DB_AFTER_R2", "0") == "1":
                    p.data = b""
                db.session.commit()
        except Exception:
            try:
                with app.app_context():
                    db.session.rollback()
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True).start()

# --------- Models ---------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)  # simples, sem hash, uso local
    is_admin = db.Column(db.Boolean, default=False)
    splicer_name = db.Column(db.String(120), nullable=True)  # nome que aparece como Splicer nos lançamentos
    is_company_owner = db.Column(db.Boolean, default=False, nullable=False)  # dono de empresa: vê registros da própria empresa
    company_name = db.Column(db.String(120), nullable=True)  # nome da empresa a que o usuário pertence

class CompanyConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    included_splices = db.Column(db.Integer, default=1, nullable=False)  # fusões inclusas por lançamento
    invoice_address = db.Column(db.Text, nullable=True)  # nome + endereço p/ usar na invoice


class Project(db.Model):
    """Projeto dentro de uma empresa, com regras e valores próprios."""
    id = db.Column(db.Integer, primary_key=True)
    company = db.Column(db.String(120), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    # Se None, usa o valor da empresa (CompanyConfig.included_splices)
    included_splices = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('company', 'name', name='uq_project_company_name'),
    )


class SystemConfig(db.Model):
    """Configurações gerais do sistema (dados da sua empresa para sair na invoice)."""
    id = db.Column(db.Integer, primary_key=True)
    my_company_name = db.Column(db.String(200), nullable=True)
    my_company_address = db.Column(db.Text, nullable=True)
    my_company_tax_id = db.Column(db.String(120), nullable=True)
    my_company_email = db.Column(db.String(120), nullable=True)
    my_company_phone = db.Column(db.String(60), nullable=True)



class Invoice(db.Model):
    """Invoices geradas para controle contábil."""
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(50), nullable=False, unique=True)
    company = db.Column(db.String(120), nullable=False)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    total_usd = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending / paid
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    pdf_filename = db.Column(db.String(255), nullable=True)
    pdf_content_type = db.Column(db.String(100), nullable=True)
    pdf_data = db.Column(db.LargeBinary, nullable=True)

    created_by_user = db.relationship('User', foreign_keys=[created_by], backref=db.backref('invoices_created', lazy=True))

class DeviceType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    value_usd = db.Column(db.Float, default=0.0, nullable=False)
    company = db.Column(db.String(120), nullable=True)  # se None = valor padrão
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    project = db.relationship('Project', backref=db.backref('device_types', lazy=True))

class SpliceTier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    min_splices = db.Column(db.Integer, nullable=False)
    max_splices = db.Column(db.Integer, nullable=True)
    price_per_splice_usd = db.Column(db.Float, default=0.0, nullable=False)
    company = db.Column(db.String(120), nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    project = db.relationship('Project', backref=db.backref('splice_tiers', lazy=True))

class CompanyMap(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company = db.Column(db.String(120), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    project = db.relationship('Project', backref=db.backref('maps', lazy=True))

    # Opcional (por mapa): habilita seleção MEIO/PONTA no lançamento.
    # Só aparece no Entry se mid_end_enabled=1.
    mid_end_enabled = db.Column(db.Boolean, nullable=False, default=False)
    included_splices_meio = db.Column(db.Integer, nullable=True)   # inclusas quando MEIO
    included_splices_ponta = db.Column(db.Integer, nullable=True)  # inclusas quando PONTA

class Record(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    map = db.Column(db.String(200))
    type = db.Column(db.String(120))
    splices = db.Column(db.Integer)
    device = db.Column(db.String(120))
    splicer = db.Column(db.String(120))
    created_date = db.Column(db.DateTime, nullable=True)
    company = db.Column(db.String(120), nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    project = db.relationship('Project', backref=db.backref('records', lazy=True))

    # Snapshot (opcional): se o mapa estiver configurado com MEIO/PONTA,
    # salvamos o tipo escolhido e quantas fusões inclusas foram aplicadas.
    map_role = db.Column(db.String(10), nullable=True)  # 'MEIO' / 'PONTA'
    included_splices_applied = db.Column(db.Integer, nullable=True)

    price_splices_usd = db.Column(db.Float, default=0.0)
    price_device_usd = db.Column(db.Float, default=0.0)
    total_usd = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class RecordPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey('record.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100))
    data = deferred(db.Column(db.LargeBinary, nullable=False))  # may be empty bytes when stored in R2
    r2_key = db.Column(db.String(512))
    r2_thumb_key = db.Column(db.String(512))
    size_bytes = db.Column(db.Integer)
    thumb_data = deferred(db.Column(db.LargeBinary))
    thumb_content_type = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    record = db.relationship('Record', backref=db.backref('photos', lazy=True))


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("expenses", lazy=True))
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(120), nullable=True)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    date = db.Column(db.Date, nullable=False, default=date.today)

    paid = db.Column(db.Boolean, nullable=False, default=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    paid_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)


    paid_by_user = db.relationship("User", foreign_keys=[paid_by], backref=db.backref("expenses_paid", lazy=True))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# --------- User loader ---------
@login_manager.user_loader
def load_user(user_id: str):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None

def ensure_db_schema():
    """Best-effort schema updates (no destructive changes)."""
    try:
        insp = inspect(db.engine)
        cols = {c["name"] for c in insp.get_columns("record_photo")}
    except Exception:
        cols = set()

    needed = {
        "r2_key": "VARCHAR(512)",
        "r2_thumb_key": "VARCHAR(512)",
        "size_bytes": "INTEGER",
    }

    dialect = getattr(db.engine.dialect, "name", "").lower()
    for col, coltype in needed.items():
        if col in cols:
            continue
        try:
            if dialect == "postgresql":
                db.session.execute(text(f'ALTER TABLE record_photo ADD COLUMN IF NOT EXISTS {col} {coltype}'))
            else:
                # SQLite: no IF NOT EXISTS on older versions; we already checked.
                db.session.execute(text(f'ALTER TABLE record_photo ADD COLUMN {col} {coltype}'))
            db.session.commit()
        except Exception:
            db.session.rollback()

# --------- DB init & migrations simples ---------
with app.app_context():
    db.create_all()
    ensure_db_schema()

    # garante usuário padrão
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(username="admin", password="admin", is_admin=True, splicer_name="ADMIN"))
        db.session.commit()

    # migração simples de colunas (funciona tanto em SQLite quanto em Postgres)
    def ensure(table, col, typ):
        """Garante que uma coluna exista na tabela informada."""
        inspector = inspect(db.engine)
        existing = [c["name"] for c in inspector.get_columns(table)]
        if col not in existing:
            # Em Postgres, usar aspas duplas no nome da tabela evita problemas de case
            db.session.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {col} {typ}'))
            db.session.commit()

    ensure("record", "company", "VARCHAR(120)")
    ensure("device_type", "company", "VARCHAR(120)")
    ensure("splice_tier", "company", "VARCHAR(120)")
    ensure("record", "project_id", "INTEGER")
    ensure("record", "map_role", "VARCHAR(10)")
    ensure("record", "included_splices_applied", "INTEGER")
    ensure("device_type", "project_id", "INTEGER")
    ensure("splice_tier", "project_id", "INTEGER")
    ensure("company_map", "project_id", "INTEGER")
    ensure("company_map", "mid_end_enabled", "BOOLEAN")
    ensure("company_map", "included_splices_meio", "INTEGER")
    ensure("company_map", "included_splices_ponta", "INTEGER")
    ensure("record_photo", "thumb_data", "BYTEA")
    ensure("record_photo", "thumb_content_type", "VARCHAR(100)")
    ensure("project", "company", "VARCHAR(120)")
    ensure("project", "name", "VARCHAR(200)")
    ensure("project", "included_splices", "INTEGER")
    ensure("company_config", "invoice_address", "TEXT")
    ensure("user", "is_admin", "BOOLEAN")
    ensure("user", "splicer_name", "VARCHAR(120)")
    ensure("expense", "paid", "BOOLEAN")
    ensure("expense", "paid_at", "TIMESTAMP")
    ensure("expense", "paid_by", "INTEGER")
    ensure("invoice", "created_by", "INTEGER")
    ensure("invoice", "pdf_filename", "VARCHAR(255)")
    ensure("invoice", "pdf_content_type", "VARCHAR(100)")
    ensure("invoice", "pdf_data", "BYTEA" if 'postgres' in db.engine.name else "BLOB")

    # garante valor padrão para despesas antigas
    try:
        db.session.execute(text('UPDATE "expense" SET paid = 0 WHERE paid IS NULL'))
        db.session.commit()
    except Exception:
        pass

# --------- Login ---------
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            flash("Login realizado com sucesso.", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))

        flash("Usuário ou senha inválidos.", "danger")

    return render_template("login.html")

# --------- Helpers de preço ---------
def included_splices_for(company: str | None, project_id: int | None = None) -> int:
    """Quantas fusões são inclusas para essa empresa/projeto."""
    # Prioridade: projeto -> empresa -> padrão antigo
    if project_id:
        pr = Project.query.get(int(project_id))
        if pr and pr.included_splices is not None:
            return int(pr.included_splices or 0)

    if company:
        cfg = CompanyConfig.query.filter_by(name=company).first()
        if cfg:
            return int(cfg.included_splices or 0)

    return 1

def device_value_for(name: str, company: str | None, project_id: int | None = None) -> float:
    if not name:
        return 0.0

    # Prioridade de busca:
    # 1) dispositivo do projeto
    # 2) dispositivo da empresa
    # 3) dispositivo global (company NULL / project NULL)
    q = DeviceType.query.filter(DeviceType.name.ilike(name))

    if project_id:
        q = q.filter(or_(DeviceType.project_id == project_id, DeviceType.project_id.is_(None)))
    else:
        q = q.filter(DeviceType.project_id.is_(None))

    if company:
        q = q.filter(or_(DeviceType.company == company, DeviceType.company.is_(None)))
        order_clauses = []
        if project_id:
            order_clauses.append(case((DeviceType.project_id == project_id, 0), else_=1))
        # prioriza company específico quando existir
        order_clauses.append(case((DeviceType.company == company, 0), else_=1))
        dt = q.order_by(*order_clauses).first()
    else:
        dt = q.first()

    return float(dt.value_usd) if dt else 0.0

def tier_price_for(count: int, company: str | None, project_id: int | None = None) -> float:
    from sqlalchemy import or_ as _or, case as _case

    q = SpliceTier.query.filter(SpliceTier.min_splices <= count)

    if project_id:
        q = q.filter(_or(SpliceTier.project_id == project_id, SpliceTier.project_id.is_(None)))
    else:
        q = q.filter(SpliceTier.project_id.is_(None))

    q = q.filter(_or(SpliceTier.max_splices == None, SpliceTier.max_splices >= count))

    if company:
        q = q.filter(_or(SpliceTier.company == company, SpliceTier.company.is_(None)))
        order_clauses = []
        if project_id:
            order_clauses.append(_case((SpliceTier.project_id == project_id, 0), else_=1))
        order_clauses.append(_case((SpliceTier.company == company, 0), else_=1))
        order_clauses.append(SpliceTier.min_splices.desc())
        tier = q.order_by(*order_clauses).first()
    else:
        tier = q.order_by(SpliceTier.min_splices.desc()).first()

    return float(tier.price_per_splice_usd) if tier else 0.0


def compute_prices(
    splices: int,
    device_name: str,
    company: str | None,
    project_id: int | None = None,
    included_override: int | None = None,
):
    """Calcula preço de fusões e dispositivo para um lançamento manual.

    Se included_override vier preenchido, ele tem prioridade (ex.: mapas com regra MEIO/PONTA).
    """
    included = int(included_override) if included_override is not None else included_splices_for(company, project_id)
    charge = max(int(splices or 0) - included, 0)
    price_splices = charge * tier_price_for(charge, company, project_id)
    price_device = device_value_for(device_name or "", company, project_id)
    return price_splices, price_device, price_splices + price_device



# --------- Decorators ---------

def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            flash("Apenas o administrador pode acessar essa área.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper

# --------- Rotas ---------
@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    # Importar planilha foi removido do sistema; qualquer POST apenas mostra aviso.
    if request.method == "POST":
        flash("A importação de planilha foi desativada neste sistema.", "warning")
        return redirect(url_for("index"))

    # filtros vindos da URL
    company_filter = request.args.get("company") or None
    splicer_filter = request.args.get("splicer") or None
    map_filter = request.args.get("map") or None
    device_filter = request.args.get("device") or None
    start_raw = request.args.get("start") or None
    end_raw = request.args.get("end") or None

    # base da consulta
    query = Record.query

    # regras de visibilidade por tipo de usuário
    is_admin = getattr(current_user, "is_admin", False)
    is_owner = getattr(current_user, "is_company_owner", False)
    enforced_splicer = None

    if is_admin:
        # Admin enxerga todos os registros, sem restrição adicional.
        pass
    elif is_owner:
        # Dono de empresa enxerga todos os registros da PRÓPRIA empresa.
        owner_company = getattr(current_user, "company_name", None)
        if owner_company:
            company_filter = owner_company
            # ignoramos qualquer empresa passada manualmente na URL
    else:
        # Splicer normal vê apenas os lançamentos feitos por ele mesmo.
        enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
        query = query.filter(Record.splicer == enforced_splicer)
        # evita que tente filtrar manualmente outro splicer pela URL
        splicer_filter = None

    # filtros principais
    if company_filter:
        query = query.filter(Record.company == company_filter)
    if splicer_filter and (is_admin or is_owner):
        # apenas admin / dono de empresa podem filtrar por outro splicer
        query = query.filter(Record.splicer == splicer_filter)
    if map_filter:
        query = query.filter(Record.map.ilike(f"%{map_filter}%"))
    if device_filter:
        query = query.filter(Record.device.ilike(f"%{device_filter}%"))

    # filtros por data
    if start_raw:
        try:
            start_dt = datetime.fromisoformat(start_raw)
            query = query.filter(Record.created_date >= start_dt)
        except ValueError:
            pass
    if end_raw:
        try:
            end_dt = datetime.fromisoformat(end_raw)
            query = query.filter(Record.created_date <= end_dt)
        except ValueError:
            pass

    records = query.order_by(Record.created_date.desc().nullslast(), Record.id.desc()).all()
    total_rows = len(records)
    total_amount = sum(r.total_usd or 0 for r in records)

    companies = [c.name for c in CompanyConfig.query.order_by(CompanyConfig.name).all()]
    companies_from_records = {
        c for (c,) in db.session.query(Record.company).distinct().all() if c
    }
    all_companies = sorted(set(companies) | companies_from_records)

    # lista de splicers / usuários cadastrados (para o filtro)
    splicers_from_records = {
        s for (s,) in db.session.query(Record.splicer).distinct().all() if s
    }
    splicers_from_users = {
        (u.splicer_name or u.username)
        for u in User.query.all()
        if (u.splicer_name or u.username)
    }
    all_splicers = sorted(splicers_from_records | splicers_from_users)

    # Para usuários normais, o dropdown DEVE mostrar só ele mesmo.
    # Para dono de empresa, mantemos a lista completa (pode filtrar por qualquer splicer da empresa).
    if not is_admin and not is_owner:
        if enforced_splicer:
            all_splicers = [enforced_splicer]
            splicer_filter = enforced_splicer

    return render_template(
        "index.html",
        records=records,
        total_rows=total_rows,
        total_amount=total_amount,
        companies=all_companies,
        splicers=all_splicers,
        company_filter=company_filter or "",
        splicer_filter=splicer_filter or "",
        map_filter=map_filter or "",
        device_filter=device_filter or "",
        start=start_raw or "",
        end=end_raw or "",
    )

def build_filtered_record_query_from_request():
    """Reaproveita os filtros da tela principal para buscar os registros."""
    company_filter = request.args.get("company") or None
    map_filter = request.args.get("map") or None
    device_filter = request.args.get("device") or None
    splicer_filter = request.args.get("splicer") or None
    start_raw = request.args.get("start") or None
    end_raw = request.args.get("end") or None

    query = Record.query

    # Restrições de visibilidade por tipo de usuário
    is_admin = getattr(current_user, "is_admin", False)
    is_owner = getattr(current_user, "is_company_owner", False)

    if is_admin:
        # Admin enxerga todos os registros; os filtros são aplicados mais abaixo.
        pass
    elif is_owner:
        # Dono de empresa enxerga todos os registros da própria empresa.
        # Ignoramos o filtro de empresa vindo da URL, se houver.
        owner_company = getattr(current_user, "company", None) or getattr(current_user, "company_name", None)
        if owner_company:
            company_filter = owner_company
    else:
        # Splicer comum: sempre vê apenas os próprios registros.
        enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
        query = query.filter(Record.splicer == enforced_splicer)
        # Evita que o splicer tente ver registros de outra pessoa via URL.
        splicer_filter = None

    # Filtros por campos principais
    if company_filter:
        query = query.filter(Record.company == company_filter)
    if map_filter:
        query = query.filter(Record.map == map_filter)
    if device_filter:
        query = query.filter(Record.device == device_filter)

    # Filtro de splicer (somente admin ou dono de empresa)
    if splicer_filter and (is_admin or is_owner):
        query = query.filter(Record.splicer == splicer_filter)

    # Filtro por datas
    # OBS: o modelo Record não possui coluna "date". A data usada no sistema é "created_date" (ou "created_at" como fallback).
    record_day = func.date(func.coalesce(Record.created_date, Record.created_at))

    if start_raw:
        try:
            start_date = datetime.strptime(start_raw, "%Y-%m-%d").date()
            query = query.filter(record_day >= start_date)
        except ValueError:
            pass

    if end_raw:
        try:
            end_date = datetime.strptime(end_raw, "%Y-%m-%d").date()
            query = query.filter(record_day <= end_date)
        except ValueError:
            pass

@app.route("/photo/<int:photo_id>")
@login_required
def photo_file(photo_id: int):
    """Retorna o binário de uma foto salva em RecordPhoto.

    Performance:
    - suporte a thumbnail via ?size=thumb
    - cache agressivo (as fotos são imutáveis por ID)
    - suporta fotos antigas no banco (BLOB) e novas no R2 (r2_key)
    """
    photo = RecordPhoto.query.get_or_404(photo_id)

    want_thumb = (request.args.get("size") == "thumb")

    # ETag leve (não faz md5 do arquivo inteiro)
    etag = f'W/"{photo.id}-{"t" if want_thumb else "o"}-{photo.r2_thumb_key or ""}-{photo.r2_key or ""}-{photo.size_bytes or 0}"'
    if request.headers.get("If-None-Match") == etag:
        resp = make_response("", 304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    def get_original_bytes() -> tuple[bytes, str]:
        if getattr(photo, "data", None) and len(photo.data) > 0:
            return photo.data, (photo.content_type or "image/jpeg")
        if getattr(photo, "r2_key", None) and r2_enabled():
            try:
                b = r2_get_bytes(photo.r2_key)
                return b, (photo.content_type or "application/octet-stream")
            except Exception:
                pass
        return b"", (photo.content_type or "application/octet-stream")

    blob = b""
    mimetype = "application/octet-stream"

    if want_thumb:
        # 1) thumb no banco
        if getattr(photo, "thumb_data", None):
            blob = photo.thumb_data
            mimetype = photo.thumb_content_type or "image/jpeg"
        # 2) thumb no R2
        elif getattr(photo, "r2_thumb_key", None) and r2_enabled():
            try:
                blob = r2_get_bytes(photo.r2_thumb_key)
                mimetype = "image/jpeg"
            except Exception:
                blob = b""
        # 3) gerar thumb sob demanda (uma vez) e salvar
        if not blob:
            orig, _ct = get_original_bytes()
            if orig:
                try:
                    # gera thumb pequeno (rápido)
                    img = Image.open(BytesIO(orig))
                    img = ImageOps.exif_transpose(img)
                    img.thumbnail((420, 420))
                    out = BytesIO()
                    img.save(out, format="JPEG", quality=70, optimize=True, progressive=True)
                    thumb_bytes = out.getvalue()

                    if getattr(photo, "r2_key", None) and r2_enabled():
                        tkey = photo.r2_thumb_key or (photo.r2_key + ".thumb.jpg")
                        r2_put_bytes(tkey, thumb_bytes, content_type="image/jpeg")
                        photo.r2_thumb_key = tkey
                    else:
                        photo.thumb_data = thumb_bytes
                        photo.thumb_content_type = "image/jpeg"

                    db.session.commit()

                    blob = thumb_bytes
                    mimetype = "image/jpeg"
                except Exception:
                    db.session.rollback()

        if not blob:
            # fallback: retorna original
            blob, mimetype = get_original_bytes()
    else:
        blob, mimetype = get_original_bytes()

    resp = send_file(
        BytesIO(blob),
        mimetype=mimetype,
        as_attachment=False,
        download_name=photo.filename,
        conditional=True,
    )
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


    resp = send_file(
        BytesIO(blob),
        mimetype=mimetype,
        as_attachment=False,
        download_name=photo.filename,
        conditional=True,
    )
    if etag:
        resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.route("/record/<int:rid>/photos", methods=["POST"])
@login_required
def record_add_photos(rid: int):
    """Anexa fotos a um lançamento já criado.
    Usado pelo modo rápido: salva o lançamento primeiro e sobe as fotos depois.
    """
    rec = Record.query.get_or_404(rid)

    # Permissões básicas: splicer só mexe nos próprios registros (admin pode tudo)
    is_admin = bool(getattr(current_user, "is_admin", False))
    is_owner = bool(getattr(current_user, "is_owner", False))
    if not (is_admin or is_owner):
        # se não for admin/dono, deve ser o mesmo splicer
        current_splicer = (getattr(current_user, "splicer_name", None) or current_user.username)
        if (rec.splicer or "") != current_splicer:
            abort(403)

    files = request.files.getlist("photos")
    created = []
    if files:
        for f in files[:5]:

            if not f or not f.filename:
                continue

            # Performance + NOLOSS:
            # 1) Always store *optimized* bytes in Postgres (fast + guarantees we never lose the photo).
            # 2) If R2 is configured, upload to R2 in a background thread and then set r2_key.
            raw = f.read()
            if not raw:
                continue
            filename = (f.filename or "photo").strip()
            optimized, content_type = optimize_upload_bytes(raw, getattr(f, "mimetype", None))

            # Do NOT generate thumbnail at upload time (keeps it fast). Thumbs are generated lazily on demand.
            photo = RecordPhoto(
                record_id=rec.id,
                filename=filename,
                content_type=content_type,
                data=optimized if optimized else b"",  # NOT NULL constraint
                thumb_data=None,
                thumb_content_type=None,
                r2_key=None,
                r2_thumb_key=None,
                size_bytes=int(len(optimized) if optimized else 0),
            )

            db.session.add(photo)
            db.session.flush()

            if r2_enabled() and optimized:
                try:
                    key = r2_key_for_record_photo(rec.id, filename)
                    enqueue_r2_upload(int(photo.id), key, optimized, content_type)
                except Exception:
                    pass

            created.append(int(photo.id))
        db.session.commit()

    return jsonify({"ok": True, "photo_ids": created, "record_id": int(rec.id)})

@app.route("/photos_filtered_zip")
@login_required
def photos_filtered_zip():
    """Gera um .zip com TODAS as fotos dos registros filtrados na tela principal."""

    query = build_filtered_record_query_from_request()
    records = query.all()

    if not records:
        flash("Nenhum registro encontrado para gerar o .zip de fotos.", "warning")
        return redirect(request.referrer or url_for("index"))

    record_ids = [r.id for r in records]
    photos = RecordPhoto.query.filter(RecordPhoto.record_id.in_(record_ids)).all()

    if not photos:
        flash("Nenhuma foto encontrada para os filtros selecionados.", "warning")
        return redirect(request.referrer or url_for("index"))

    mem = BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        rec_by_id = {r.id: r for r in records}
        for photo in photos:
            rec = rec_by_id.get(photo.record_id)
            company_part = (rec.company or "SEM_EMPRESA").replace("/", "-") if rec else "SEM_EMPRESA"
            map_part = (rec.map or "SEM_MAP").replace("/", "-") if rec else "SEM_MAP"
            device_part = (rec.device or f"ID-{photo.record_id}").replace("/", "-") if rec else f"ID-{photo.record_id}"
            safe_filename = photo.filename or f"foto_{photo.id}.jpg"
            zip_path = f"{company_part}/{map_part}/{device_part}/ID-{photo.record_id}_PH-{photo.id}_{safe_filename}"
            zf.writestr(zip_path, photo.data)

    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name="fotos_filtradas.zip",
    )




@app.route("/record/<int:rid>/photos_zip")
@login_required
def record_photos_zip(rid):
    """Gera um .zip com TODAS as fotos de um único lançamento (record)."""
    record = Record.query.get_or_404(rid)

    photos = (
        db.session.query(RecordPhoto)
        .filter(RecordPhoto.record_id == rid)
        .order_by(RecordPhoto.created_at.asc())
        .all()
    )

    if not photos:
        flash("Este dispositivo não possui fotos para download.", "warning")
        return redirect(request.referrer or url_for("index"))

    mem = BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        for photo in photos:
            safe_filename = photo.filename or f"foto_{photo.id}.jpg"
            device_part = (record.device or f"ID-{record.id}").replace("/", "-")
            zip_path = f"{device_part}/ID-{record.id}_PH-{photo.id}_{safe_filename}"
            zf.writestr(zip_path, photo.data)

    mem.seek(0)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    zip_name = f"fotos_dispositivo_{(record.device or record.id)}_{ts}.zip"

    return send_file(
        mem,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zip_name,
    )


@app.route("/entry", methods=["GET", "POST"])
@login_required
def entry():
    """Lançamento manual de produção (uma linha por vez).

    Para splicer: modo rápido (não escolhe projeto). O sistema deriva o projeto pelo mapa.
    """
    companies = [c.name for c in CompanyConfig.query.order_by(CompanyConfig.name).all()]

    # Modo rápido (padrão): não escolhe projeto. O sistema deriva o projeto pelo mapa.
    # Para ADMIN, se quiser o modo completo (com seleção de projeto), use /entry?full=1
    full_mode = bool(getattr(current_user, "is_admin", False)) and (request.args.get("full") == "1")
    is_splicer = not full_mode
    entry_url = url_for('entry', full='1') if full_mode else url_for('entry')

    # Permite reset do "modo rápido" (opcional)
    if request.method == "GET" and request.args.get("reset") == "1":
        for k in ("entry_company", "entry_map_id", "entry_map_role", "entry_type"):
            session.pop(k, None)

    projects_by_company = {}
    for pr in Project.query.order_by(Project.company, Project.name).all():
        projects_by_company.setdefault(pr.company, []).append({"id": pr.id, "name": pr.name})

    maps_by_project = {}
    maps_by_company = {}          # mapas SEM projeto (legado) por empresa
    maps_by_company_all = {}      # todos mapas por empresa (inclui mapas vinculados a projeto) – usado no splicer

    for m in CompanyMap.query.order_by(CompanyMap.company, CompanyMap.name).all():
        mobj = {
            "id": int(m.id),
            "name": m.name,
            "project_id": int(m.project_id) if m.project_id else None,
            "mid_end_enabled": bool(getattr(m, "mid_end_enabled", False)),
            "included_splices_meio": int(getattr(m, "included_splices_meio", 0) or 0),
            "included_splices_ponta": int(getattr(m, "included_splices_ponta", 0) or 0),
        }
        maps_by_company_all.setdefault(m.company, []).append(mobj)

        if m.project_id:
            maps_by_project.setdefault(str(m.project_id), []).append(mobj)
        else:
            maps_by_company.setdefault(m.company, []).append(mobj)

    devices_by_project = {}
    devices_by_company = {}
    for dt in DeviceType.query.order_by(DeviceType.company, DeviceType.name).all():
        if dt.project_id:
            devices_by_project.setdefault(str(dt.project_id), []).append(dt.name)
        else:
            key = dt.company or "__global__"
            devices_by_company.setdefault(key, []).append(dt.name)

    splicer_options = []
    if getattr(current_user, "is_admin", False):
        splicer_options = [
            (u.splicer_name or u.username)
            for u in User.query.order_by(User.username).all()
            if (u.splicer_name or u.username)
        ]

    default_splicer = getattr(current_user, "splicer_name", None) or current_user.username

    # Prefill "modo rápido" (splicer) – mantém enquanto o usuário ficar nessa tela
    pre_company = session.get("entry_company") if is_splicer else None
    pre_map_id = session.get("entry_map_id") if is_splicer else None
    pre_map_role = session.get("entry_map_role") if is_splicer else None
    pre_type = session.get("entry_type") if is_splicer else None

    if request.method == "POST":
        company = (request.form.get("company") or "").strip() or None

        # Para splicer, projeto vem do mapa; para admin pode vir do form.
        project_id_raw = (request.form.get("project_id") or "").strip()
        project_id = int(project_id_raw) if project_id_raw.isdigit() else None

        map_id_raw = (request.form.get("map_id") or "").strip()
        map_val = (request.form.get("map") or "").strip()

        map_obj = None
        if map_id_raw.isdigit():
            map_obj = CompanyMap.query.get(int(map_id_raw))
        else:
            # compatibilidade: se ainda vier pelo nome
            if company and map_val:
                q = CompanyMap.query.filter_by(company=company, name=map_val)
                map_obj = q.first()

        # Se for splicer, sempre deriva project_id pelo mapa escolhido
        if is_splicer and map_obj:
            project_id = int(map_obj.project_id) if map_obj.project_id else None
            map_val = map_obj.name

        type_val = (request.form.get("type") or "").strip()
        device_name = (request.form.get("device_name") or "").strip()

        device_for_price = type_val or device_name

        splices_raw = request.form.get("splices") or "0"
        created_raw = request.form.get("created") or ""
        splicer = (request.form.get("splicer") or "").strip() or default_splicer
        confirm_duplicate = (request.form.get("confirm_duplicate") == "yes")

        existing = None
        if map_val and device_name:
            dup_query = Record.query.filter(Record.map == map_val, Record.device == device_name)
            if company:
                dup_query = dup_query.filter(Record.company == company)
            if project_id is not None:
                dup_query = dup_query.filter(Record.project_id == project_id)
            existing = dup_query.order_by(Record.created_date.desc().nullslast(), Record.id.desc()).first()

        if existing and not confirm_duplicate:
            flash(
                "Este dispositivo já foi lançado neste map. Data: "
                + (existing.created_date.date().isoformat() if existing.created_date else "-")
                + f", Splicer: {existing.splicer}. Se desejar lançar novamente, confirme o lançamento.",
                "warning",
            )
            return render_template(
                "entry.html",
                splicer_options=splicer_options,
                companies=companies,
                projects_by_company=projects_by_company,
                maps_by_project=maps_by_project,
                maps_by_company=maps_by_company,
                maps_by_company_all=maps_by_company_all,
                devices_by_project=devices_by_project,
                devices_by_company=devices_by_company,
                default_splicer=default_splicer,
                today=date.today().isoformat(),
                duplicate_record=existing,
                form_company=company,
                form_project_id=str(project_id or ""),
                form_map_id=str(map_obj.id) if map_obj else "",
                form_map=map_val,
                form_type=type_val,
                form_device_name=device_name,
                form_splices=splices_raw,
                form_created=created_raw or date.today().isoformat(),
                form_map_role=(request.form.get("map_role") or ""),
                confirm_duplicate=True,
                is_splicer=is_splicer,
            )

        try:
            splices = int(splices_raw or 0)
        except ValueError:
            splices = 0

        if created_raw:
            try:
                created_date = datetime.strptime(created_raw, "%Y-%m-%d")
            except ValueError:
                created_date = datetime.utcnow()
        else:
            today = date.today()
            created_date = datetime(today.year, today.month, today.day)

        # Regra opcional por MAPA: se o mapa estiver com MEIO/PONTA habilitado,
        # o usuário precisa escolher e o sistema aplica as fusões inclusas do mapa.
        map_role = (request.form.get("map_role") or "").strip().upper() or None

        map_cfg = map_obj
        if not map_cfg and map_val and company:
            map_cfg = CompanyMap.query.filter_by(company=company, name=map_val, project_id=project_id).first()
            if not map_cfg:
                map_cfg = CompanyMap.query.filter_by(company=company, name=map_val, project_id=None).first()

        included_override = None
        included_applied = None
        if map_cfg and bool(getattr(map_cfg, "mid_end_enabled", False)):
            if map_role not in ("MEIO", "PONTA"):
                flash("Este mapa exige selecionar MEIO ou PONTA.", "danger")
                return redirect(entry_url)
            if map_role == "MEIO":
                included_override = int(getattr(map_cfg, "included_splices_meio", 0) or 0)
            else:
                included_override = int(getattr(map_cfg, "included_splices_ponta", 0) or 0)
            included_applied = included_override

        price_splices, price_device, total = compute_prices(
            splices,
            device_for_price,
            company,
            project_id,
            included_override=included_override,
        )

        rec = Record(
            map=map_val,
            type=type_val,
            splices=splices,
            device=device_name,
            splicer=splicer,
            created_date=created_date,
            company=company,
            project_id=project_id,
            map_role=map_role,
            included_splices_applied=included_applied,
            price_splices_usd=price_splices,
            price_device_usd=price_device,
            total_usd=total,
        )
        db.session.add(rec)
        db.session.commit()

        # Se for modo AJAX (usado quando existem fotos), responde rápido com o ID
        ajax_mode = (request.form.get("_ajax") == "1") or (request.args.get("ajax") == "1")
        if ajax_mode:
            # mantém seleções do modo rápido
            if is_splicer:
                session["entry_company"] = company
                if map_obj:
                    session["entry_map_id"] = int(map_obj.id)
                session["entry_map_role"] = map_role or ""
                session["entry_type"] = type_val or ""
            return jsonify({"ok": True, "record_id": int(rec.id)})

        files = request.files.getlist("photos")
        if files:
            for f in files[:5]:
                if not f or not f.filename:
                    continue
                filename, content_type, data, thumb_data, thumb_ct = process_uploaded_photo(f)
                if not data:
                    continue
                photo = RecordPhoto(
                    record_id=rec.id,
                    filename=filename[:255],
                    content_type=content_type,
                    data=data,
                    thumb_data=thumb_data,
                    thumb_content_type=thumb_ct,
                )
                db.session.add(photo)
            db.session.commit()

        # Modo rápido: memoriza última seleção (somente para splicer)
        if is_splicer and company and (map_cfg or map_obj):
            last_map = map_cfg or map_obj
            session["entry_company"] = company
            session["entry_map_id"] = int(last_map.id)
            session["entry_map_role"] = map_role or ""
            session["entry_type"] = type_val or ""

        flash("Lançamento salvo.", "success")
        return redirect(entry_url)

    # GET
    return render_template(
        "entry.html",
        splicer_options=splicer_options,
        companies=companies,
        projects_by_company=projects_by_company,
        maps_by_project=maps_by_project,
        maps_by_company=maps_by_company,
        maps_by_company_all=maps_by_company_all,
        devices_by_project=devices_by_project,
        devices_by_company=devices_by_company,
        default_splicer=default_splicer,
        today=date.today().isoformat(),
        form_company=pre_company or "",
        form_map_id=str(pre_map_id or ""),
        form_map_role=pre_map_role or "",
        form_type=pre_type or "",
        is_splicer=is_splicer,
    )


@app.route("/record/<int:rid>/edit", methods=["GET", "POST"])
@login_required
def record_edit(rid):
    """Editar um lançamento existente."""
    rec = Record.query.get_or_404(rid)

    companies = [c.name for c in CompanyConfig.query.order_by(CompanyConfig.name).all()]

    projects_by_company = {}
    for pr in Project.query.order_by(Project.company, Project.name).all():
        projects_by_company.setdefault(pr.company, []).append({"id": pr.id, "name": pr.name})

    maps_by_project = {}
    maps_by_company = {}
    maps_by_company_all = {}
    for m in CompanyMap.query.order_by(CompanyMap.company, CompanyMap.name).all():
        # Mantém o mesmo formato usado em /entry (lista de objetos),
        # para o JS funcionar também na tela de edição.
        mobj = {
            "id": int(m.id),
            "name": m.name,
            "project_id": int(m.project_id) if m.project_id else None,
            "mid_end_enabled": bool(getattr(m, "mid_end_enabled", False)),
            "included_splices_meio": int(getattr(m, "included_splices_meio", 0) or 0),
            "included_splices_ponta": int(getattr(m, "included_splices_ponta", 0) or 0),
        }
        maps_by_company_all.setdefault(m.company, []).append(mobj)

        if m.project_id:
            maps_by_project.setdefault(str(m.project_id), []).append(mobj)
        else:
            maps_by_company.setdefault(m.company, []).append(mobj)

    devices_by_project = {}
    devices_by_company = {}
    for dt in DeviceType.query.order_by(DeviceType.company, DeviceType.name).all():
        if dt.project_id:
            devices_by_project.setdefault(str(dt.project_id), []).append(dt.name)
        else:
            key = dt.company or "__global__"
            devices_by_company.setdefault(key, []).append(dt.name)

    splicer_options = []
    if getattr(current_user, "is_admin", False):
        splicer_options = [
            (u.splicer_name or u.username)
            for u in User.query.order_by(User.username).all()
            if (u.splicer_name or u.username)
        ]

    default_splicer = getattr(current_user, "splicer_name", None) or current_user.username

    if request.method == "POST":
        company = (request.form.get("company") or "").strip() or None
        project_id_raw = (request.form.get("project_id") or "").strip()
        project_id = int(project_id_raw) if project_id_raw.isdigit() else None
        map_val = (request.form.get("map") or "").strip()
        type_val = (request.form.get("type") or "").strip()
        device_name = (request.form.get("device_name") or "").strip()

        device_for_price = type_val or device_name

        splices_raw = request.form.get("splices") or "0"
        created_raw = request.form.get("created") or ""
        splicer = (request.form.get("splicer") or "").strip() or default_splicer

        try:
            splices = int(splices_raw or 0)
        except ValueError:
            splices = 0

        if created_raw:
            try:
                created_date = datetime.strptime(created_raw, "%Y-%m-%d")
            except ValueError:
                created_date = datetime.utcnow()
        else:
            today = date.today()
            created_date = datetime(today.year, today.month, today.day)

        price_splices, price_device, total = compute_prices(splices, device_for_price, company, project_id)

        rec.company = company
        rec.project_id = project_id
        rec.map = map_val
        rec.type = type_val
        rec.device = device_name
        rec.splices = splices
        rec.splicer = splicer
        rec.created_date = created_date
        rec.price_splices_usd = price_splices
        rec.price_device_usd = price_device
        rec.total_usd = total

        db.session.commit()
        flash("Lançamento atualizado.", "success")
        return redirect(url_for("index"))

    form_created = rec.created_date.date().isoformat() if rec.created_date else date.today().isoformat()

    return render_template(
        "entry.html",
        splicer_options=splicer_options,
        companies=companies,
        projects_by_company=projects_by_company,
        maps_by_project=maps_by_project,
        devices_by_project=devices_by_project,
        maps_by_company=maps_by_company,
        maps_by_company_all=maps_by_company_all,
        devices_by_company=devices_by_company,
        default_splicer=default_splicer,
        today=date.today().isoformat(),
        is_edit=True,
        record=rec,
        form_company=rec.company,
        form_project_id=str(rec.project_id or ""),
        form_map=rec.map,
        form_type=rec.type,
        form_device_name=rec.device,
        form_splices=str(rec.splices or 0),
        form_created=form_created,
    )

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/settings", methods=["GET"])
@admin_required
def settings():
    """Tela principal de cadastro de empresas."""
    companies = CompanyConfig.query.order_by(CompanyConfig.name).all()
    syscfg = SystemConfig.query.first()
    if not syscfg:
        syscfg = SystemConfig()
        db.session.add(syscfg)
        db.session.commit()
    return render_template("settings.html", companies=companies, syscfg=syscfg)

@app.route("/settings/company/add", methods=["POST"])
@login_required
def settings_company_add():
    name = (request.form.get("name") or "").strip()
    included_raw = request.form.get("included_splices") or "0"
    included = int(included_raw or 0)
    invoice_address = (request.form.get("invoice_address") or "").strip() or None

    if not name:
        flash("Nome da empresa é obrigatório.", "danger")
        return redirect(url_for("settings"))

    cfg = CompanyConfig.query.filter_by(name=name).first()
    if cfg:
        cfg.included_splices = included
        cfg.invoice_address = invoice_address
    else:
        cfg = CompanyConfig(name=name, included_splices=included, invoice_address=invoice_address)
        db.session.add(cfg)
    db.session.commit()
    flash("Empresa / fusões inclusas salva.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/company/<int:cid>", methods=["GET", "POST"])
@admin_required
def settings_company_detail(cid: int):
    company = CompanyConfig.query.get_or_404(cid)

    # exclusão de mapa via querystring
    del_map_id = request.args.get("del_map")
    if del_map_id:
        mp = CompanyMap.query.get(int(del_map_id))
        if mp and mp.company == company.name:
            db.session.delete(mp)
            db.session.commit()
            flash("Mapa removido.", "success")
        return redirect(url_for("settings_company_detail", cid=company.id))

    # inclusão de mapa via POST
    if request.method == "POST":
        new_map = (request.form.get("new_map") or "").strip()
        if new_map:
            exists = CompanyMap.query.filter_by(company=company.name, name=new_map).first()
            if not exists:
                db.session.add(CompanyMap(company=company.name, name=new_map))
                db.session.commit()
                flash("Mapa adicionado.", "success")
        return redirect(url_for("settings_company_detail", cid=company.id))

    types = DeviceType.query.filter_by(company=company.name, project_id=None).order_by(DeviceType.name).all()
    tiers = SpliceTier.query.filter_by(company=company.name, project_id=None).order_by(SpliceTier.min_splices).all()
    maps = CompanyMap.query.filter_by(company=company.name, project_id=None).order_by(CompanyMap.name).all()
    projects = Project.query.filter_by(company=company.name).order_by(Project.name).all()
    return render_template(
        "settings_company.html",
        company=company,
        projects=projects,
        types=types,
        tiers=tiers,
        maps=maps,
    )



@app.route("/settings/system", methods=["POST"])
@admin_required
def settings_system_update():
    """Atualiza os dados da sua empresa (emitente da invoice)."""
    name = (request.form.get("my_company_name") or "").strip() or None
    addr = (request.form.get("my_company_address") or "").strip() or None
    taxid = (request.form.get("my_company_tax_id") or "").strip() or None
    email = (request.form.get("my_company_email") or "").strip() or None
    phone = (request.form.get("my_company_phone") or "").strip() or None

    cfg = SystemConfig.query.first()
    if not cfg:
        cfg = SystemConfig()
        db.session.add(cfg)

    cfg.my_company_name = name
    cfg.my_company_address = addr
    cfg.my_company_tax_id = taxid
    cfg.my_company_email = email
    cfg.my_company_phone = phone

    db.session.commit()
    flash("Dados da sua empresa atualizados.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/project/add", methods=["POST"])
@admin_required
def settings_project_add():
    company = (request.form.get("company") or "").strip()
    project_name = (request.form.get("project_name") or "").strip()

    if not company or not project_name:
        flash("Empresa e nome do projeto são obrigatórios.", "danger")
        return redirect(url_for("settings"))

    existing = Project.query.filter_by(company=company, name=project_name).first()
    if existing:
        flash("Projeto já existe.", "warning")
        comp = CompanyConfig.query.filter_by(name=company).first()
        return redirect(url_for("settings_company_detail", cid=comp.id if comp else 0))

    pr = Project(company=company, name=project_name, included_splices=None)
    db.session.add(pr)
    db.session.commit()

    # Copia dispositivos e faixas da empresa (defaults) para servir como base do projeto
    base_types = DeviceType.query.filter_by(company=company, project_id=None).all()
    for dt in base_types:
        db.session.add(DeviceType(name=dt.name, value_usd=dt.value_usd, company=company, project_id=pr.id))

    base_tiers = SpliceTier.query.filter_by(company=company, project_id=None).all()
    for t in base_tiers:
        db.session.add(
            SpliceTier(
                company=company,
                project_id=pr.id,
                min_splices=t.min_splices,
                max_splices=t.max_splices,
                price_per_splice_usd=t.price_per_splice_usd,
            )
        )

    db.session.commit()
    flash("Projeto criado (valores copiados da empresa).", "success")
    return redirect(url_for("settings_project_detail", pid=pr.id))


@app.route("/settings/project/<int:pid>", methods=["GET", "POST"])
@admin_required
def settings_project_detail(pid: int):
    project = Project.query.get_or_404(pid)

    # usado no link "Voltar para empresa" na página do projeto
    comp_cfg = CompanyConfig.query.filter_by(name=project.company).first()
    company_id = comp_cfg.id if comp_cfg else 0

    del_map_id = request.args.get("del_map")
    if del_map_id:
        mp = CompanyMap.query.get(int(del_map_id))
        if mp and mp.project_id == project.id:
            db.session.delete(mp)
            db.session.commit()
            flash("Mapa removido.", "success")
        return redirect(url_for("settings_project_detail", pid=project.id))

    if request.method == "POST":
        if request.form.get("action") == "update_project":
            inc_raw = (request.form.get("included_splices") or "").strip()
            project.included_splices = int(inc_raw) if inc_raw != "" else None
            db.session.commit()
            flash("Projeto atualizado.", "success")
            return redirect(url_for("settings_project_detail", pid=project.id))

        if request.form.get("action") == "update_map_rules":
            mid_raw = (request.form.get("included_meio") or "").strip()
            ponta_raw = (request.form.get("included_ponta") or "").strip()
            enabled = bool(request.form.get("mid_end_enabled"))
            mid = int(mid_raw) if mid_raw != "" else 0
            ponta = int(ponta_raw) if ponta_raw != "" else 0
            mid = max(mid, 0)
            ponta = max(ponta, 0)
            map_id_raw = (request.form.get("map_id") or "").strip()
            if map_id_raw.isdigit():
                mp = CompanyMap.query.get(int(map_id_raw))
                if mp and mp.project_id == project.id:
                    mp.mid_end_enabled = enabled
                    mp.included_splices_meio = mid
                    mp.included_splices_ponta = ponta
                    db.session.commit()
                    flash("Mapa atualizado.", "success")
            return redirect(url_for("settings_project_detail", pid=project.id))

        new_map = (request.form.get("new_map") or "").strip()
        if new_map:
            exists = CompanyMap.query.filter_by(company=project.company, name=new_map, project_id=project.id).first()
            if not exists:
                db.session.add(CompanyMap(company=project.company, name=new_map, project_id=project.id))
                db.session.commit()
                flash("Mapa adicionado.", "success")
        return redirect(url_for("settings_project_detail", pid=project.id))

    types = DeviceType.query.filter_by(company=project.company, project_id=project.id).order_by(DeviceType.name).all()
    tiers = SpliceTier.query.filter_by(company=project.company, project_id=project.id).order_by(SpliceTier.min_splices).all()
    maps = CompanyMap.query.filter_by(company=project.company, project_id=project.id).order_by(CompanyMap.name).all()

    return render_template(
        "settings_project.html",
        project=project,
        types=types,
        tiers=tiers,
        maps=maps,
        company_id=company_id,
    )


@app.route("/settings/device/add", methods=["POST"])
@login_required
def settings_device_add():
    name = (request.form.get("name") or "").strip()
    company = (request.form.get("company") or "").strip() or None
    project_id_raw = (request.form.get("project_id") or "").strip()
    project_id = int(project_id_raw) if project_id_raw.isdigit() else None
    next_url = (request.form.get("next") or "").strip() or None
    value_raw = request.form.get("value_usd") or "0"
    try:
        value = float(value_raw or 0)
    except ValueError:
        value = 0.0

    if not name:
        flash("Nome do dispositivo é obrigatório.", "danger")
        return redirect(next_url or url_for("settings"))

    dt = DeviceType.query.filter_by(name=name, company=company, project_id=project_id).first()
    if dt:
        dt.value_usd = value
    else:
        dt = DeviceType(name=name, company=company, project_id=project_id, value_usd=value)
        db.session.add(dt)
    db.session.commit()
    flash("Dispositivo salvo.", "success")
    return redirect(next_url or url_for("settings"))


@app.route("/settings/device/<int:did>/delete")
@login_required
def settings_device_delete(did: int):
    next_url = request.args.get("next") or None
    dt = DeviceType.query.get_or_404(did)
    db.session.delete(dt)
    db.session.commit()
    flash("Dispositivo removido.", "success")
    return redirect(next_url or url_for("settings"))


@app.route("/settings/tier/add", methods=["POST"])
@login_required
def settings_tier_add():
    company = (request.form.get("company") or "").strip() or None
    project_id_raw = (request.form.get("project_id") or "").strip()
    project_id = int(project_id_raw) if project_id_raw.isdigit() else None
    next_url = (request.form.get("next") or "").strip() or None
    min_raw = request.form.get("min_splices") or "0"
    max_raw = request.form.get("max_splices") or ""
    price_raw = request.form.get("price") or "0"

    try:
        min_s = int(min_raw or 0)
    except ValueError:
        min_s = 0
    max_s = int(max_raw) if max_raw else None
    try:
        price = float(price_raw or 0)
    except ValueError:
        price = 0.0

    if min_s < 0:
        flash("Splices mín. não pode ser negativo.", "danger")
        return redirect(next_url or url_for("settings"))

    tier = SpliceTier(
        company=company,
        project_id=project_id,
        min_splices=min_s,
        max_splices=max_s,
        price_per_splice_usd=price,
    )
    db.session.add(tier)
    db.session.commit()
    flash("Faixa de fusões salva.", "success")
    return redirect(next_url or url_for("settings"))


@app.route("/settings/tier/<int:tid>/delete")
@login_required
def settings_tier_delete(tid: int):
    next_url = request.args.get("next") or None
    tier = SpliceTier.query.get_or_404(tid)
    db.session.delete(tier)
    db.session.commit()
    flash("Faixa de fusões removida.", "success")
    return redirect(next_url or url_for("settings"))




@app.route("/users", methods=["GET", "POST"])
@admin_required
def manage_users():
    """Cadastro simples de usuários. Apenas admin acessa."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        splicer_name = (request.form.get("splicer_name") or "").strip() or None
        company_name = (request.form.get("company_name") or "").strip() or None
        is_company_owner = bool(request.form.get("is_company_owner"))
        is_admin = bool(request.form.get("is_admin"))

        if not username or not password:
            flash("Usuário e senha são obrigatórios.", "danger")
            return redirect(url_for("manage_users"))

        user = User.query.filter_by(username=username).first()
        if user:
            user.password = password
            user.splicer_name = splicer_name
            user.company_name = company_name
            user.is_company_owner = is_company_owner
            user.is_admin = is_admin
        else:
            user = User(
                username=username,
                password=password,
                splicer_name=splicer_name,
                company_name=company_name,
                is_company_owner=is_company_owner,
                is_admin=is_admin,
            )
            db.session.add(user)
        db.session.commit()
        flash("Usuário salvo com sucesso.", "success")
        return redirect(url_for("manage_users"))

    companies = CompanyConfig.query.order_by(CompanyConfig.name).all()
    users = User.query.order_by(User.username).all()
    return render_template("users.html", users=users, companies=companies)


@app.route("/users/<int:uid>/delete")
@admin_required
def user_delete(uid: int):
    user = User.query.get_or_404(uid)
    if user.username == "admin":
        flash("Não é permitido remover o usuário admin.", "danger")
        return redirect(url_for("manage_users"))
    if current_user.id == user.id:
        flash("Você não pode remover o próprio usuário logado.", "danger")
        return redirect(url_for("manage_users"))
    db.session.delete(user)
    db.session.commit()
    flash("Usuário removido.", "success")
    return redirect(url_for("manage_users"))

@app.route("/export/pdf")
@login_required
def export_pdf():
    """Gera um PDF simples com os registros filtrados (mesma lógica da tela principal)."""

    # Flag opcional: se "no_values=1", não mostra colunas de valores em dinheiro.
    no_values = request.args.get("no_values") == "1"

    # Usa exatamente a mesma lógica de filtros/permissões da tela principal
    query = build_filtered_record_query_from_request()
    records = query.order_by(
        Record.created_date.asc().nullslast(),
        Record.id.asc()
    ).all()

    # totais do período
    total_amount = sum((r.total_usd or 0) for r in records)
    total_splices = sum((r.splices or 0) for r in records)
    total_hubs = sum(1 for r in records if (r.type or "").upper() == "HUB")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Relatorio de Producao - SPLICER", ln=1)
    pdf.set_font("Arial", "", 9)

    # linha de totais
    pdf.cell(0, 8, f"Total de splices: {total_splices}", ln=1)
    pdf.cell(0, 8, f"Total de hubs: {total_hubs}", ln=1)
    if not no_values:
        pdf.cell(0, 8, f"Total no período: $ {total_amount:.2f}", ln=1)
    pdf.ln(4)

    # cabeçalho (tabela sem quebrar colunas)
    pdf.set_font("Arial", "B", 8)

    page_w = pdf.w - pdf.l_margin - pdf.r_margin  # largura útil da página

    def _scale_widths(base_widths):
        s = float(sum(base_widths)) or 1.0
        scale = page_w / s
        return [w * scale for w in base_widths]

    def fit_text(text, width):
        """Trunca o texto para caber na célula, sem invadir a coluna seguinte."""
        if text is None:
            text = ""
        text = str(text)
        if width <= 0:
            return ""
        if pdf.get_string_width(text) <= width:
            return text
        ell = "..."
        # garante que o ellipsis cabe
        while pdf.get_string_width(ell) > width and len(ell) > 0:
            ell = ell[:-1]
        if not ell:
            return ""
        # vai cortando até caber com "..."
        cut = text
        while cut and pdf.get_string_width(cut + ell) > width:
            cut = cut[:-1]
        return cut + ell

    if no_values:
        headers = ["Data", "Empresa", "Map", "Type", "Dispositivo", "Splices"]
        # base (soma ~190mm) -> escala para caber na largura útil
        col_widths = _scale_widths([20, 35, 35, 15, 65, 20])
    else:
        headers = ["Data", "Empresa", "Map", "Type", "Dispositivo", "Splices", "Fusoes $", "Total $"]
        col_widths = _scale_widths([20, 30, 30, 15, 40, 15, 20, 20])

    # cabeçalho
    for w, h in zip(col_widths, headers):
        pdf.cell(w, 7, h, border=1)
    pdf.ln()

    pdf.set_font("Arial", "", 8)

    for r in records:
        if no_values:
            row = [
                r.created_date.strftime("%Y-%m-%d") if r.created_date else "",
                r.company or "",
                r.map or "",
                r.type or "",
                r.device or "",
                str(r.splices or 0),
            ]
        else:
            row = [
                r.created_date.strftime("%Y-%m-%d") if r.created_date else "",
                r.company or "",
                r.map or "",
                r.type or "",
                r.device or "",
                str(r.splices or 0),
                f"{(r.price_splices_usd or 0):.2f}",
                f"{(r.total_usd or 0):.2f}",
            ]

        for w, val in zip(col_widths, row):
            pdf.cell(w, 6, fit_text(val, w - 1), border=1)
        pdf.ln()

    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)
    filename = "relatorio_producao.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")




@app.route("/invoices")
@admin_required
def invoices_list():
    """Lista simples de todas as invoices para controle contábil."""
    status_filter = request.args.get("status") or None
    query = Invoice.query.order_by(Invoice.created_at.desc())
    if status_filter in ("pending", "paid"):
        query = query.filter(Invoice.status == status_filter)
    invoices = query.all()
    return render_template("invoices.html", invoices=invoices, status_filter=status_filter)

@app.route("/invoice/<int:iid>/pdf")
@login_required
def invoice_pdf(iid: int):
    """Exibe/baixa o PDF salvo de uma invoice já criada."""
    inv = Invoice.query.get_or_404(iid)
    # permissões: admin vê tudo; (futuro) dono da empresa pode ver da própria empresa
    if not getattr(current_user, 'is_admin', False):
        if not (getattr(current_user, 'is_company_owner', False) and (inv.company == (current_user.company_name or ''))):
            abort(403)
    if not inv.pdf_data:
        flash('Esta invoice não tem PDF salvo. Gere novamente.', 'warning')
        return redirect(url_for('invoices_list'))
    download = (request.args.get('download') or '').lower() in ('1','true','yes')
    return send_file(BytesIO(inv.pdf_data), as_attachment=download, download_name=inv.pdf_filename or f"{inv.number}.pdf", mimetype=(inv.pdf_content_type or 'application/pdf'))


@app.route("/invoice/<int:iid>/toggle", methods=["POST"])
@admin_required
def invoice_toggle_status(iid: int):
    inv = Invoice.query.get_or_404(iid)
    inv.status = "paid" if inv.status != "paid" else "pending"
    db.session.commit()
    flash("Invoice status updated.", "success")
    return redirect(url_for("invoices_list"))

@app.route("/invoice/<int:iid>/delete", methods=["POST"])
@admin_required
def invoice_delete(iid: int):
    inv = Invoice.query.get_or_404(iid)
    db.session.delete(inv)
    db.session.commit()
    flash("Invoice deleted.", "success")
    return redirect(url_for("invoices_list"))
@app.route("/export/invoice")
@login_required
def export_invoice():
    """Gera uma invoice (nota de cobrança) em PDF para o intervalo de datas e filtros informados.

    A invoice contém: nome do mapa, número do dispositivo, número de fusões,
    valor do dispositivo e total, somados por mapa/dispositivo no período.
    """
    # mesmos filtros do index / export_pdf
    company_filter = request.args.get("company") or None
    splicer_filter = request.args.get("splicer") or None
    map_filter = request.args.get("map") or None
    device_filter = request.args.get("device") or None
    start_raw = request.args.get("start") or None
    end_raw = request.args.get("end") or None
    no_values = False  # sempre com valores na invoice

    # invoice só pode ser gerada para UMA empresa específica
    if not company_filter:
        flash("Para gerar invoice, selecione uma empresa específica (filtro de empresa).", "danger")
        return redirect(url_for("index"))

    query = Record.query

    if company_filter:
        query = query.filter(Record.company == company_filter)
    if splicer_filter and getattr(current_user, "is_admin", False):
        query = query.filter(Record.splicer == splicer_filter)
    if map_filter:
        query = query.filter(Record.map.ilike(f"%{map_filter}%"))
    if device_filter:
        query = query.filter(Record.device.ilike(f"%{device_filter}%"))

    if start_raw:
        try:
            start_dt = datetime.fromisoformat(start_raw)
            query = query.filter(Record.created_date >= start_dt)
        except ValueError:
            start_dt = None
    else:
        start_dt = None

    if end_raw:
        try:
            end_dt = datetime.fromisoformat(end_raw)
            query = query.filter(Record.created_date <= end_dt)
        except ValueError:
            end_dt = None
    else:
        end_dt = None

    from datetime import datetime as _dt
    inv_date = _dt.utcnow().date().isoformat()
    inv_number = _dt.utcnow().strftime("INV-%Y%m%d-%H%M%S")

    # se não for admin, força o filtro para o próprio splicer
    if not getattr(current_user, "is_admin", False):
        enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
        query = query.filter(Record.splicer == enforced_splicer)

    records = query.order_by(Record.created_date.asc().nullslast(), Record.id.asc()).all()

    # agrupar por mapa + dispositivo (+ opcional: MEIO/PONTA + inclusas aplicadas)
    grouped = {}
    for r in records:
        key = (
            (r.map or "").strip(),
            (r.device or "").strip(),
            (r.map_role or "").strip(),
            int(r.included_splices_applied) if r.included_splices_applied is not None else None,
        )
        if key not in grouped:
            grouped[key] = {
                "map": key[0] or "-",
                "device": key[1] or "-",
                "map_role": key[2] or "",
                "included": key[3],
                "splices": 0,
                "price_device_usd": float(r.price_device_usd or 0.0),
                "total_usd": 0.0,
            }
        grouped[key]["splices"] += int(r.splices or 0)
        grouped[key]["total_usd"] += float(r.total_usd or 0.0)
        # se o preço do dispositivo vier zero mas houver total,
        # tenta inferir um valor médio por dispositivo
        if grouped[key]["price_device_usd"] == 0.0 and (r.total_usd or 0) and (r.splices or 0):
            grouped[key]["price_device_usd"] = float(r.total_usd or 0.0) / float(r.splices or 1)

    lines = list(grouped.values())
    lines.sort(key=lambda x: (x["map"], x["device"]))

    total_invoice = sum(l["total_usd"] for l in lines)

    # persist invoice for accounting (PDF será salvo no banco para visualizar depois)
    inv_start_date = start_dt.date() if start_dt else None
    inv_end_date = end_dt.date() if end_dt else None
    inv_rec = Invoice(
        number=inv_number,
        company=company_filter or "",
        start_date=inv_start_date,
        end_date=inv_end_date,
        total_usd=float(total_invoice or 0.0),
        created_by=getattr(current_user, 'id', None),
    )
    db.session.add(inv_rec)
    # montar PDF da invoice
    # buscar dados da sua empresa (emitente)
    syscfg = SystemConfig.query.first()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # header - your company (FROM)
    pdf.set_font("Arial", "B", 12)
    if syscfg and syscfg.my_company_name:
        pdf.cell(0, 6, syscfg.my_company_name, ln=1)
    if syscfg and syscfg.my_company_address:
        for line in (syscfg.my_company_address or "").splitlines():
            if line.strip():
                pdf.set_font("Arial", "", 9)
                pdf.cell(0, 5, line.strip(), ln=1)
    if syscfg and (syscfg.my_company_email or syscfg.my_company_phone):
        contact_parts = []
        if syscfg.my_company_email:
            contact_parts.append(syscfg.my_company_email)
        if syscfg.my_company_phone:
            contact_parts.append(syscfg.my_company_phone)
        pdf.cell(0, 5, " | ".join(contact_parts), ln=1)
    pdf.ln(4)

    # invoice title and metadata
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 8, "INVOICE", ln=1)

    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 6, f"Invoice date: {inv_date}", ln=1)
    pdf.cell(0, 6, f"Invoice #: {inv_number}", ln=1)
    pdf.ln(4)

    # BILL TO (client)
    cfg_cli = CompanyConfig.query.filter_by(name=company_filter).first()
    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 6, "BILL TO:", ln=1)
    pdf.set_font("Arial", "", 9)
    if cfg_cli:
        if cfg_cli.invoice_address:
            for line in (cfg_cli.invoice_address or "").splitlines():
                if line.strip():
                    pdf.cell(0, 5, line.strip(), ln=1)
        else:
            pdf.cell(0, 5, cfg_cli.name, ln=1)
    else:
        pdf.cell(0, 5, company_filter or "", ln=1)

    pdf.ln(4)

    # table header
    # OBS: FPDF "cell" não faz quebra de linha. Para não "estourar" a tabela e
    # mostrar o nome COMPLETO do device dentro da coluna, vamos desenhar a linha
    # com multi_cell e quebra manual (principalmente em '_' e '-').
    col_widths = [40, 70, 12, 12, 16, 20, 20]
    headers = ["Map", "Device", "Tipo", "Incl.", "Splices", "Device price", "Total"]

    pdf.set_font("Arial", "B", 10)
    for w, h in zip(col_widths, headers):
        pdf.cell(w, 7, h, border=1)
    pdf.ln()

    def _break_for_table(txt: str) -> str:
        """Ajuda o FPDF a quebrar nomes longos SEM picotar.

        O FPDF quebra linha em espaços. Como o device costuma vir com '_' e '-',
        inserimos um espaço APÓS esses separadores (ex: 'A_B' vira 'A_ B') para
        permitir quebra somente quando precisar, mantendo o nome completo dentro
        da coluna.
        """
        s = (txt or "").strip()
        if not s:
            return "-"
        s = s.replace("_", "_ ")
        s = s.replace("-", "- ")
        return s

    line_h = 6
    pdf.set_font("Arial", "", 9)
    for l in lines:
        row = [
            str(l["map"] or "-"),
            _break_for_table(str(l["device"] or "-")),
            str(l.get("map_role") or ""),
            str(l.get("included") if l.get("included") is not None else ""),
            str(l["splices"]),
            f"$ {l['price_device_usd']:.2f}",
            f"$ {l['total_usd']:.2f}",
        ]

        # calcula altura da linha pela célula que tiver mais quebras
        n_lines = max((str(v).count("\n") + 1) for v in row)
        row_h = line_h * n_lines

        x0 = pdf.get_x()
        y0 = pdf.get_y()

        # desenha cada célula como multi_cell mantendo a mesma altura de linha
        x = x0
        for w, val in zip(col_widths, row):
            pdf.set_xy(x, y0)
            pdf.multi_cell(w, line_h, str(val), border=1)
            x += w

        # vai para a próxima linha
        pdf.set_xy(x0, y0 + row_h)

    pdf.ln(4)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(0, 8, f"Invoice total: $ {total_invoice:.2f}", ln=1)

    # gerar bytes do PDF e salvar na Invoice para poder visualizar depois
    try:
        pdf_bytes = pdf.output(dest='S').encode('latin-1')
    except Exception:
        buf = BytesIO()
        pdf.output(buf)
        pdf_bytes = buf.getvalue()

    inv_rec.pdf_filename = f"{inv_number}.pdf"
    inv_rec.pdf_content_type = "application/pdf"
    inv_rec.pdf_data = pdf_bytes
    db.session.commit()

    download = (request.args.get('download') or '').lower() in ('1', 'true', 'yes')
    return send_file(
        BytesIO(pdf_bytes),
        as_attachment=download,
        download_name=inv_rec.pdf_filename or "invoice.pdf",
        mimetype="application/pdf",
    )





@app.route("/export/excel")
@login_required
def export_excel():
    """Exporta os dados de produção em formato Excel (CSV) por empresa e período.

    O arquivo contém: nome do mapa, nome do dispositivo e número de fusões,
    já somados por mapa/dispositivo dentro do filtro.
    """
    company_filter = request.args.get("company") or None
    splicer_filter = request.args.get("splicer") or None
    map_filter = request.args.get("map") or None
    device_filter = request.args.get("device") or None
    start_raw = request.args.get("start") or None
    end_raw = request.args.get("end") or None

    if not company_filter:
        flash("To export Excel, select a company in the filter.", "danger")
        return redirect(url_for("index"))

    query = Record.query

    if company_filter:
        query = query.filter(Record.company == company_filter)
    if splicer_filter:
        query = query.filter(Record.splicer == splicer_filter)
    if map_filter:
        query = query.filter(Record.map == map_filter)

    start_dt = None
    end_dt = None
    if start_raw:
        try:
            start_dt = datetime.fromisoformat(start_raw)
            query = query.filter(Record.created_date >= start_dt)
        except ValueError:
            start_dt = None
    if end_raw:
        try:
            end_dt = datetime.fromisoformat(end_raw)
            query = query.filter(Record.created_date <= end_dt)
        except ValueError:
            end_dt = None

    # se não for admin, força o filtro para o próprio splicer
    if not getattr(current_user, "is_admin", False):
        enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
        query = query.filter(Record.splicer == enforced_splicer)

    records = query.order_by(Record.created_date.asc().nullslast(), Record.id.asc()).all()

    if not records:
        flash("No records found for this filter.", "warning")
        return redirect(url_for("index"))

    # agrupar por mapa + dispositivo + data
    grouped = {}
    devices_unique = set()
    for r in records:
        map_name = (r.map or "").strip()
        device_name = (r.device or "").strip()
        date_value = r.created_date.date().isoformat() if r.created_date else None

        key = (map_name, device_name, date_value)
        if key not in grouped:
            grouped[key] = {
                "map": map_name or "-",
                "device": device_name or "-",
                "date": date_value or "",
                "splices": 0,
            }
        grouped[key]["splices"] += int(r.splices or 0)

        if device_name:
            devices_unique.add(device_name)

    lines = list(grouped.values())
    lines.sort(key=lambda x: (x["date"], x["map"], x["device"]))

    # gerar planilha Excel (XLSX) em memória
    wb = Workbook()
    ws = wb.active
    ws.title = "Production"

    # cabeçalho (Date | Map | Device | Splices)
    headers = ["Date", "Map", "Device", "Splices"]
    ws.append(headers)

    # linhas de dados
    total_splices = 0
    for line in lines:
        ws.append([line["date"], line["map"], line["device"], line["splices"]])
        total_splices += int(line["splices"] or 0)

    # linhas de totais
    total_devices = len(devices_unique)
    ws.append([])
    total_row_devices = ws.max_row + 1
    ws.cell(row=total_row_devices, column=1, value="TOTAL DEVICES")
    ws.cell(row=total_row_devices, column=2, value=total_devices)

    total_row_splices = total_row_devices + 1
    ws.cell(row=total_row_splices, column=1, value="TOTAL SPLICES")
    ws.cell(row=total_row_splices, column=4, value=total_splices)

    # aplicar estilos (negrito cabeçalho e totais, bordas)
    thin = Side(border_style="thin", color="000000")
    border = Border(top=thin, left=thin, right=thin, bottom=thin)

    # cabeçalho em negrito
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.border = border

    # dados + bordas
    last_row = total_row_splices
    for row in range(2, last_row + 1):
        for col in range(1, 5):
            cell = ws.cell(row=row, column=col)
            # destacar totais
            if row in (total_row_devices, total_row_splices):
                cell.font = Font(bold=True)
            cell.border = border

    # autoajustar largura das colunas
    for col in range(1, 5):
        max_len = 0
        col_letter = ws.cell(row=1, column=col).column_letter
        for row in range(1, last_row + 1):
            val = ws.cell(row=row, column=col).value
            if val is None:
                continue
            max_len = max(max_len, len(str(val)))
        ws.column_dimensions[col_letter].width = max_len + 2

    buf = BytesIO()
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"splicer_{company_filter or 'all'}_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
@app.route("/record/<int:rid>/delete")
@login_required
def record_delete(rid: int):
    rec = Record.query.get_or_404(rid)

    # Apenas admin pode apagar qualquer registro.
    # Usuário comum só pode apagar o próprio lançamento.
    if not getattr(current_user, "is_admin", False):
        enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
        if rec.splicer != enforced_splicer:
            abort(403)

    # Apaga primeiro as fotos vinculadas a esse lançamento.
    # Isso evita erro de integridade no PostgreSQL, pois a FK de record_photo
    # não aceita valor NULL em record_id.
    RecordPhoto.query.filter_by(record_id=rec.id).delete(synchronize_session=False)

    db.session.delete(rec)
    db.session.commit()
    flash("Registro removido.", "success")
    return redirect(url_for("index"))



@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    is_admin = getattr(current_user, "is_admin", False)
    is_owner = getattr(current_user, "is_company_owner", False)

    # Dono de empresa não tem acesso ao módulo de despesas (mantém sistema como estava)
    if is_owner and not is_admin:
        flash("Você não tem acesso a este módulo.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        description = (request.form.get("description") or "").strip()
        category = (request.form.get("category") or "").strip()
        amount_raw = (request.form.get("amount") or "").replace(",", ".")
        date_raw = request.form.get("date") or ""

        if not description:
            flash("Descrição é obrigatória.", "danger")
            return redirect(url_for("expenses"))

        try:
            amount = float(amount_raw)
        except ValueError:
            flash("Valor inválido para despesa.", "danger")
            return redirect(url_for("expenses"))

        if amount <= 0:
            flash("O valor da despesa deve ser maior que zero.", "danger")
            return redirect(url_for("expenses"))

        try:
            if date_raw:
                y, m, d = map(int, date_raw.split("-"))
                d = date(y, m, d)
            else:
                d = date.today()
        except Exception:
            d = date.today()

        exp = Expense(
            user_id=current_user.id,
            description=description,
            category=category or None,
            amount=amount,
            date=d,
        )
        db.session.add(exp)
        db.session.commit()
        flash("Despesa lançada com sucesso.", "success")
        return redirect(url_for("expenses"))

    user_filter = request.args.get("user_id") or None
    start_raw = request.args.get("start") or None
    end_raw = request.args.get("end") or None

    q = Expense.query

    show_paid = (request.args.get("show_paid") == "1")

    # por padrão mostra só despesas em aberto; com show_paid=1 mostra pagas
    if show_paid:
        q = q.filter(Expense.paid == True)
    else:
        q = q.filter(Expense.paid == False)

    if is_admin:
        if user_filter:
            try:
                q = q.filter(Expense.user_id == int(user_filter))
            except ValueError:
                pass
    else:
        q = q.filter(Expense.user_id == current_user.id)

    if start_raw:
        try:
            y, m, d = map(int, start_raw.split("-"))
            q = q.filter(Expense.date >= date(y, m, d))
        except Exception:
            pass

    if end_raw:
        try:
            y, m, d = map(int, end_raw.split("-"))
            q = q.filter(Expense.date <= date(y, m, d))
        except Exception:
            pass

    q = q.order_by(Expense.date.desc(), Expense.id.desc())
    expenses_list = q.all()
    total_amount = sum(e.amount for e in expenses_list)

    per_user = []
    users_for_filter = []

    if is_admin:
        agg_q = (
            db.session.query(
                User.id,
                User.username,
                User.splicer_name,
                func.sum(Expense.amount).label("total")
            )
            .join(Expense, Expense.user_id == User.id)
        )

        if show_paid:
            agg_q = agg_q.filter(Expense.paid == True)
        else:
            agg_q = agg_q.filter(Expense.paid == False)


        if start_raw:
            try:
                y, m, d = map(int, start_raw.split("-"))
                agg_q = agg_q.filter(Expense.date >= date(y, m, d))
            except Exception:
                pass

        if end_raw:
            try:
                y, m, d = map(int, end_raw.split("-"))
                agg_q = agg_q.filter(Expense.date <= date(y, m, d))
            except Exception:
                pass

        agg_q = agg_q.group_by(User.id, User.username, User.splicer_name)
        per_user = agg_q.all()

        users_for_filter = User.query.order_by(User.username).all()

    return render_template(
        "expenses.html",
        expenses=expenses_list,
        total_amount=total_amount,
        per_user=per_user,
        users_for_filter=users_for_filter,
        user_filter=user_filter,
        start_raw=start_raw,
        end_raw=end_raw,
        show_paid=show_paid,
    )


@app.route("/expenses/mark_paid", methods=["POST"])
@login_required
def expenses_mark_paid():
    is_admin = getattr(current_user, "is_admin", False)
    if not is_admin:
        flash("Acesso negado.", "danger")
        return redirect(url_for("expenses"))

    user_filter = request.form.get("user_id") or None
    start_raw = request.form.get("start") or None
    end_raw = request.form.get("end") or None

    q = Expense.query.filter(Expense.paid == False)

    if user_filter:
        try:
            q = q.filter(Expense.user_id == int(user_filter))
        except ValueError:
            pass

    if start_raw:
        try:
            y, m, d = map(int, start_raw.split("-"))
            q = q.filter(Expense.date >= date(y, m, d))
        except Exception:
            pass

    if end_raw:
        try:
            y, m, d = map(int, end_raw.split("-"))
            q = q.filter(Expense.date <= date(y, m, d))
        except Exception:
            pass

    try:
        updated = q.update(
            {
                Expense.paid: True,
                Expense.paid_at: datetime.utcnow(),
                Expense.paid_by: current_user.id,
            },
            synchronize_session=False,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        updated = 0

    flash(f"{updated} despesas marcadas como pagas.", "success")
    return redirect(url_for("expenses", user_id=user_filter or "", start=start_raw or "", end=end_raw or ""))



@app.route("/expenses/delete/<int:expense_id>", methods=["POST"])
@login_required
def expenses_delete(expense_id):
    is_admin = getattr(current_user, "is_admin", False)
    is_owner = getattr(current_user, "is_company_owner", False)

    # Dono de empresa não tem acesso ao módulo de despesas (mantém sistema como estava)
    if is_owner and not is_admin:
        flash("Você não tem acesso a este módulo.", "danger")
        return redirect(url_for("index"))

    exp = Expense.query.get_or_404(expense_id)

    # Splicer só pode excluir as próprias despesas
    if not is_admin and exp.user_id != current_user.id:
        flash("Você não pode excluir despesas de outro usuário.", "danger")
        return redirect(url_for("expenses"))

    try:
        db.session.delete(exp)
        db.session.commit()
        flash("Despesa excluída com sucesso.", "success")
    except Exception:
        db.session.rollback()
        flash("Erro ao excluir despesa.", "danger")

    return redirect(request.referrer or url_for("expenses"))

if __name__ == "__main__":
    app.run(debug=True)



@app.route('/__version')
def __version__():
    return 'FULL-FIX-503 2026-02-12'