"""MyCount - a very simple tool to track business growth."""
import csv
import io
import os
import re
import zipfile
import shutil
import sqlite3
from datetime import date, timedelta

from flask import Flask, Response, abort, g, redirect, render_template, request, url_for, flash

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "mycount.db"))

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    email TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT DEFAULT '',
    store_link TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_number TEXT NOT NULL DEFAULT '',
    job_name TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    date TEXT NOT NULL,
    quoted_price REAL NOT NULL DEFAULT 0,
    expenses REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'paid',
    stage TEXT NOT NULL DEFAULT 'new'
);
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_name TEXT NOT NULL,
    product TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unpaid',
    date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_name TEXT NOT NULL,
    item_name TEXT NOT NULL,
    description TEXT DEFAULT '',
    store_link TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS goals (
    year INTEGER PRIMARY KEY,
    revenue REAL NOT NULL DEFAULT 0,
    profit REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "business_name": "My Business",
    "currency": "₱",
    "fy_start_month": "1",
}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-docker-compose")


# ---------- database helpers ----------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def upgrade_sales_to_jobs(db):
    """Turn sales saved by the first version (product, qty, price) into jobs.

    Product name becomes the job name and the total becomes the quoted price.
    Old sales start with no job number, no expenses and status "paid".
    A copy of the database is saved first, just in case.
    """
    columns = [row[1] for row in db.execute("PRAGMA table_info(sales)")]
    if "product_name" not in columns:
        return
    backup = DB_PATH + ".before-jobs-update.bak"
    if not os.path.exists(backup):
        shutil.copy2(DB_PATH, backup)
    db.execute("ALTER TABLE sales RENAME COLUMN product_name TO job_name")
    db.execute("ALTER TABLE sales RENAME COLUMN total TO quoted_price")
    db.execute("ALTER TABLE sales ADD COLUMN job_number TEXT NOT NULL DEFAULT ''")
    db.execute("ALTER TABLE sales ADD COLUMN expenses REAL NOT NULL DEFAULT 0")
    db.execute("ALTER TABLE sales ADD COLUMN status TEXT NOT NULL DEFAULT 'paid'")


def add_job_stage(db):
    """Give jobs saved before job statuses existed the status "done"."""
    columns = [row[1] for row in db.execute("PRAGMA table_info(sales)")]
    if columns and "stage" not in columns:
        db.execute("ALTER TABLE sales ADD COLUMN stage TEXT NOT NULL DEFAULT 'done'")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, isolation_level=None)
    # Lock the database so two app workers starting together don't upgrade it twice
    db.execute("BEGIN IMMEDIATE")
    upgrade_sales_to_jobs(db)
    add_job_stage(db)
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)
    for key, value in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.execute("COMMIT")
    db.close()


def get_settings():
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    settings = dict(DEFAULT_SETTINGS)
    settings.update({r["key"]: r["value"] for r in rows})
    return settings


@app.context_processor
def inject_globals():
    settings = get_settings()
    cur = settings["currency"]

    def money(v):
        return f"{'-' if v < 0 else ''}{cur}{abs(v):,.2f}"

    def money_short(v):
        """Short money for chart axes: 1500 -> 1.5k, 2000000 -> 2M."""
        for size, unit in ((1e6, "M"), (1e3, "k")):
            if abs(v) >= size:
                return f"{cur}{v / size:,.1f}".rstrip("0").rstrip(".") + unit
        return f"{cur}{v:,.0f}"

    return {"settings": settings, "money": money, "money_short": money_short}


def margin(profit, price):
    """Profit as a percentage of the price, or None when there is no price."""
    return profit / price * 100 if price else None


@app.template_filter("pct")
def format_pct(value, digits=0):
    return "—" if value is None else f"{value:.{digits}f}%"


def to_number(value, default=0.0):
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return default


def form_text(name):
    return request.form.get(name, "").strip()


# ---------- financial year ----------

def fy_range(start_year, start_month):
    """Return (first_day, last_day, label) of the financial year that starts in start_year."""
    first = date(start_year, start_month, 1)
    if start_month == 1:
        last = date(start_year, 12, 31)
        label = str(start_year)
    else:
        last = date(start_year + 1, start_month, 1) - timedelta(days=1)
        label = f"{start_year}-{start_year + 1}"
    return first, last, label


def current_fy_start_year(start_month):
    today = date.today()
    return today.year if today.month >= start_month else today.year - 1


# ---------- dashboard ----------

def nice_ceiling(value):
    """Round a chart maximum up to a tidy number (1, 2, 2.5 or 5 x 10^n)."""
    if value <= 0:
        return 1
    power = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * power:
            return step * power
    return 10 * power


def year_fraction_done(first, last):
    """How much of the financial year has passed: 0 before it starts, 1 after it ends."""
    today = date.today()
    if today < first:
        return 0.0
    if today > last:
        return 1.0
    return ((today - first).days + 1) / ((last - first).days + 1)


def goal_progress(actual, target, expected_fraction):
    if not target:
        return None
    percent = actual / target * 100
    if actual >= target:
        state = "reached"
    elif percent >= expected_fraction * 100:
        state = "on_track"
    else:
        state = "behind"
    return {"target": target, "actual": actual, "percent": percent, "bar": max(0, min(percent, 100)),
            "expected": expected_fraction * 100, "state": state}


@app.route("/")
def dashboard():
    db = get_db()
    start_month = int(get_settings()["fy_start_month"])
    year = request.args.get("year", type=int) or current_fy_start_year(start_month)
    first, last, label = fy_range(year, start_month)
    params = (first.isoformat(), last.isoformat())

    income, job_expenses, job_count = db.execute(
        "SELECT COALESCE(SUM(quoted_price), 0), COALESCE(SUM(expenses), 0), COUNT(*) FROM sales "
        "WHERE date BETWEEN ? AND ?", params).fetchone()
    bought = db.execute("SELECT COALESCE(SUM(amount), 0) FROM purchases WHERE date BETWEEN ? AND ?", params).fetchone()[0]
    expenses = bought + job_expenses
    profit = income - expenses
    unpaid = db.execute(
        "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM purchases WHERE status = 'unpaid' AND date BETWEEN ? AND ?",
        params).fetchone()
    owed = db.execute(
        "SELECT COALESCE(SUM(quoted_price), 0), COUNT(*) FROM sales WHERE status = 'unpaid' AND date BETWEEN ? AND ?",
        params).fetchone()

    # Month by month numbers
    today_prefix = date.today().isoformat()[:7]
    months = []
    for i in range(12):
        m = (start_month - 1 + i) % 12 + 1
        y = year if m >= start_month else year + 1
        prefix = f"{y:04d}-{m:02d}"
        m_in, m_job_out, m_jobs = db.execute(
            "SELECT COALESCE(SUM(quoted_price), 0), COALESCE(SUM(expenses), 0), COUNT(*) FROM sales "
            "WHERE substr(date, 1, 7) = ?", (prefix,)).fetchone()
        m_bought = db.execute("SELECT COALESCE(SUM(amount), 0) FROM purchases WHERE substr(date, 1, 7) = ?",
                              (prefix,)).fetchone()[0]
        m_out = m_bought + m_job_out
        months.append({"name": MONTHS[m - 1][:3], "full": f"{MONTHS[m - 1]} {y}", "year": y, "income": m_in,
                       "expenses": m_out, "profit": m_in - m_out, "jobs": m_jobs,
                       "job_margin": margin(m_in - m_job_out, m_in), "current": prefix == today_prefix,
                       "future": prefix > today_prefix})
    chart_max = nice_ceiling(max([0] + [max(mo["income"], mo["expenses"]) for mo in months]))

    # This month compared with last month (only while looking at the current financial year)
    this_month = next((i for i, mo in enumerate(months) if mo["current"]), None)
    month_now = months[this_month] if this_month is not None else None
    month_before = months[this_month - 1] if this_month else None
    months_so_far = sum(1 for mo in months if not mo["future"]) or 1

    done = year_fraction_done(first, last)
    goal = db.execute("SELECT revenue, profit FROM goals WHERE year = ?", (year,)).fetchone()
    revenue_goal = goal_progress(income, goal["revenue"], done) if goal else None
    profit_goal = goal_progress(profit, goal["profit"], done) if goal else None

    # Years that have data, so the year picker only shows useful choices
    years = {current_fy_start_year(start_month)}
    for table in ("sales", "purchases"):
        for row in db.execute(f"SELECT DISTINCT date FROM {table}"):
            d = date.fromisoformat(row[0])
            years.add(d.year if d.month >= start_month else d.year - 1)
    year_choices = [(y, fy_range(y, start_month)[2]) for y in sorted(years, reverse=True)]

    top_customers = db.execute(
        "SELECT customer_name, COUNT(*) AS jobs, SUM(quoted_price) AS quoted, SUM(quoted_price - expenses) AS profit "
        "FROM sales WHERE date BETWEEN ? AND ? GROUP BY customer_name ORDER BY quoted DESC LIMIT 5", params).fetchall()

    return render_template(
        "dashboard.html", income=income, expenses=expenses, bought=bought, job_expenses=job_expenses,
        profit=profit, net_margin=margin(profit, income), job_count=job_count,
        job_margin=margin(income - job_expenses, income),
        avg_month_income=income / months_so_far, months_so_far=months_so_far,
        unpaid_amount=unpaid[0], unpaid_count=unpaid[1], owed_amount=owed[0], owed_count=owed[1],
        months=months, chart_max=chart_max, month_now=month_now, month_before=month_before,
        goal=goal, revenue_goal=revenue_goal, profit_goal=profit_goal, year_done=done * 100,
        year=year, label=label, first=first, last=last, year_choices=year_choices,
        top_customers=top_customers)


@app.post("/goals/<int:year>")
def save_goals(year):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO goals (year, revenue, profit) VALUES (?, ?, ?)",
               (year, max(0, to_number(request.form.get("revenue"))), max(0, to_number(request.form.get("profit")))))
    db.commit()
    flash("Goals saved.", "ok")
    return redirect(url_for("dashboard", year=year))


# ---------- sales (jobs) ----------

# Job status: what the form and table show, in the order a job goes through them
STAGES = {"new": "New job", "pending": "Pending", "done": "Done"}

def next_job_number(db):
    """Guess the next job number from the last one typed: 7 -> 8, J-009 -> J-010."""
    row = db.execute("SELECT job_number FROM sales WHERE job_number != '' ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return "1"
    match = re.search(r"(\d+)(?!.*\d)", row[0])
    if not match:
        return ""
    digits = match.group(1)
    bumped = str(int(digits) + 1).zfill(len(digits))
    return row[0][:match.start()] + bumped + row[0][match.end():]


@app.route("/sales", methods=["GET", "POST"])
@app.route("/sales/<int:item_id>/edit", methods=["GET", "POST"])
def sales(item_id=None):
    db = get_db()
    editing = db.execute("SELECT * FROM sales WHERE id = ?", (item_id,)).fetchone() if item_id else None
    if request.method == "POST":
        job_number, job_name, customer = form_text("job_number"), form_text("job_name"), form_text("customer_name")
        quoted = to_number(request.form.get("quoted_price"))
        job_expenses = to_number(request.form.get("expenses"))
        status = "paid" if request.form.get("status") == "paid" else "unpaid"
        stage = request.form.get("stage") if request.form.get("stage") in STAGES else "new"
        job_date = form_text("date") or date.today().isoformat()
        if not job_name or not customer:
            flash("Please type the job name and the customer name.", "error")
        else:
            values = (job_number, job_name, customer, job_date, quoted, job_expenses, status, stage)
            if editing:
                db.execute("UPDATE sales SET job_number=?, job_name=?, customer_name=?, date=?, quoted_price=?, "
                           "expenses=?, status=?, stage=? WHERE id=?", values + (item_id,))
                flash("Job updated.", "ok")
            else:
                db.execute("INSERT INTO sales (job_number, job_name, customer_name, date, quoted_price, expenses, "
                           "status, stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
                flash("Job saved.", "ok")
            db.commit()
            return redirect(url_for("sales"))

    q = request.args.get("q", "").strip()
    show = request.args.get("show", "all")
    sql = "SELECT * FROM sales WHERE (job_number LIKE ? OR job_name LIKE ? OR customer_name LIKE ?)"
    args = [f"%{q}%"] * 3
    if show in ("paid", "unpaid"):
        sql += " AND status = ?"
        args.append(show)
    stage = request.args.get("stage", "all")
    if stage in STAGES:
        sql += " AND stage = ?"
        args.append(stage)
    rows = db.execute(sql + " ORDER BY date DESC, id DESC", args).fetchall()
    stage_counts = dict(db.execute("SELECT stage, COUNT(*) FROM sales GROUP BY stage").fetchall())
    customers = db.execute("SELECT name FROM customers ORDER BY name").fetchall()
    return render_template("sales.html", rows=rows, customers=customers, editing=editing, q=q, show=show,
                           stage=stage, stages=STAGES, stage_counts=stage_counts,
                           today=date.today().isoformat(), next_number=next_job_number(db))


@app.post("/sales/<int:item_id>/toggle")
def toggle_sale(item_id):
    db = get_db()
    db.execute("UPDATE sales SET status = CASE status WHEN 'paid' THEN 'unpaid' ELSE 'paid' END WHERE id = ?",
               (item_id,))
    db.commit()
    return redirect(request.referrer or url_for("sales"))


@app.post("/sales/<int:item_id>/stage")
def next_stage(item_id):
    """Move a job on to its next status: New job -> Pending -> Done -> New job."""
    db = get_db()
    db.execute("UPDATE sales SET stage = CASE stage WHEN 'new' THEN 'pending' WHEN 'pending' THEN 'done' ELSE 'new' END "
               "WHERE id = ?", (item_id,))
    db.commit()
    return redirect(request.referrer or url_for("sales"))


@app.post("/sales/<int:item_id>/delete")
def delete_sale(item_id):
    db = get_db()
    db.execute("DELETE FROM sales WHERE id = ?", (item_id,))
    db.commit()
    flash("Job deleted.", "ok")
    return redirect(url_for("sales"))


# ---------- purchases ----------

@app.route("/purchases", methods=["GET", "POST"])
@app.route("/purchases/<int:item_id>/edit", methods=["GET", "POST"])
def purchases(item_id=None):
    db = get_db()
    editing = db.execute("SELECT * FROM purchases WHERE id = ?", (item_id,)).fetchone() if item_id else None
    if request.method == "POST":
        supplier, product = form_text("supplier_name"), form_text("product")
        amount = to_number(request.form.get("amount"))
        status = "paid" if request.form.get("status") == "paid" else "unpaid"
        buy_date = form_text("date") or date.today().isoformat()
        if not supplier or not product:
            flash("Please type the supplier name and the product.", "error")
        else:
            values = (supplier, product, amount, status, buy_date)
            if editing:
                db.execute("UPDATE purchases SET supplier_name=?, product=?, amount=?, status=?, date=? WHERE id=?",
                           values + (item_id,))
                flash("Purchase updated.", "ok")
            else:
                db.execute("INSERT INTO purchases (supplier_name, product, amount, status, date) VALUES (?, ?, ?, ?, ?)",
                           values)
                flash("Purchase saved.", "ok")
            db.commit()
            return redirect(url_for("purchases"))

    q = request.args.get("q", "").strip()
    show = request.args.get("show", "all")
    sql = "SELECT * FROM purchases WHERE (supplier_name LIKE ? OR product LIKE ?)"
    args = [f"%{q}%", f"%{q}%"]
    if show in ("paid", "unpaid"):
        sql += " AND status = ?"
        args.append(show)
    rows = db.execute(sql + " ORDER BY date DESC, id DESC", args).fetchall()
    suppliers = db.execute("SELECT name FROM suppliers ORDER BY name").fetchall()
    parts = db.execute("SELECT DISTINCT item_name FROM parts ORDER BY item_name").fetchall()
    return render_template("purchases.html", rows=rows, suppliers=suppliers, parts=parts, editing=editing, q=q, show=show,
                           today=date.today().isoformat())


@app.post("/purchases/<int:item_id>/toggle")
def toggle_purchase(item_id):
    db = get_db()
    db.execute("UPDATE purchases SET status = CASE status WHEN 'paid' THEN 'unpaid' ELSE 'paid' END WHERE id = ?",
               (item_id,))
    db.commit()
    return redirect(request.referrer or url_for("purchases"))


@app.post("/purchases/<int:item_id>/delete")
def delete_purchase(item_id):
    db = get_db()
    db.execute("DELETE FROM purchases WHERE id = ?", (item_id,))
    db.commit()
    flash("Purchase deleted.", "ok")
    return redirect(url_for("purchases"))


# ---------- parts (where to buy things again) ----------

@app.route("/parts", methods=["GET", "POST"])
@app.route("/parts/<int:item_id>/edit", methods=["GET", "POST"])
def parts(item_id=None):
    db = get_db()
    editing = db.execute("SELECT * FROM parts WHERE id = ?", (item_id,)).fetchone() if item_id else None
    if request.method == "POST":
        supplier, item = form_text("supplier_name"), form_text("item_name")
        description, link = form_text("description"), form_text("store_link")
        if not supplier or not item:
            flash("Please type the supplier name and the item name.", "error")
        else:
            values = (supplier, item, description, link)
            if editing:
                db.execute("UPDATE parts SET supplier_name=?, item_name=?, description=?, store_link=? WHERE id=?",
                           values + (item_id,))
                flash("Part updated.", "ok")
            else:
                db.execute("INSERT INTO parts (supplier_name, item_name, description, store_link) VALUES (?, ?, ?, ?)",
                           values)
                flash("Part saved.", "ok")
            db.commit()
            return redirect(url_for("parts"))

    q = request.args.get("q", "").strip()
    rows = db.execute(
        "SELECT * FROM parts WHERE item_name LIKE ? OR description LIKE ? OR supplier_name LIKE ? "
        "ORDER BY item_name, supplier_name", (f"%{q}%",) * 3).fetchall()
    suppliers = db.execute("SELECT name FROM suppliers ORDER BY name").fetchall()
    return render_template("parts.html", rows=rows, suppliers=suppliers, editing=editing, q=q)


@app.post("/parts/<int:item_id>/delete")
def delete_part(item_id):
    db = get_db()
    db.execute("DELETE FROM parts WHERE id = ?", (item_id,))
    db.commit()
    flash("Part deleted.", "ok")
    return redirect(url_for("parts"))


# ---------- customers & suppliers ----------

PEOPLE = {
    "customers": {"title": "Customers", "one": "customer", "icon": "users", "fields": [
        ("name", "Name", "text"), ("address", "Address", "text"),
        ("phone", "Phone", "tel"), ("email", "Email", "email")]},
    "suppliers": {"title": "Suppliers", "one": "supplier", "icon": "truck", "fields": [
        ("name", "Name", "text"), ("address", "Address", "text"),
        ("store_link", "Store link", "url")]},
}


def people_page(kind, item_id=None):
    db = get_db()
    info = PEOPLE[kind]
    columns = [f[0] for f in info["fields"]]
    editing = db.execute(f"SELECT * FROM {kind} WHERE id = ?", (item_id,)).fetchone() if item_id else None
    if request.method == "POST":
        values = [form_text(c) for c in columns]
        if not values[0]:
            flash(f"Please type the {info['one']}'s name.", "error")
        else:
            if editing:
                sets = ", ".join(f"{c} = ?" for c in columns)
                db.execute(f"UPDATE {kind} SET {sets} WHERE id = ?", values + [item_id])
                flash(f"{info['one'].title()} updated.", "ok")
            else:
                db.execute(f"INSERT INTO {kind} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                           values)
                flash(f"{info['one'].title()} added.", "ok")
            db.commit()
            return redirect(url_for(kind))
    q = request.args.get("q", "").strip()
    rows = db.execute(f"SELECT * FROM {kind} WHERE name LIKE ? ORDER BY name", (f"%{q}%",)).fetchall()
    return render_template("people.html", kind=kind, info=info, rows=rows, editing=editing, q=q)


def people_delete(kind, item_id):
    db = get_db()
    db.execute(f"DELETE FROM {kind} WHERE id = ?", (item_id,))
    db.commit()
    flash(f"{PEOPLE[kind]['one'].title()} deleted.", "ok")
    return redirect(url_for(kind))


@app.route("/customers", methods=["GET", "POST"])
@app.route("/customers/<int:item_id>/edit", methods=["GET", "POST"])
def customers(item_id=None):
    return people_page("customers", item_id)


@app.post("/customers/<int:item_id>/delete")
def delete_customer(item_id):
    return people_delete("customers", item_id)


@app.route("/suppliers", methods=["GET", "POST"])
@app.route("/suppliers/<int:item_id>/edit", methods=["GET", "POST"])
def suppliers(item_id=None):
    return people_page("suppliers", item_id)


@app.post("/suppliers/<int:item_id>/delete")
def delete_supplier(item_id):
    return people_delete("suppliers", item_id)


# ---------- settings ----------

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    db = get_db()
    if request.method == "POST":
        month = request.form.get("fy_start_month", "1")
        new = {
            "business_name": form_text("business_name") or DEFAULT_SETTINGS["business_name"],
            "currency": form_text("currency"),
            "fy_start_month": month if month.isdigit() and 1 <= int(month) <= 12 else "1",
        }
        for key, value in new.items():
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        db.commit()
        flash("Settings saved.", "ok")
        return redirect(url_for("settings_page"))
    counts = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in EXPORTS}
    return render_template("settings.html", months=MONTHS, exports=EXPORTS, counts=counts)


# ---------- export to CSV ----------

# file name -> (title, column headings, SQL)
EXPORTS = {
    "sales": ("Sales (jobs)",
              ["Job number", "Job name", "Customer", "Date", "Quoted price", "Expenses", "Profit",
               "Profit margin %", "Job status", "Payment status"],
              "SELECT job_number, job_name, customer_name, date, quoted_price, expenses, "
              "quoted_price - expenses AS profit, "
              "CASE WHEN quoted_price > 0 THEN ROUND((quoted_price - expenses) * 100.0 / quoted_price, 1) END, "
              "CASE stage WHEN 'new' THEN 'New job' WHEN 'pending' THEN 'Pending' ELSE 'Done' END, "
              "CASE status WHEN 'paid' THEN 'Paid' ELSE 'Not yet' END FROM sales ORDER BY date, id"),
    "purchases": ("Purchases", ["Supplier", "Product", "Amount", "Status", "Date"],
                  "SELECT supplier_name, product, amount, CASE status WHEN 'paid' THEN 'Paid' ELSE 'Unpaid' END, date "
                  "FROM purchases ORDER BY date, id"),
    "parts": ("Parts", ["Supplier", "Item name", "Item description", "Store link"],
              "SELECT supplier_name, item_name, description, store_link FROM parts ORDER BY item_name"),
    "customers": ("Customers", ["Name", "Address", "Phone", "Email"],
                  "SELECT name, address, phone, email FROM customers ORDER BY name"),
    "suppliers": ("Suppliers", ["Name", "Address", "Store link"],
                  "SELECT name, address, store_link FROM suppliers ORDER BY name"),
}


def make_csv(name):
    _title, headings, sql = EXPORTS[name]
    out = io.StringIO()
    out.write("\ufeff")  # lets Excel show the money sign and accents correctly
    writer = csv.writer(out)
    writer.writerow(headings)
    writer.writerows(get_db().execute(sql).fetchall())
    return out.getvalue()


@app.get("/export/<name>.csv")
def export_csv(name):
    if name not in EXPORTS:
        abort(404)
    return Response(make_csv(name), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=mycount-{name}-{date.today()}.csv"})


@app.get("/export/all.zip")
def export_all():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in EXPORTS:
            zf.writestr(f"mycount-{name}-{date.today()}.csv", make_csv(name).encode("utf-8"))
    return Response(buffer.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=mycount-all-data-{date.today()}.zip"})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)
