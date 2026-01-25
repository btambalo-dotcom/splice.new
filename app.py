from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from datetime import datetime, date
import pandas as pd
from sqlalchemy import text, case, or_
import os
from fpdf import FPDF
from io import BytesIO
from functools import wraps

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

    # migração simples de colunas (caso use um data.db antigo)
    def ensure(table, col, typ):
        existing = [r[1] for r in db.session.execute(text(f"PRAGMA table_info({table})"))]
        if col not in existing:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
            db.session.commit()

    ensure("record", "company", "VARCHAR(120)")
    ensure("device_type", "company", "VARCHAR(120)")
    ensure("splice_tier", "company", "VARCHAR(120)")
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


def apply_prices(df: pd.DataFrame, default_company: str | None = None) -> pd.DataFrame:
    if "Company" not in df.columns:
        df["Company"] = default_company

    def calc(row: pd.Series):
        company = row.get("Company") or default_company
        splices = int(row.get("Splices") or 0)
        included = included_splices_for(company)
        charge = max(splices - included, 0)
        price_splices = charge * tier_price_for(charge, company)
        price_device = device_value_for(str(row.get("Device") or ""), company)
        return pd.Series([price_splices, price_device, price_splices + price_device])

    df[["price_splices_usd", "price_device_usd", "total_usd"]] = df.apply(calc, axis=1)
    return df

def parse_excel(file_storage, default_company: str | None = None) -> pd.DataFrame:
    if not file_storage:
        raise ValueError("Nenhum arquivo enviado.")
    df = pd.read_excel(file_storage)
    df.columns = [str(c).strip() for c in df.columns]

    required = ["Type", "Map", "Splices", "Device", "Splicer", "Created"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError("Colunas ausentes: " + ", ".join(missing))

    # garante colunas mínimas
    out = df[required].copy()
    if "Company" in df.columns:
        out["Company"] = df["Company"].fillna(default_company)
    else:
        out["Company"] = default_company

    out["Splices"] = pd.to_numeric(out["Splices"], errors="coerce").fillna(0).astype(int)
    out["Created"] = pd.to_datetime(out["Created"], errors="coerce")
    return out

def persist_df(df: pd.DataFrame):
    rows = []
    for _, r in df.iterrows():
        rec = Record(
            map=str(r.get("Map") or ""),
            type=str(r.get("Type") or ""),
            splices=int(r.get("Splices") or 0),
            device=str(r.get("Device") or ""),
            splicer=str(r.get("Splicer") or ""),
            created_date=r.get("Created"),
            company=r.get("Company"),
            price_splices_usd=float(r.get("price_splices_usd") or 0),
            price_device_usd=float(r.get("price_device_usd") or 0),
            total_usd=float(r.get("total_usd") or 0),
        )
        rows.append(rec)
    if rows:
        db.session.bulk_save_objects(rows)
        db.session.commit()


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
    if request.method == "POST":
        company = request.form.get("company") or None
        file = request.files.get("file")
        try:
            df = parse_excel(file, default_company=company)
            df = apply_prices(df, default_company=company)
            persist_df(df)
            flash("Planilha importada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao importar planilha: {e}", "danger")
        return redirect(url_for("index"))

    # filtros
    company_filter = request.args.get("company") or None
    splicer_filter = request.args.get("splicer") or None
    map_filter = request.args.get("map") or None
    start_raw = request.args.get("start") or None
    end_raw = request.args.get("end") or None

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
        return redirect(url_for("index"))

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
    return render_template("settings.html", companies=companies)

@app.route("/settings/company/add", methods=["POST"])
@login_required
def settings_company_add():
    name = (request.form.get("name") or "").strip()
    included_raw = request.form.get("included_splices") or "0"
    included = int(included_raw or 0)

    if not name:
        flash("Nome da empresa é obrigatório.", "danger")
        return redirect(url_for("settings"))

    cfg = CompanyConfig.query.filter_by(name=name).first()
    if cfg:
        cfg.included_splices = included
    else:
        cfg = CompanyConfig(name=name, included_splices=included)
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

    # total do período (somente se for relatório com valores)
    total_amount = sum((r.total_usd or 0) for r in records)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Relatorio de Producao - SPLICER", ln=1)
    pdf.set_font("Arial", "", 9)

    if not no_values:
        pdf.cell(0, 8, f"Total no período: $ {total_amount:.2f}", ln=1)
        pdf.ln(2)
    else:
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


@app.route("/record/<int:rid>/delete")
@login_required
def record_delete(rid: int):
    rec = Record.query.get_or_404(rid)
    db.session.delete(rec)
    db.session.commit()
    flash("Registro removido.", "success")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
