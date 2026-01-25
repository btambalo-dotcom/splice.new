
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin
from datetime import datetime
import pandas as pd
from sqlalchemy import text, case, or_

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///data.db'
db = SQLAlchemy(app)
login = LoginManager(app)
login.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True)
    password = db.Column(db.String(120))

class CompanyConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True)
    included_splices = db.Column(db.Integer, default=1)

class DeviceType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    value_usd = db.Column(db.Float, default=0.0)
    company = db.Column(db.String(120))

class SpliceTier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    min_splices = db.Column(db.Integer)
    max_splices = db.Column(db.Integer)
    price_per_splice_usd = db.Column(db.Float, default=0.0)
    company = db.Column(db.String(120))

class Record(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    map = db.Column(db.String(200))
    type = db.Column(db.String(120))
    splices = db.Column(db.Integer)
    device = db.Column(db.String(120))
    splicer = db.Column(db.String(120))
    created_date = db.Column(db.DateTime)
    company = db.Column(db.String(120))
    price_splices_usd = db.Column(db.Float, default=0.0)
    price_device_usd = db.Column(db.Float, default=0.0)
    total_usd = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login.user_loader
def load_user(uid):
    return User.query.get(int(uid))

with app.app_context():
    db.create_all()
    # add migration columns
    def ensure(table, col, typ):
        existing=[r[1] for r in db.session.execute(text(f"PRAGMA table_info({table})"))]
        if col not in existing:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
            db.session.commit()
    ensure("record","company","VARCHAR(120)")
    ensure("device_type","company","VARCHAR(120)")
    ensure("splice_tier","company","VARCHAR(120)")

def included_splices_for(company):
    if not company: return 1
    cfg=CompanyConfig.query.filter_by(name=company).first()
    return cfg.included_splices if cfg else 1

def device_value_for(name, company):
    q=DeviceType.query.filter(DeviceType.name.ilike(name))
    q=q.filter(or_(DeviceType.company==company, DeviceType.company.is_(None)))
    dt=q.order_by(case((DeviceType.company==company,0),else_=1)).first()
    return dt.value_usd if dt else 0

def tier_price_for(count, company):
    q=SpliceTier.query.filter(SpliceTier.min_splices<=count)
    q=q.filter(or_(SpliceTier.company==company, SpliceTier.company.is_(None)))
    q=q.filter(or_(SpliceTier.max_splices==None, SpliceTier.max_splices>=count))
    tier=q.order_by(case((SpliceTier.company==company,0),else_=1),
                    SpliceTier.min_splices.desc()).first()
    return tier.price_per_splice_usd if tier else 0

def apply_prices(df, default_company=None):
    if "Company" not in df.columns:
        df["Company"]=default_company
    def calc(r):
        sp=int(r["Splices"])
        company=r.get("Company") or default_company
        included=included_splices_for(company)
        charge=max(sp - included,0)
        return pd.Series([
            charge * tier_price_for(charge, company),
            device_value_for(str(r["Device"]), company)
        ])
    df[["price_splices_usd","price_device_usd"]] = df.apply(calc, axis=1)
    df["total_usd"] = df["price_splices_usd"] + df["price_device_usd"]
    return df

@app.route("/", methods=["GET","POST"])
@login_required
def index():
    if request.method=="POST":
        company=request.form.get("company")
        f=request.files.get("file")
        df=pd.read_excel(f)
        df=apply_prices(df,company)
        for _,r in df.iterrows():
            rec=Record(
                map=r.get("Map"),
                type=r.get("Type"),
                splices=int(r.get("Splices")),
                device=r.get("Device"),
                splicer=r.get("Splicer"),
                created_date=r.get("Created"),
                company=company,
                price_splices_usd=float(r["price_splices_usd"]),
                price_device_usd=float(r["price_device_usd"]),
                total_usd=float(r["total_usd"])
            )
            db.session.add(rec)
        db.session.commit()
        flash("Dados importados","success")
        return redirect(url_for("index"))
    recs=Record.query.order_by(Record.created_at.desc()).all()
    return render_template("index.html", recs=recs)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u=User.query.filter_by(username=request.form["username"]).first()
        if u and u.password==request.form["password"]:
            login_user(u)
            return redirect(url_for("index"))
        flash("Login inválido","error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    companies=CompanyConfig.query.all()
    types=DeviceType.query.all()
    tiers=SpliceTier.query.all()
    return render_template("settings.html", companies=companies, types=types, tiers=tiers)

@app.route("/settings/company/add", methods=["POST"])
@login_required
def settings_company_add():
    name=request.form["name"]
    inc=int(request.form["included"])
    c=CompanyConfig.query.filter_by(name=name).first()
    if not c:
        c=CompanyConfig(name=name, included_splices=inc)
        db.session.add(c)
    else:
        c.included_splices=inc
    db.session.commit()
    return redirect(url_for("settings"))

if __name__=="__main__":
    app.run(debug=True)
