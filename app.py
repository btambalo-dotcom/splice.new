from flask import Flask, render_template, request, redirect, url_for, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime, date
from sqlalchemy import text, case, or_, inspect
import os
from fpdf import FPDF
from io import BytesIO
from functools import wraps
import csv
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side

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

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# --------- Models ---------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)  # simples, sem hash, uso local
    is_admin = db.Column(db.Boolean, default=False)
    splicer_name = db.Column(db.String(120), nullable=True)  # nome que aparece como Splicer nos lançamentos

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

# --------- User loader ---------
@login_manager.user_loader
def load_user(user_id: str):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None

# --------- DB init & migrations simples ---------
with app.app_context():
    db.create_all()

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
    ensure("company_config", "invoice_address", "TEXT")
    ensure("user", "is_admin", "BOOLEAN")
    ensure("user", "splicer_name", "VARCHAR(120)")

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

    # se não for admin, restringe SEMPRE aos lançamentos do próprio usuário
    enforced_splicer = None
    if not getattr(current_user, "is_admin", False):
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
        start=start_raw or "",
        end=end_raw or "",
    )

@app.route("/entry", methods=["GET", "POST"])
@login_required
def entry():
    """Lançamento manual de produção (uma linha por vez)."""
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
        try:
            splices = int(splices_raw or 0)
        except ValueError:
            splices = 0
        splicer = (request.form.get("splicer") or "").strip() or default_splicer
        created_raw = request.form.get("created") or ""
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
        flash("Lançamento salvo.", "success")
        # após salvar, permanece na tela de lançamento para permitir novo registro
        return redirect(url_for("entry"))

    # GET
    return render_template(
        "entry.html",
        companies=companies,
        maps_by_company=maps_by_company,
        devices_by_company=devices_by_company,
        default_splicer=default_splicer,
        today=date.today().isoformat(),
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
        is_admin = bool(request.form.get("is_admin"))

        if not username or not password:
            flash("Usuário e senha são obrigatórios.", "danger")
            return redirect(url_for("manage_users"))

        user = User.query.filter_by(username=username).first()
        if user:
            user.password = password
            user.splicer_name = splicer_name
            user.is_admin = is_admin
        else:
            user = User(
                username=username,
                password=password,
                splicer_name=splicer_name,
                is_admin=is_admin,
            )
            db.session.add(user)
        db.session.commit()
        flash("Usuário salvo com sucesso.", "success")
        return redirect(url_for("manage_users"))

    users = User.query.order_by(User.username).all()
    return render_template("users.html", users=users)

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

    # se não for admin, restringe aos lançamentos do próprio usuário
    if not getattr(current_user, "is_admin", False):
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
        col_widths = [24, 24, 24, 22, 32, 18]
        headers = ["Data", "Empresa", "Map", "Type", "Dispositivo", "Splices"]
    else:
        col_widths = [22, 22, 22, 20, 25, 18, 18, 18]
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
            pdf.cell(w, 6, str(val)[:16], border=1)  # corta textos muito grandes
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

    db.session.delete(rec)
    db.session.commit()
    flash("Registro removido.", "success")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
