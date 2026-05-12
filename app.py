from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import mysql.connector
from mysql.connector import Error
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from io import BytesIO
import pandas as pd
from flask import send_file
from fpdf import FPDF
import os

app = Flask(__name__)
app.config.from_object(Config)

# Flask-Login setup
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

# Simple User class for Flask-Login
class User(UserMixin):
    def __init__(self, id_, username, password_hash, full_name, role):
        self.id = id_
        self.username = username
        self.password_hash = password_hash
        self.full_name = full_name
        self.role = role

# helper: get DB connection
def get_db_connection():
    cfg = app.config['DB_CONFIG']
    conn = mysql.connector.connect(
        host=cfg['host'],
        user=cfg['user'],
        password=cfg['password'],
        database=cfg['database'],
    )
    return conn

# helper: fetch user by username
def get_user_by_username(username):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return User(row['id'], row['username'], row['password'], row['full_name'], row['role'])
    return None

# helper: fetch user by id
def get_user_by_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return User(row['id'], row['username'], row['password'], row['full_name'], row['role'])
    return None

@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(user_id)

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('mr_dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        user = get_user_by_username(username)
        if user:
            # If stored password is plain (from sample SQL) we check equality first
            # Preferred: store hashed passwords and use check_password_hash
            if user.password_hash == password or check_password_hash(user.password_hash, password):
                login_user(user)
                flash("Logged in successfully.", "success")
                next_page = request.args.get('next')
                return redirect(next_page or url_for('index'))
        flash("Invalid username or password.", "danger")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

# --- Admin routes ---
@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))
    # Show summary: number of MRs, number of reports, latest reports
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, full_name, username FROM users WHERE role='mr'")
    mrs = cursor.fetchall()
    cursor.execute("""
        SELECT r.*, u.full_name as mr_name
        FROM reports r JOIN users u ON r.mr_id = u.id
        ORDER BY r.visit_date DESC LIMIT 50
    """)
    reports = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('admin_dashboard.html', mrs=mrs, reports=reports)

@app.route('/admin/mr_reports', methods=['GET'])
@login_required
def admin_mr_reports():
    if current_user.role != 'admin':
        flash("Unauthorized access.", "danger")
        return redirect(url_for('index'))

    mr_id = request.args.get('mr_id')
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, full_name FROM users WHERE role='mr'")
    mrs = cursor.fetchall()

    reports = []
    if mr_id and mr_id != 'all':
        cursor.execute("""
            SELECT r.*, u.full_name as mr_name
            FROM reports r 
            JOIN users u ON r.mr_id = u.id
            WHERE r.mr_id = %s
            ORDER BY r.visit_date DESC
        """, (mr_id,))
        reports = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template('admin_mr_reports.html', mrs=mrs, reports=reports, selected_mr=mr_id)

@app.route('/admin/create_mr', methods=['GET','POST'])
@login_required
def create_mr():
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username').strip()
        full_name = request.form.get('full_name').strip()
        password = request.form.get('password').strip()
        if not username or not password:
            flash("Username and password required.", "warning")
            return redirect(url_for('create_mr'))
        password_hash = generate_password_hash(password)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (username, password, full_name, role) VALUES (%s, %s, %s, 'mr')",
                           (username, password_hash, full_name))
            conn.commit()
            flash("MR account created.", "success")
        except Error as e:
            flash("Error creating MR: " + str(e), "danger")
        finally:
            cursor.close()
            conn.close()
        return redirect(url_for('admin_dashboard'))
    return render_template('create_mr.html')

# View reports with filters (admin)
@app.route('/admin/reports', methods=['GET'])
@login_required
def admin_view_reports():
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))

    mr_id = request.args.get('mr_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    q = """
        SELECT r.*, u.full_name AS mr_name
        FROM reports r
        JOIN users u ON r.mr_id = u.id
        WHERE 1=1
    """
    params = []

    if mr_id and mr_id != 'all':
        q += " AND r.mr_id=%s"
        params.append(mr_id)
    if start_date:
        q += " AND r.visit_date >= %s"
        params.append(start_date)
    if end_date:
        q += " AND r.visit_date <= %s"
        params.append(end_date)

    q += " ORDER BY r.visit_date DESC"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(q, params)
    reports = cursor.fetchall()

    cursor.execute("SELECT id, full_name FROM users WHERE role='mr'")
    mrs = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template(
        'view_reports.html',
        reports=reports,
        mrs=mrs,
        filter={'mr_id': mr_id, 'start_date': start_date, 'end_date': end_date}
    )
# EDIT or DELETE (admin)
@app.route('/report/edit/<int:report_id>', methods=['GET','POST'])
@login_required
def edit_report(report_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    # fetch report
    cursor.execute("SELECT * FROM reports WHERE id=%s", (report_id,))
    report = cursor.fetchone()
    if not report:
        cursor.close()
        conn.close()
        flash("Report not found.", "warning")
        return redirect(url_for('index'))
    # Authorization: admin or the MR who owns the report
    if current_user.role != 'admin' and int(current_user.id) != int(report['mr_id']):
        cursor.close()
        conn.close()
        flash("Unauthorized to edit this report.", "danger")
        return redirect(url_for('index'))
    if request.method == 'POST':
        doctor_name = request.form.get('doctor_name').strip()
        hospital_name = request.form.get('hospital_name').strip()
        location = request.form.get('location').strip()
        visit_date = request.form.get('visit_date')
        products_promoted = request.form.get('products_promoted')
        remarks = request.form.get('remarks')
        cursor.execute("""
            UPDATE reports SET doctor_name=%s, hospital_name=%s, location=%s, visit_date=%s,
            products_promoted=%s, remarks=%s WHERE id=%s
        """, (doctor_name, hospital_name, location, visit_date, products_promoted, remarks, report_id))
        conn.commit()
        cursor.close()
        conn.close()
        flash("Report updated.", "success")
        return redirect(url_for('index'))
    cursor.close()
    conn.close()
    return render_template('edit_report.html', report=report)

@app.route('/report/delete/<int:report_id>', methods=['POST'])
@login_required
def delete_report(report_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT mr_id FROM reports WHERE id=%s", (report_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        flash("Report not found.", "warning")
        return redirect(url_for('index'))
    mr_id = row[0]
    # Authorization: admin or owner
    if current_user.role != 'admin' and int(current_user.id) != int(mr_id):
        cursor.close()
        conn.close()
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))
    cursor.execute("DELETE FROM reports WHERE id=%s", (report_id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Report deleted.", "info")
    return redirect(url_for('index'))

# --- MR routes ---
@app.route('/mr')
@login_required
def mr_dashboard():
    if current_user.role != 'mr':
        return redirect(url_for('admin_dashboard'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # All Reports
    cursor.execute("SELECT * FROM reports WHERE mr_id=%s ORDER BY visit_date DESC", (current_user.id,))
    reports = cursor.fetchall()

    # Upcoming Visits (Next 7 Days)
    cursor.execute("""
        SELECT * FROM reports 
        WHERE mr_id=%s AND next_visit_date BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 7 DAY)
        ORDER BY next_visit_date
    """, (current_user.id,))
    upcoming_visits = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('mr_dashboard.html', reports=reports, upcoming_visits=upcoming_visits)

@app.route('/mr/add_report', methods=['GET', 'POST'])
@login_required
def add_report():
    if current_user.role != 'mr':
        flash("Only MRs can add reports.", "danger")
        return redirect(url_for('index'))

    if request.method == 'POST':
        doctor_id = request.form.get('doctor_id') or None
        hospital_id = request.form.get('hospital_id') or None
        visit_date = request.form.get('visit_date')
        products_promoted = request.form.get('products_promoted')
        remarks = request.form.get('remarks')
        location = request.form.get('location')

        if not doctor_id or not hospital_id or not visit_date:
            flash("Please select doctor, hospital and date.", "warning")
            return redirect(url_for('add_report'))

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM doctors WHERE id = %s", (doctor_id,))
        doc_row = cursor.fetchone()
        doctor_name = doc_row[0] if doc_row else ''

        cursor.execute("SELECT name FROM hospitals WHERE id = %s", (hospital_id,))
        hosp_row = cursor.fetchone()
        hospital_name = hosp_row[0] if hosp_row else ''

        cursor.execute("""
            INSERT INTO reports (mr_id, doctor_id, hospital_id, doctor_name, hospital_name, location, visit_date, products_promoted, remarks)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (current_user.id, doctor_id, hospital_id, doctor_name, hospital_name, location, visit_date, products_promoted, remarks))

        # app.py (inside add_report POST section)
        next_visit_date = request.form.get('next_visit_date') or None

        cursor.execute("""
            INSERT INTO reports 
            (mr_id, doctor_id, hospital_id, doctor_name, hospital_name, location, visit_date, next_visit_date, products_promoted, remarks)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (current_user.id, doctor_id, hospital_id, doctor_name, hospital_name, location, visit_date, next_visit_date, products_promoted, remarks))      

        conn.commit()
        cursor.close()
        conn.close()

        flash("Report added successfully.", "success")
        return redirect(url_for('mr_dashboard'))

    doctors = get_all_doctors()
    hospitals = get_all_hospitals()
    products = get_all_products()  # ✅ Add this line
    return render_template('add_report.html', doctors=doctors, hospitals=hospitals, products=products)
@app.route('/mr/reports')
@login_required
def mr_view_reports():
    if current_user.role != 'mr':
        return redirect(url_for('index'))

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    q = "SELECT * FROM reports WHERE mr_id=%s"
    params = [current_user.id]

    if start_date:
        q += " AND visit_date >= %s"
        params.append(start_date)
    if end_date:
        q += " AND visit_date <= %s"
        params.append(end_date)

    q += " ORDER BY visit_date DESC"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(q, params)
    reports = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template(
        'view_reports.html',
        reports=reports,
        filter={'start_date': start_date, 'end_date': end_date},
        mrs=None
    )
@app.route('/register_admin', methods=['GET','POST'])
def register_admin():
    # small route to create an admin, recommended to remove after initial use
    if request.method == 'POST':
        username = request.form.get('username').strip()
        full_name = request.form.get('full_name').strip()
        password = request.form.get('password').strip()
        if not username or not password:
            flash("Username and password required", "warning")
            return redirect(url_for('register_admin'))
        password_hash = generate_password_hash(password)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (username, password, full_name, role) VALUES (%s,%s,%s,'admin')", (username, password_hash, full_name))
            conn.commit()
            flash("Admin created. Please login.", "success")
            return redirect(url_for('login'))
        except Error as e:
            flash("Error: " + str(e), "danger")
        finally:
            cursor.close()
            conn.close()
    return render_template('create_mr.html', is_admin_registration=True)


# --- Doctor & Hospital helpers and admin routes ---

# get list of doctors
def get_all_doctors():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM doctors ORDER BY name")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

# get list of hospitals
def get_all_hospitals():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM hospitals ORDER BY name")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

# ---------- DOCTOR ROUTES ----------
@app.route('/admin/doctors')
@login_required
def admin_doctors():
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))
    doctors = get_all_doctors()
    return render_template('admin_doctors.html', doctors=doctors)

@app.route('/admin/doctors/add', methods=['GET','POST'])
@login_required
def admin_doctors_add():
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form.get('name').strip()
        spec = request.form.get('specialization').strip()
        phone = request.form.get('phone').strip()
        email = request.form.get('email').strip()
        if not name:
            flash("Doctor name required.", "warning")
            return redirect(url_for('admin_doctors_add'))
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO doctors (name, specialization, phone, email) VALUES (%s,%s,%s,%s)",
            (name, spec, phone, email)
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash("Doctor added successfully.", "success")
        return redirect(url_for('admin_doctors'))

    return render_template('admin_doctors_add.html')

@app.route('/admin/doctors/edit/<int:doc_id>', methods=['GET','POST'])
@login_required
def admin_doctors_edit(doc_id):
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM doctors WHERE id=%s", (doc_id,))
    doc = cursor.fetchone()

    if not doc:
        cursor.close()
        conn.close()
        flash("Doctor not found.", "warning")
        return redirect(url_for('admin_doctors'))

    if request.method == 'POST':
        name = request.form.get('name').strip()
        spec = request.form.get('specialization').strip()
        phone = request.form.get('phone').strip()
        email = request.form.get('email').strip()
        cursor.execute(
            "UPDATE doctors SET name=%s, specialization=%s, phone=%s, email=%s WHERE id=%s",
            (name, spec, phone, email, doc_id)
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash("Doctor updated successfully.", "success")
        return redirect(url_for('admin_doctors'))

    cursor.close()
    conn.close()
    return render_template('admin_doctors_edit.html', doc=doc)

@app.route('/admin/doctors/delete/<int:doc_id>', methods=['POST'])
@login_required
def admin_doctors_delete(doc_id):
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM doctors WHERE id=%s", (doc_id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Doctor deleted.", "info")
    return redirect(url_for('admin_doctors'))


# ---------- HOSPITAL ROUTES ----------
@app.route('/admin/hospitals')
@login_required
def admin_hospitals():
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))
    hospitals = get_all_hospitals()
    return render_template('admin_hospitals.html', hospitals=hospitals)

@app.route('/admin/hospitals/add', methods=['GET','POST'])
@login_required
def admin_hospitals_add():
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form.get('name').strip()
        address = request.form.get('address').strip()
        city = request.form.get('city').strip()
        phone = request.form.get('phone').strip()
        if not name:
            flash("Hospital name required.", "warning")
            return redirect(url_for('admin_hospitals_add'))
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO hospitals (name, address, city, phone) VALUES (%s,%s,%s,%s)",
            (name, address, city, phone)
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash("Hospital added successfully.", "success")
        return redirect(url_for('admin_hospitals'))

    return render_template('admin_hospitals_add.html')

@app.route('/admin/hospitals/edit/<int:hid>', methods=['GET','POST'])
@login_required
def admin_hospitals_edit(hid):
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM hospitals WHERE id=%s", (hid,))
    h = cursor.fetchone()

    if not h:
        cursor.close()
        conn.close()
        flash("Hospital not found.", "warning")
        return redirect(url_for('admin_hospitals'))

    if request.method == 'POST':
        name = request.form.get('name').strip()
        address = request.form.get('address').strip()
        city = request.form.get('city').strip()
        phone = request.form.get('phone').strip()
        cursor.execute(
            "UPDATE hospitals SET name=%s, address=%s, city=%s, phone=%s WHERE id=%s",
            (name, address, city, phone, hid)
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash("Hospital updated successfully.", "success")
        return redirect(url_for('admin_hospitals'))

    cursor.close()
    conn.close()
    return render_template('admin_hospitals_edit.html', h=h)

@app.route('/admin/hospitals/delete/<int:hid>', methods=['POST'])
@login_required
def admin_hospitals_delete(hid):
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM hospitals WHERE id=%s", (hid,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Hospital deleted.", "info")
    return redirect(url_for('admin_hospitals'))
# ---------- PRODUCT ROUTES ----------
@app.route('/admin/products')
@login_required
def admin_products():
  if current_user.role != 'admin':
    flash("Unauthorized.", "danger")
    return redirect(url_for('index'))
  conn = get_db_connection()
  cursor = conn.cursor(dictionary=True)
  cursor.execute("SELECT * FROM products ORDER BY name")
  products = cursor.fetchall()
  cursor.close(); conn.close()
  return render_template('admin_products.html', products=products)

@app.route('/admin/products/add', methods=['GET','POST'])
@login_required
def admin_products_add():
  if current_user.role != 'admin':
    flash("Unauthorized.", "danger")
    return redirect(url_for('index'))
  if request.method == 'POST':
    name = request.form.get('name').strip()
    desc = request.form.get('description').strip()
    price = request.form.get('price').strip()
    if not name:
      flash("Product name required.", "warning")
      return redirect(url_for('admin_products_add'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO products (name, description, price) VALUES (%s,%s,%s)", (name, desc, price))
    conn.commit()
    cursor.close(); conn.close()
    flash("Product added successfully.", "success")
    return redirect(url_for('admin_products'))
  return render_template('admin_products_add.html')

def get_all_products():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM products ORDER BY name")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

@app.route('/admin/products/edit/<int:pid>', methods=['GET','POST'])
@login_required
def admin_products_edit(pid):
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM products WHERE id=%s", (pid,))
    p = cursor.fetchone()
    if not p:
        cursor.close(); conn.close()
        flash("Product not found.", "warning")
        return redirect(url_for('admin_products'))
    if request.method == 'POST':
        name = request.form.get('name').strip()
        desc = request.form.get('description').strip()
        price = request.form.get('price').strip()
        cursor.execute("UPDATE products SET name=%s, description=%s, price=%s WHERE id=%s", (name, desc, price, pid))
        conn.commit()
        cursor.close(); conn.close()
        flash("Product updated successfully.", "success")
        return redirect(url_for('admin_products'))
    cursor.close(); conn.close()
    return render_template('admin_products_edit.html', p=p)


@app.route('/admin/products/delete/<int:pid>', methods=['POST'])
@login_required
def admin_products_delete(pid):
    if current_user.role != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('index'))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id=%s", (pid,))
    conn.commit()
    cursor.close(); conn.close()
    flash("Product deleted successfully.", "info")
    return redirect(url_for('admin_products'))

@app.route('/mr/export/excel')
@login_required
def export_mr_excel():
    if current_user.role != 'mr':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT visit_date, doctor_name, hospital_name, location, products_promoted, remarks, next_visit_date 
        FROM reports WHERE mr_id=%s ORDER BY visit_date DESC
    """, (current_user.id,))
    data = cursor.fetchall()
    cursor.close()
    conn.close()

    if not data:
        flash("No reports to export!", "warning")
        return redirect(url_for('mr_view_reports'))

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Reports')

    output.seek(0)
    return send_file(output, as_attachment=True, download_name='My_Reports.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/mr/export/pdf')
@login_required
def export_mr_pdf():
    if current_user.role != 'mr':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT visit_date, doctor_name, hospital_name, products_promoted, remarks 
        FROM reports WHERE mr_id=%s ORDER BY visit_date DESC
    """, (current_user.id,))
    reports = cursor.fetchall()
    cursor.close()
    conn.close()

    if not reports:
        flash("No reports to export!", "warning")
        return redirect(url_for('mr_view_reports'))

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(200, 10, txt="MR Daily Reports", ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=11)

    for r in reports:
        pdf.cell(0, 8, txt=f"Date: {r['visit_date']}", ln=True)
        pdf.cell(0, 8, txt=f"Doctor: {r['doctor_name']}", ln=True)
        pdf.cell(0, 8, txt=f"Hospital: {r['hospital_name']}", ln=True)
        pdf.multi_cell(0, 8, txt=f"Products: {r['products_promoted']}")
        pdf.multi_cell(0, 8, txt=f"Remarks: {r['remarks']}")
        pdf.ln(5)

    # 🧩 यहाँ है FIX — Output को string में convert करना
    pdf_bytes = pdf.output(dest='S').encode('latin1')

    return send_file(
        BytesIO(pdf_bytes),
        as_attachment=True,
        download_name='My_Reports.pdf',
        mimetype='application/pdf'
    )

from flask import make_response
import io, csv
from fpdf import FPDF
import pandas as pd
from datetime import datetime

@app.route('/export/pdf')
@login_required
def export_pdf():

    mr_id = request.args.get('mr_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # ---------------------------
    # Query
    # ---------------------------
    query = """
        SELECT r.*, u.full_name AS mr_name
        FROM reports r
        JOIN users u ON r.mr_id = u.id
        WHERE 1=1
    """
    params = []

    if mr_id and mr_id != 'all':
        query += " AND r.mr_id = %s"
        params.append(mr_id)

    if start_date:
        query += " AND r.visit_date >= %s"
        params.append(start_date)

    if end_date:
        query += " AND r.visit_date <= %s"
        params.append(end_date)

    query += " ORDER BY r.visit_date DESC"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(query, params)
    reports = cursor.fetchall()
    cursor.close()
    conn.close()

    if not reports:
        flash("No data found.", "warning")
        return redirect(request.referrer)

    # ---------------------------
    # PDF Design
    # ---------------------------
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "MR VISIT REPORT", ln=True, align="C")
    pdf.ln(5)

    # Filter Info
    pdf.set_font("Arial", size=10)
    pdf.cell(0, 8, f"Generated On: {datetime.now().strftime('%d-%m-%Y %H:%M')}", ln=True)

    if mr_id and mr_id != 'all':
        pdf.cell(0, 8, f"MR ID: {mr_id}", ln=True)

    if start_date or end_date:
        pdf.cell(0, 8, f"Date Range: {start_date or '---'} to {end_date or '---'}", ln=True)

    pdf.ln(5)

    # Table Header
    pdf.set_font("Arial", "B", 10)
    pdf.set_fill_color(220, 220, 220)

    pdf.cell(10, 8, "#", 1, 0, "C", True)
    pdf.cell(30, 8, "Date", 1, 0, "C", True)
    pdf.cell(35, 8, "Doctor", 1, 0, "C", True)
    pdf.cell(35, 8, "Hospital", 1, 0, "C", True)
    pdf.cell(40, 8, "Products", 1, 0, "C", True)
    pdf.cell(40, 8, "Remarks", 1, 1, "C", True)

    # Table Body
    pdf.set_font("Arial", size=9)

    for i, r in enumerate(reports, start=1):
        pdf.cell(10, 8, str(i), 1)
        pdf.cell(30, 8, str(r['visit_date']), 1)
        pdf.cell(35, 8, r['doctor_name'][:15], 1)
        pdf.cell(35, 8, r['hospital_name'][:15], 1)
        pdf.cell(40, 8, r['products_promoted'][:18], 1)
        pdf.cell(40, 8, r['remarks'][:18], 1)
        pdf.ln()

    pdf.ln(5)

    # Footer
    pdf.set_font("Arial", "I", 8)
    pdf.cell(0, 8, "Generated by MR Reporting System", align="C")

    response = make_response(pdf.output(dest='S').encode('latin-1'))
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename=MR_Report.pdf'

    return response

@app.route('/export/excel')
@login_required
def export_excel():

    mr_id = request.args.get('mr_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # ---------------------------
    # Build Query
    # ---------------------------
    query = """
        SELECT 
            u.full_name AS MR_Name,
            r.visit_date,
            r.doctor_name,
            r.hospital_name,
            r.products_promoted,
            r.remarks
        FROM reports r
        JOIN users u ON r.mr_id = u.id
        WHERE 1=1
    """

    params = []

    if mr_id and mr_id != 'all':
        query += " AND r.mr_id = %s"
        params.append(mr_id)

    if start_date:
        query += " AND r.visit_date >= %s"
        params.append(start_date)

    if end_date:
        query += " AND r.visit_date <= %s"
        params.append(end_date)

    query += " ORDER BY r.visit_date DESC"

    # ---------------------------
    # Fetch Data
    # ---------------------------
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(query, params)
    reports = cursor.fetchall()
    cursor.close()
    conn.close()

    if not reports:
        flash("No data found for selected filters.", "warning")
        return redirect(request.referrer or url_for('admin_mr_reports'))

    # ---------------------------
    # Create Excel
    # ---------------------------
    df = pd.DataFrame(reports)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='MR Reports')

    output.seek(0)

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=MR_Reports.xlsx"
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return response

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':
        full_name = request.form.get('full_name').strip()
        email = request.form.get('email').strip()
        phone = request.form.get('phone').strip()
        new_password = request.form.get('password').strip()

        # Profile Image Upload
        # Profile Image Upload
        profile_img = request.files.get('profile_img')
        img_filename = None
        if profile_img and profile_img.filename:
             img_filename = f"profile_{current_user.id}.jpg"
        upload_dir = os.path.join('static', 'uploads')
        os.makedirs(upload_dir, exist_ok=True)  # ✅ अगर folder नहीं है तो बना देगा
        upload_path = os.path.join(upload_dir, img_filename)
        profile_img.save(upload_path)
        # Update Query
        if new_password:
            password_hash = generate_password_hash(new_password)
            cursor.execute("""
                UPDATE users SET full_name=%s, email=%s, phone=%s, password=%s, profile_img=%s WHERE id=%s
            """, (full_name, email, phone, password_hash, img_filename, current_user.id))
        else:
            cursor.execute("""
                UPDATE users SET full_name=%s, email=%s, phone=%s, profile_img=%s WHERE id=%s
            """, (full_name, email, phone, img_filename, current_user.id))

        conn.commit()
        flash("Profile updated successfully!", "success")
        cursor.close()
        conn.close()
        return redirect(url_for('profile'))

    # Fetch current user details
    cursor.execute("SELECT * FROM users WHERE id=%s", (current_user.id,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    return render_template('profile.html', user=user)

@app.route('/admin/add_task', methods=['GET', 'POST'])
@login_required
def admin_add_task():
    if current_user.role != 'admin':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # MRs की list भेजना dropdown के लिए
    cursor.execute("SELECT id, full_name FROM users WHERE role='mr'")
    mrs = cursor.fetchall()

    if request.method == 'POST':
        mr_id = request.form.get('mr_id')
        title = request.form.get('title')
        description = request.form.get('description')
        task_date = request.form.get('task_date') or date.today()

        cursor.execute(
            "INSERT INTO tasks (mr_id, admin_id, title, description, task_date) VALUES (%s, %s, %s, %s, %s)",
            (mr_id, current_user.id, title, description, task_date)
        )
        conn.commit()
        flash("✅ Task assigned successfully!", "success")
        return redirect(url_for('admin_view_tasks'))

    cursor.close()
    conn.close()
    return render_template('admin_add_task.html', mrs=mrs, today=date.today())

@app.route('/admin/view_tasks')
@login_required
def admin_view_tasks():
    if current_user.role != 'admin':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT t.*, u.full_name AS mr_name 
        FROM tasks t
        JOIN users u ON t.mr_id = u.id
        ORDER BY t.task_date DESC
    """)

    tasks = cursor.fetchall()

    # 🔴 unread per task
    for t in tasks:
        cursor.execute("""
            SELECT COUNT(*) AS total FROM task_messages
            WHERE task_id=%s AND sender_id != %s AND is_read=0
        """, (t['id'], current_user.id))

        row = cursor.fetchone()
        t['unread'] = row['total']   # ✅ FIX

    cursor.close()
    conn.close()

    return render_template('admin_view_tasks.html', tasks=tasks)

from datetime import date

@app.route('/mr/tasks')
@login_required
def mr_tasks():
    if current_user.role != 'mr':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM tasks 
        WHERE mr_id=%s 
        ORDER BY task_date DESC
    """, (current_user.id,))
    
    tasks = cursor.fetchall()

    # 🔴 unread count per task
    for t in tasks:
        cursor.execute("""
            SELECT COUNT(*) AS total FROM task_messages
            WHERE task_id=%s AND sender_id != %s AND is_read=0
        """, (t['id'], current_user.id))

        row = cursor.fetchone()
        t['unread'] = row['total']   # ✅ FIX

    cursor.close()
    conn.close()

    return render_template('mr_tasks.html', tasks=tasks)

@app.route('/mr/complete_task/<int:task_id>', methods=['POST'])
@login_required
def complete_task(task_id):
    if current_user.role != 'mr':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))

    reply = request.form.get('reply', '').strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE tasks 
        SET status='completed', reply=%s 
        WHERE id=%s AND mr_id=%s
    """, (reply, task_id, current_user.id))
    conn.commit()
    cursor.close()
    conn.close()

    flash("✅ Reply sent and task marked as completed!", "success")
    return redirect(url_for('mr_tasks'))

@app.route('/admin/task_history', methods=['GET'])
@login_required
def admin_task_history():
    if current_user.role != 'admin':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('index'))

    mr_id = request.args.get('mr_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    status = request.args.get('status')

    q = """
        SELECT t.*, u.full_name AS mr_name 
        FROM tasks t 
        JOIN users u ON t.mr_id = u.id 
        WHERE 1=1
    """
    params = []

    if mr_id and mr_id != 'all':
        q += " AND t.mr_id = %s"
        params.append(mr_id)
    if start_date:
        q += " AND t.task_date >= %s"
        params.append(start_date)
    if end_date:
        q += " AND t.task_date <= %s"
        params.append(end_date)
    if status and status != 'all':
        q += " AND t.status = %s"
        params.append(status)

    q += " ORDER BY t.task_date DESC"

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, full_name FROM users WHERE role='mr'")
    mrs = cursor.fetchall()

    cursor.execute(q, params)
    tasks = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('admin_task_history.html',
                           tasks=tasks,
                           mrs=mrs,
                           filter={'mr_id': mr_id, 'start_date': start_date, 'end_date': end_date, 'status': status})


@app.route('/task_chat/<int:task_id>')
@login_required
def task_chat(task_id):

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 🔐 check: MR को सिर्फ उसका task दिखे
    if current_user.role == 'mr':
        cursor.execute("SELECT * FROM tasks WHERE id=%s AND mr_id=%s", (task_id, current_user.id))
        task = cursor.fetchone()

        if not task:
            flash("Unauthorized access!", "danger")
            return redirect(url_for('mr_tasks'))

    # 🔵 mark messages as read
    cursor.execute("""
        UPDATE task_messages
        SET is_read = 1
        WHERE task_id=%s AND sender_id != %s
    """, (task_id, current_user.id))

    conn.commit()

    # 🟢 messages fetch (IMPORTANT FIX)
    cursor.execute("""
        SELECT tm.*, u.full_name
        FROM task_messages tm
        JOIN users u ON tm.sender_id = u.id
        WHERE tm.task_id=%s
        ORDER BY tm.created_at ASC   -- ✅ timestamp fix
    """, (task_id,))

    messages = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("task_chat.html", messages=messages, task_id=task_id)
@app.route('/send_task_message/<int:task_id>', methods=['POST'])
@login_required
def send_task_message(task_id):

    message = request.form.get('message')

    if not message:
        flash("Message empty नहीं हो सकता", "danger")
        return redirect(url_for('task_chat', task_id=task_id))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO task_messages (task_id, sender_id, message, is_read)
        VALUES (%s,%s,%s,0)
    """, (task_id, current_user.id, message))

    conn.commit()

    cursor.close()
    conn.close()

    return redirect(url_for('task_chat', task_id=task_id))


@app.context_processor
def inject_unread_count():
    if current_user.is_authenticated:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM task_messages 
            WHERE is_read = 0 AND sender_id != %s
        """, (current_user.id,))

        count = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        return dict(unread_count=count)

    return dict(unread_count=0)




if __name__ == "__main__":
    app.run(debug=True)