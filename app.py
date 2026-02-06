from flask import Flask, render_template, request, redirect, url_for, flash, send_file, abort, has_app_context
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime, date
from sqlalchemy import text, case, or_, inspect
import os
from fpdf import FPDF
from io import BytesIO
from zipfile import ZipFile
from functools import wraps
import csv
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side
from PIL import Image, ImageOps
import uuid

# --------- App & DB setup ---------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-key")

# Database configuration: prefer DATABASE_URL/RENDER_DATABASE_URL (e.g. Render PostgreSQL),
# fallback to local SQLite for development.
db_url = os.environ.get("DATABASE_URL") or os.environ.get("RENDER_DATABASE_URL") or "sqlite:///data.db"
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")

db = SQLAlchemy(app)


# ---- Força atualização do schema no SQLite (garante colunas novas no banco antigo) ----
try:
    import sqlite3
    from sqlalchemy.engine.url import make_url

    def _force_sqlite_user_columns():
        url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
        if not str(url.drivername).startswith("sqlite"):
            return

        db_path = url.database
        if not db_path:
            return

        # Se o caminho for relativo, resolve a partir da raiz do app
        if not os.path.isabs(db_path):
            db_path = os.path.join(app.root_path, db_path)

        if not os.path.exists(db_path):
            # Se ainda não existir, o create_all vai criar com o schema correto
            return

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        def ensure_column(table, column, coltype):
            cur.execute(f"PRAGMA table_info('{table}')")
            cols = [row[1] for row in cur.fetchall()]
            if column in cols:
                return
            cur.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {coltype};')

        # Garante as colunas novas da tabela user
        ensure_column("user", "is_admin", "INTEGER DEFAULT 0")
        ensure_column("user", "splicer_name", "VARCHAR(120)")
        ensure_column("user", "is_company_owner", "INTEGER DEFAULT 0")
        ensure_column("user", "company_name", "VARCHAR(120)")

        conn.commit()
        conn.close()

    def _force_postgres_user_columns():
        """Garante as colunas novas na tabela user quando usando PostgreSQL (Render)."""
        url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
        if not str(url.drivername).startswith("postgresql"):
            return

        try:
            existing_cols = [c["name"] for c in inspect(db.engine).get_columns("user")]
        except Exception:
            # Se não conseguir inspecionar, não derruba o app
            return

        stmts = []
        if "is_admin" not in existing_cols:
            stmts.append('ALTER TABLE "user" ADD COLUMN is_admin boolean DEFAULT false;')
        if "splicer_name" not in existing_cols:
            stmts.append('ALTER TABLE "user" ADD COLUMN splicer_name varchar(120);')
        if "is_company_owner" not in existing_cols:
            stmts.append('ALTER TABLE "user" ADD COLUMN is_company_owner boolean DEFAULT false;')
        if "company_name" not in existing_cols:
            stmts.append('ALTER TABLE "user" ADD COLUMN company_name varchar(120);')

        if not stmts:
            return

        conn = db.engine.connect()
        for s in stmts:
            try:
                conn.execute(text(s))
            except Exception:
                # ignora erros individuais para não derrubar o app
                pass
        conn.close()

    # Cria tabelas e aplica migração assim que o app sobe
    with app.app_context():
        # cria tabelas, se não existirem
        db.create_all()
        # força migração no arquivo sqlite
        _force_sqlite_user_columns()
        # força migração no PostgreSQL (Render)
        _force_postgres_user_columns()
except Exception as _e:
    # Não derruba o app se algo der errado aqui; o erro aparece no log apenas
    print("WARN: erro ao forçar migração SQLite:", _e)


login_manager = LoginManager(app)
login_manager.login_view = "login"

# --------- Models ---------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)  # simples, sem hash, uso local
    is_admin = db.Column(db.Boolean, default=False)
    splicer_name = db.Column(db.String(120), nullable=True)  # nome que aparece como Splicer nos lançamentos
    is_company_owner = db.Column(db.Boolean, default=False)  # dono de empresa: vê todos os lançamentos da própria empresa
    company_name = db.Column(db.String(120), nullable=True)  # nome exato da empresa (igual ao campo company nos lançamentos)


class CompanyConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    included_splices = db.Column(db.Integer, default=1, nullable=False)  # fusões inclusas por lançamento
    invoice_address = db.Column(db.Text, nullable=True)  # nome + endereço p/ usar na invoice


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

class DeviceType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    value_usd = db.Column(db.Float, default=0.0, nullable=False)
    company = db.Column(db.String(120), nullable=True)  # se None = valor padrão

class SpliceTier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    min_splices = db.Column(db.Integer, nullable=False)
    max_splices = db.Column(db.Integer, nullable=True)
    price_per_splice_usd = db.Column(db.Float, default=0.0, nullable=False)
    company = db.Column(db.String(120), nullable=True)


class CompanyMap(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company = db.Column(db.String(120), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)


class Record(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    map = db.Column(db.String(200))
    type = db.Column(db.String(120))
    splices = db.Column(db.Integer)
    device = db.Column(db.String(120))
    splicer = db.Column(db.String(120))
    created_date = db.Column(db.DateTime, nullable=True)
    company = db.Column(db.String(120), nullable=True)
    price_splices_usd = db.Column(db.Float, default=0.0)
    price_device_usd = db.Column(db.Float, default=0.0)
    total_usd = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class RecordPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(db.Integer, db.ForeignKey("record.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    record = db.relationship("Record", backref=db.backref("photos", lazy="dynamic"))




# --------- User loader ---------
@login_manager.user_loader
def load_user(user_id: str):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None

# --------- DB init & migrations simples ---------
# --------- DB init & migrações simples ---------
_migrations_done = False

def run_simple_migrations():
    """Cria tabelas e garante colunas novas mesmo em banco antigo."""
    global _migrations_done
    if _migrations_done:
        return

    def _do_migrations():
        db.create_all()

        # garante usuário padrão
        if not User.query.filter_by(username="admin").first():
            db.session.add(User(username="admin", password="admin", is_admin=True, splicer_name="ADMIN"))
            db.session.commit()

        # migração simples de colunas (funciona tanto em SQLite quanto em Postgres)
        def ensure(table, col, typ):
            """Garante que uma coluna exista na tabela informada."""
            conn = db.engine.connect()
            try:
                try:
                    # SQLite
                    result = conn.execute(text(f"PRAGMA table_info('{table}')"))
                    existing = [row[1] for row in result]
                except Exception:
                    # fallback usando o inspector do SQLAlchemy
                    from sqlalchemy import inspect as _inspect
                    inspector = _inspect(db.engine)
                    existing = [c["name"] for c in inspector.get_columns(table)]
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            if col not in existing:
                db.session.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {col} {typ}'))
                db.session.commit()

        ensure("record", "company", "VARCHAR(120)")
        ensure("device_type", "company", "VARCHAR(120)")
        ensure("splice_tier", "company", "VARCHAR(120)")
        ensure("company_config", "invoice_address", "TEXT")
        ensure("user", "is_admin", "BOOLEAN")
        ensure("user", "splicer_name", "VARCHAR(120)")
        ensure("user", "is_company_owner", "BOOLEAN")
        ensure("user", "company_name", "VARCHAR(120)")

        _migrations_done = True

    if has_app_context():
        _do_migrations()
    else:
        with app.app_context():
            _do_migrations()

# roda na importação
run_simple_migrations()

@app.before_request
def _ensure_migrations_before_request():
    run_simple_migrations()
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


# --------- Helpers de imagens ---------
def _allowed_image(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in {"jpg", "jpeg", "png", "webp"}


def save_record_photos(record, files):
    """
    Salva fotos redimensionadas/comprimidas para um lançamento.
    - Reduz para no máximo 1280x1280
    - Salva como JPEG qualidade 70
    - Grava apenas o nome do arquivo no banco (tabela RecordPhoto)
    """
    if not files:
        return

    upload_folder = app.config.get("UPLOAD_FOLDER") or os.path.join(app.root_path, "static", "uploads")
    os.makedirs(upload_folder, exist_ok=True)

    max_photos = 5
    for idx, file in enumerate(files[:max_photos]):
        if not file or not getattr(file, "filename", None):
            continue
        if not _allowed_image(file.filename):
            continue

        try:
            img = Image.open(file.stream)
        except Exception:
            continue

        # Corrige orientação com base no EXIF (para não "deitar" a foto)
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        img = img.convert("RGB")
        img.thumbnail((1280, 1280))

        filename = f"{record.id}_{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(upload_folder, filename)

        try:
            img.save(filepath, format="JPEG", quality=70, optimize=True)
        except Exception:
            continue

        photo = RecordPhoto(record_id=record.id, filename=filename)
        db.session.add(photo)

    db.session.commit()


# --------- Helpers de preço ---------
def included_splices_for(company: str | None) -> int:
    """Quantas fusões são inclusas para essa empresa."""
    if not company:
        return 1  # padrão antigo: 1 fusão inclusa
    cfg = CompanyConfig.query.filter_by(name=company).first()
    if cfg:
        return int(cfg.included_splices or 0)
    return 1

def device_value_for(name: str, company: str | None) -> float:
    if not name:
        return 0.0
    q = DeviceType.query.filter(DeviceType.name.ilike(name))
    if company:
        q = q.filter(or_(DeviceType.company == company, DeviceType.company.is_(None)))
        dt = q.order_by(case((DeviceType.company == company, 0), else_=1)).first()
    else:
        dt = q.first()
    return float(dt.value_usd) if dt else 0.0

def tier_price_for(count: int, company: str | None) -> float:
    from sqlalchemy import or_ as _or, case as _case
    q = SpliceTier.query.filter(SpliceTier.min_splices <= count)
    if company:
        q = q.filter(_or(SpliceTier.company == company, SpliceTier.company.is_(None)))
        q = q.filter(_or(SpliceTier.max_splices == None, SpliceTier.max_splices >= count))
        tier = q.order_by(
            _case((SpliceTier.company == company, 0), else_=1),
            SpliceTier.min_splices.desc()
        ).first()
    else:
        q = q.filter(_or(SpliceTier.max_splices == None, SpliceTier.max_splices >= count))
        tier = q.order_by(SpliceTier.min_splices.desc()).first()
    return float(tier.price_per_splice_usd) if tier else 0.0


def compute_prices(splices: int, device_name: str, company: str | None):
    """Calcula preço de fusões e dispositivo para um lançamento manual."""
    included = included_splices_for(company)
    charge = max(int(splices or 0) - included, 0)
    price_splices = charge * tier_price_for(charge, company)
    price_device = device_value_for(device_name or "", company)
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

    # filtros
    company_filter = request.args.get("company") or None
    splicer_filter = request.args.get("splicer") or None
    map_filter = request.args.get("map") or None
    device_filter = request.args.get("device") or None
    start_raw = request.args.get("start") or None
    end_raw = request.args.get("end") or None

    # base da consulta
    query = Record.query

    # filtros principais
    if company_filter:
        query = query.filter(Record.company == company_filter)
    if splicer_filter and getattr(current_user, "is_admin", False):
        # só admin pode aplicar filtro por splicer diferente
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
            pass
    if end_raw:
        try:
            end_dt = datetime.fromisoformat(end_raw)
            query = query.filter(Record.created_date <= end_dt)
        except ValueError:
            pass

    # se não for admin, aplica regra de visibilidade:
    # - dono de empresa: vê somente registros da própria empresa (todos os splicers)
    # - usuário normal: vê apenas seus próprios lançamentos (campo Splicer)
    enforced_splicer = None
    if not getattr(current_user, "is_admin", False):
        if getattr(current_user, "is_company_owner", False) and getattr(current_user, "company_name", None):
            query = query.filter(Record.company == current_user.company_name)
        else:
            enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
            query = query.filter(Record.splicer == enforced_splicer)

    records = query.order_by(Record.created_date.desc().nullslast(), Record.id.desc()).all()
    total_rows = len(records)
    total_amount = sum(r.total_usd or 0 for r in records)

    companies = [c.name for c in CompanyConfig.query.order_by(CompanyConfig.name).all()]
    # também empresas já usadas em registros
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

    # para usuários comuns, o dropdown não deve listar outros nomes
    if not getattr(current_user, "is_admin", False):
        if enforced_splicer:
            all_splicers = [enforced_splicer]
            splicer_filter = enforced_splicer
        else:
            enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
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



@app.route("/record/<int:rid>/photos_zip")
@login_required
def record_photos_zip(rid):
    """Download de todas as fotos de um único lançamento em um .zip."""
    record = Record.query.get_or_404(rid)

    # regras de acesso: não-admin só acessa o que veria na tela principal
    if not getattr(current_user, "is_admin", False):
        if getattr(current_user, "is_company_owner", False) and getattr(current_user, "company_name", None):
            if record.company != current_user.company_name:
                abort(403)
        else:
            enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
            if record.splicer != enforced_splicer:
                abort(403)

    photos = record.photos.order_by(RecordPhoto.created_at).all()
    if not photos:
        flash("Este lançamento não possui fotos para download.", "warning")
        return redirect(url_for("index"))

    upload_folder = app.config.get("UPLOAD_FOLDER") or os.path.join(app.root_path, "static", "uploads")

    mem = BytesIO()
    with ZipFile(mem, "w") as zf:
        for photo in photos:
            filepath = os.path.join(upload_folder, photo.filename)
            if not os.path.exists(filepath):
                continue
            arcname = f"record_{record.id}/{photo.filename}"
            zf.write(filepath, arcname=arcname)

    mem.seek(0)
    filename = f"record_{record.id}_fotos.zip"
    return send_file(
        mem,
        as_attachment=True,
        download_name=filename,
        mimetype="application/zip",
    )


@app.route("/photos/filtered_zip")
@login_required
def photos_filtered_zip():
    """Download .zip com fotos de todos os lançamentos que batem com os filtros atuais."""
    company_filter = request.args.get("company") or None
    splicer_filter = request.args.get("splicer") or None
    map_filter = request.args.get("map") or None
    device_filter = request.args.get("device") or None
    start_raw = request.args.get("start") or None
    end_raw = request.args.get("end") or None

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
            pass

    if end_raw:
        try:
            end_dt = datetime.fromisoformat(end_raw)
            query = query.filter(Record.created_date <= end_dt)
        except ValueError:
            pass

    # regras de visibilidade para não-admin
    enforced_splicer = None
    if not getattr(current_user, "is_admin", False):
        if getattr(current_user, "is_company_owner", False) and getattr(current_user, "company_name", None):
            query = query.filter(Record.company == current_user.company_name)
        else:
            enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
            query = query.filter(Record.splicer == enforced_splicer)

    records = query.order_by(Record.created_date.desc().nullslast(), Record.id.desc()).all()

    all_photos = []
    for record in records:
        for photo in record.photos.order_by(RecordPhoto.created_at).all():
            all_photos.append((record, photo))

    if not all_photos:
        flash("Nenhuma foto encontrada para os filtros atuais.", "warning")
        return redirect(
            url_for(
                "index",
                company=company_filter or "",
                splicer=splicer_filter or "",
                map=map_filter or "",
                device=device_filter or "",
                start=start_raw or "",
                end=end_raw or "",
            )
        )

    upload_folder = app.config.get("UPLOAD_FOLDER") or os.path.join(app.root_path, "static", "uploads")

    mem = BytesIO()
    name_counters = {}
    with ZipFile(mem, "w") as zf:
        for record, photo in all_photos:
            filepath = os.path.join(upload_folder, photo.filename)
            if not os.path.exists(filepath):
                continue
            # nome base do arquivo = nome do device, para facilitar identificação
            device_slug = (record.device or "device").strip().replace(" ", "_")
            base, ext = os.path.splitext(photo.filename)
            key = (record.id, device_slug)
            idx = name_counters.get(key, 0) + 1
            name_counters[key] = idx
            arcname = f"{device_slug}_{idx}{ext}"
            zf.write(filepath, arcname=arcname)

    mem.seek(0)
    filename = "fotos_filtradas.zip"
    return send_file(
        mem,
        as_attachment=True,
        download_name=filename,
        mimetype="application/zip",
    )


@app.route("/entry", methods=["GET", "POST"])
@login_required
def entry():
    """Lançamento manual de produção (uma linha por vez)."""
    # Dono de empresa não lança registros; apenas visualiza relatórios
    if getattr(current_user, "is_company_owner", False) and not getattr(current_user, "is_admin", False):
        flash("Dono de empresa não pode lançar registros. Use apenas os relatórios.", "warning")
        return redirect(url_for("index"))
    # empresas configuradas
    companies = [c.name for c in CompanyConfig.query.order_by(CompanyConfig.name).all()]

    # mapas cadastrados por empresa
    maps_by_company = {}
    for m in CompanyMap.query.order_by(CompanyMap.company, CompanyMap.name).all():
        maps_by_company.setdefault(m.company, []).append(m.name)

    # dispositivos cadastrados por empresa
    devices_by_company = {}
    for dt in DeviceType.query.order_by(DeviceType.company, DeviceType.name).all():
        key = dt.company or "__global__"
        devices_by_company.setdefault(key, []).append(dt.name)

    # opções de splicer (para admin poder escolher quem lançou)
    splicer_options = [u.splicer_name or u.username for u in User.query.order_by(User.username).all()]

    default_splicer = getattr(current_user, "splicer_name", None) or current_user.username

    if request.method == "POST":
        company = (request.form.get("company") or "").strip() or None
        map_val = (request.form.get("map") or "").strip()
        type_val = (request.form.get("type") or "").strip()
        device_name = (request.form.get("device_name") or "").strip()

        # para cálculo de preço usamos o tipo (dispositivo configurado),
        # e guardamos o nome digitado separado
        device_for_price = type_val or device_name

        splices_raw = request.form.get("splices") or "0"
        created_raw = request.form.get("created") or ""
        splicer = (request.form.get("splicer") or "").strip() or default_splicer
        confirm_duplicate = (request.form.get("confirm_duplicate") == "yes")

        # checagem de duplicidade: mesmo map + mesmo nome de dispositivo (+ mesma empresa, se informada)
        existing = None
        if map_val and device_name:
            dup_query = Record.query.filter(Record.map == map_val, Record.device == device_name)
            if company:
                dup_query = dup_query.filter(Record.company == company)
            existing = dup_query.order_by(Record.created_date.desc().nullslast(), Record.id.desc()).first()

        if existing and not confirm_duplicate:
            # Primeiro aviso: já existe lançamento para este dispositivo neste mapa.
            # Mostra data e splicer e pede confirmação para lançar novamente.
            flash(
                "Este dispositivo já foi lançado neste map. Data: "
                + (existing.created_date.date().isoformat() if existing.created_date else "-")
                + f", Splicer: {existing.splicer}. Se desejar lançar novamente, confirme o lançamento.",
                "warning",
            )
            return render_template(
                "entry.html",
                companies=companies,
                maps_by_company=maps_by_company,
                devices_by_company=devices_by_company,
                splicer_options=splicer_options,
                default_splicer=default_splicer,
                today=date.today().isoformat(),
                duplicate_record=existing,
                form_company=company,
                form_map=map_val,
                form_type=type_val,
                form_device_name=device_name,
                form_splices=splices_raw,
                form_created=created_raw or date.today().isoformat(),
                confirm_duplicate=True,
            )

        # conversões finais para salvar o registro
        try:
            splices = int(splices_raw or 0)
        except ValueError:
            splices = 0

        if created_raw:
            try:
                # campo vem como YYYY-MM-DD
                created_date = datetime.strptime(created_raw, "%Y-%m-%d")
            except ValueError:
                created_date = datetime.utcnow()
        else:
            # padrão: hoje sem horário
            today = date.today()
            created_date = datetime(today.year, today.month, today.day)

        price_splices, price_device, total = compute_prices(splices, device_for_price, company)

        rec = Record(
            map=map_val,
            type=type_val,
            splices=splices,
            device=device_name,
            splicer=splicer,
            created_date=created_date,
            company=company,
            price_splices_usd=price_splices,
            price_device_usd=price_device,
            total_usd=total,
        )
        db.session.add(rec)
        db.session.commit()

        # salva fotos do dispositivo (se anexadas), redimensionadas e comprimidas
        photos = request.files.getlist("photos")
        save_record_photos(rec, photos)

        flash("Lançamento salvo.", "success")
        # após salvar, volta para a tela de lançamento já com a mesma empresa, mapa e tipo
        return redirect(url_for("entry", company=company or "", map=map_val or "", dtype=type_val or ""))

# GET
    pre_company = request.args.get("company") or None
    pre_map = request.args.get("map") or ""
    pre_type = request.args.get("dtype") or ""

    return render_template(
        "entry.html",
        companies=companies,
        maps_by_company=maps_by_company,
        devices_by_company=devices_by_company,
        splicer_options=splicer_options,
        default_splicer=default_splicer,
        today=date.today().isoformat(),
        form_company=pre_company,
        form_map=pre_map,
        form_type=pre_type,
    )


@app.route("/record/<int:rid>/edit", methods=["GET", "POST"])
@login_required
def record_edit(rid):
    """Editar um lançamento existente."""
    rec = Record.query.get_or_404(rid)

    # Apenas admin ou o próprio splicer podem editar
    if not getattr(current_user, "is_admin", False) and rec.splicer != current_user.username:
        flash("Você não tem permissão para editar este lançamento.", "danger")
        return redirect(url_for("index"))

    # mesmas estruturas de apoio usadas na tela de lançamento
    companies = [c.name for c in CompanyConfig.query.order_by(CompanyConfig.name).all()]

    maps_by_company = {}
    for m in CompanyMap.query.order_by(CompanyMap.company, CompanyMap.name).all():
        maps_by_company.setdefault(m.company, []).append(m.name)

    devices_by_company = {}
    for dt in DeviceType.query.order_by(DeviceType.company, DeviceType.name).all():
        key = dt.company or "__global__"
        devices_by_company.setdefault(key, []).append(dt.name)

    # mesmas opções de splicer da tela de lançamento
    splicer_options = [u.splicer_name or u.username for u in User.query.order_by(User.username).all()]

    # padrão sugerido para o admin: mantém o splicer já salvo no registro
    default_splicer = rec.splicer or (getattr(current_user, "splicer_name", None) or current_user.username)

    if request.method == "POST":
        company = (request.form.get("company") or "").strip() or None
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

        price_splices, price_device, total = compute_prices(splices, device_for_price, company)

        # atualiza o registro existente
        rec.company = company
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

    # GET: preenche o formulário com os dados atuais
    form_created = rec.created_date.date().isoformat() if rec.created_date else date.today().isoformat()

    return render_template(
        "entry.html",
        companies=companies,
        maps_by_company=maps_by_company,
        devices_by_company=devices_by_company,
        splicer_options=splicer_options,
        default_splicer=default_splicer,
        today=date.today().isoformat(),
        is_edit=True,
        record=rec,
        form_company=rec.company,
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

    types = DeviceType.query.filter_by(company=company.name).order_by(DeviceType.name).all()
    tiers = SpliceTier.query.filter_by(company=company.name).order_by(SpliceTier.min_splices).all()
    maps = CompanyMap.query.filter_by(company=company.name).order_by(CompanyMap.name).all()
    return render_template(
        "settings_company.html",
        company=company,
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

@app.route("/settings/device/add", methods=["POST"])
@login_required
def settings_device_add():
    name = (request.form.get("name") or "").strip()
    company = (request.form.get("company") or "").strip() or None
    next_url = (request.form.get("next") or "").strip() or None
    value_raw = request.form.get("value_usd") or "0"
    try:
        value = float(value_raw or 0)
    except ValueError:
        value = 0.0
    if not name:
        flash("Nome do dispositivo é obrigatório.", "danger")
        return redirect(next_url or url_for("settings"))

    dt = DeviceType.query.filter_by(name=name, company=company).first()
    if dt:
        dt.value_usd = value
    else:
        dt = DeviceType(name=name, company=company, value_usd=value)
        db.session.add(dt)
    db.session.commit()
    flash("Dispositivo salvo.", "success")
    return redirect(next_url or url_for("settings"))

    dt = DeviceType.query.filter_by(name=name, company=company).first()
    if dt:
        dt.value_usd = value
    else:
        dt = DeviceType(name=name, company=company, value_usd=value)
        db.session.add(dt)
    db.session.commit()
    flash("Dispositivo salvo.", "success")
    return redirect(url_for("settings"))


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
        is_admin = bool(request.form.get("is_admin"))
        is_company_owner = bool(request.form.get("is_company_owner"))

        if not username or not password:
            flash("Usuário e senha são obrigatórios.", "danger")
            return redirect(url_for("manage_users"))

        user = User.query.filter_by(username=username).first()
        if user:
            user.password = password
            user.splicer_name = splicer_name
            user.is_admin = is_admin
            user.is_company_owner = is_company_owner
            user.company_name = company_name
        else:
            user = User(
                username=username,
                password=password,
                splicer_name=splicer_name,
                is_admin=is_admin,
                is_company_owner=is_company_owner,
                company_name=company_name,
            )
            db.session.add(user)
        db.session.commit()
        flash("Usuário salvo com sucesso.", "success")
        return redirect(url_for("manage_users"))

    users = User.query.order_by(User.username).all()
    companies = CompanyConfig.query.order_by(CompanyConfig.name).all()
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
    # mesmos filtros do index
    company_filter = request.args.get("company") or None
    splicer_filter = request.args.get("splicer") or None
    map_filter = request.args.get("map") or None
    device_filter = request.args.get("device") or None
    start_raw = request.args.get("start") or None
    end_raw = request.args.get("end") or None
    no_values = request.args.get("no_values") == "1"

    query = Record.query
    if company_filter:
        query = query.filter(Record.company == company_filter)
    if splicer_filter:
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
            pass
    if end_raw:
        try:
            end_dt = datetime.fromisoformat(end_raw)
            query = query.filter(Record.created_date <= end_dt)
        except ValueError:
            pass

    # se não for admin, aplica mesma regra de visibilidade da tela principal
    if not getattr(current_user, "is_admin", False):
        # não permite PDF "sem valores" para não-admin
        no_values = False
        if getattr(current_user, "is_company_owner", False) and getattr(current_user, "company_name", None):
            query = query.filter(Record.company == current_user.company_name)
        else:
            enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
            query = query.filter(Record.splicer == enforced_splicer)

    records = query.order_by(Record.created_date.desc().nullslast(), Record.id.desc()).all()

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

    # cabeçalho
    if no_values:
        col_widths = [20, 22, 20, 18, 40, 18]
        headers = ["Data", "Empresa", "Map", "Type", "Dispositivo", "Splices"]
    else:
        col_widths = [18, 22, 20, 18, 40, 16, 18, 18]
        headers = ["Data", "Empresa", "Map", "Type", "Dispositivo", "Splices", "Fusoes $", "Total $"]

    for w, h in zip(col_widths, headers):
        pdf.cell(w, 7, h, border=1)
    pdf.ln()

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
            pdf.cell(w, 6, str(val)[:30], border=1)  # corta textos muito grandes
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

    # se não for admin, aplica mesma regra de visibilidade da tela principal
    if not getattr(current_user, "is_admin", False):
        if getattr(current_user, "is_company_owner", False) and getattr(current_user, "company_name", None):
            query = query.filter(Record.company == current_user.company_name)
        else:
            enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
            query = query.filter(Record.splicer == enforced_splicer)

    records = query.order_by(Record.created_date.asc().nullslast(), Record.id.asc()).all()

    # agrupar por mapa + dispositivo
    grouped = {}
    for r in records:
        key = ((r.map or "").strip(), (r.device or "").strip())
        if key not in grouped:
            grouped[key] = {
                "map": key[0] or "-",
                "device": key[1] or "-",
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

    # persist invoice for accounting
    inv_start_date = start_dt.date() if start_dt else None
    inv_end_date = end_dt.date() if end_dt else None
    inv_rec = Invoice(number=inv_number, company=company_filter or "", start_date=inv_start_date, end_date=inv_end_date, total_usd=float(total_invoice or 0.0))
    db.session.add(inv_rec)
    db.session.commit()

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
    col_widths = [60, 40, 25, 30, 30]
    headers = ["Map", "Device", "Splices", "Device price", "Total"]

    pdf.set_font("Arial", "B", 10)
    for w, h in zip(col_widths, headers):
        pdf.cell(w, 7, h, border=1)
    pdf.ln()

    pdf.set_font("Arial", "", 9)
    for l in lines:
        row = [
            l["map"],
            l["device"],
            str(l["splices"]),
            f"$ {l['price_device_usd']:.2f}",
            f"$ {l['total_usd']:.2f}",
        ]
        for w, val in zip(col_widths, row):
            pdf.cell(w, 6, str(val)[:30], border=1)
        pdf.ln()

    pdf.ln(4)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(0, 8, f"Invoice total: $ {total_invoice:.2f}", ln=1)

    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)
    filename = "invoice_splicer.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")





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

    # se não for admin, aplica mesma regra de visibilidade da tela principal
    if not getattr(current_user, "is_admin", False):
        if getattr(current_user, "is_company_owner", False) and getattr(current_user, "company_name", None):
            query = query.filter(Record.company == current_user.company_name)
        else:
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

    # Dono de empresa não pode excluir lançamentos (somente admin)
    if getattr(current_user, "is_company_owner", False) and not getattr(current_user, "is_admin", False):
        flash("Dono de empresa não pode excluir lançamentos.", "danger")
        return redirect(url_for("index"))

    # Apenas admin ou o próprio splicer podem editar/apagar
    if not getattr(current_user, "is_admin", False) and rec.splicer != current_user.username:
        flash("Você não tem permissão para editar este lançamento.", "danger")
        return redirect(url_for("index"))

    # Apenas admin pode apagar qualquer registro.
    # Usuário comum só pode apagar o próprio lançamento.
    if not getattr(current_user, "is_admin", False):
        enforced_splicer = getattr(current_user, "splicer_name", None) or current_user.username
        if rec.splicer != enforced_splicer:
            abort(403)

    db.session.delete(rec)
    db.session.commit()
    flash("Registro removido.", "success")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
