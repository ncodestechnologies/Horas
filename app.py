#!/usr/bin/env python3
"""
Meu Controle de Horas — Sistema Pessoal
100% Python + Flask + SQLite + HTML5 + CSS3 (ZERO JAVASCRIPT)
"""
import os
import sys
import calendar
import argparse
import json
import base64
import time
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_file, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from itsdangerous import URLSafeTimedSerializer

class PartitionedCookieMiddleware:
    """WSGI Middleware para garantir cookies compatíveis com iframes particionados no Chrome/Safari."""
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        def custom_start_response(status, headers, exc_info=None):
            new_headers = []
            for name, val in headers:
                if name.lower() == 'set-cookie':
                    parts = [p.strip().lower() for p in val.split(';')]
                    if not any(p.startswith('samesite=') for p in parts):
                        val += '; SameSite=None'
                    if 'secure' not in parts:
                        val += '; Secure'
                    if 'partitioned' not in parts:
                        val += '; Partitioned'
                new_headers.append((name, val))
            return start_response(status, new_headers, exc_info)
        return self.wsgi_app(environ, custom_start_response)

from database.db import get_db, init_db, reset_all_data
from services.cpf_validator import validate_cpf, format_cpf, clean_cpf
from services.calculations import (
    time_to_minutes,
    minutes_to_time_str,
    minutes_to_balance_str,
    calculate_day_metrics,
    validate_periods
)
from services.reports import (
    get_user_settings,
    generate_report_summary,
    get_cumulative_bank_balance,
    get_bank_debit_breakdown,
    get_days_data_in_range
)
from services.exports import export_to_csv, export_to_excel, export_to_pdf
from services.firebase_service import (
    load_config,
    get_firebase_status,
    sync_settings_to_firebase,
    sync_work_day_to_firebase,
    delete_work_day_from_firebase,
    reset_user_data_in_firebase,
    sync_all_local_to_firebase,
    pull_from_firebase,
    sync_user_profile_to_firebase,
    pull_user_profile_from_firebase
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)
app.wsgi_app = PartitionedCookieMiddleware(ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1))

app.secret_key = os.environ.get('SECRET_KEY', 'controle-horas-secret-key-prod-2026')
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_NAME'] = 'horas_session'

auth_serializer = URLSafeTimedSerializer(app.secret_key, salt='horas-auth-session')

def generate_auth_token(user_id, email):
    return auth_serializer.dumps({'uid': user_id, 'email': email})

def verify_auth_token(token, max_age=86400 * 7):
    try:
        return auth_serializer.loads(token, max_age=max_age)
    except Exception:
        return None

def restore_session_from_token():
    token = request.args.get('auth_token') or request.headers.get('X-Auth-Token')
    if token:
        data = verify_auth_token(token)
        if data and data.get('uid'):
            try:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE id = ?", (data['uid'],))
                user = cursor.fetchone()
                conn.close()
                if user:
                    user_name = user['name']
                    user_cpf = user['cpf'] if 'cpf' in user.keys() else None
                    # Se o nome ainda for genérico/vazio ou não tiver CPF, busca da nuvem Firestore
                    if not user_cpf or user_name in ('Taijou BR', 'Administrador', None, ''):
                        try:
                            prof = pull_user_profile_from_firebase(user_id=user['id'], email=user['email'])
                            if prof:
                                fb_name = prof.get('name')
                                fb_cpf = prof.get('cpf')
                                if fb_name or fb_cpf:
                                    conn_up = get_db()
                                    conn_up.execute("""
                                        UPDATE users 
                                        SET name = COALESCE(NULLIF(?, ''), name),
                                            cpf = COALESCE(NULLIF(?, ''), cpf)
                                        WHERE id = ?
                                    """, (fb_name, fb_cpf, user['id']))
                                    conn_up.commit()
                                    conn_up.close()
                                    if fb_name:
                                        user_name = fb_name
                        except Exception:
                            pass
                    session['user_id'] = user['id']
                    session['user_email'] = user['email']
                    session['user_name'] = user_name or user['email'].split('@')[0]
                    session['user_role'] = user['role'] or ('admin' if user['email'] in ('p.nikolas3@gmail.com', 'ncodestechnologies@gmail.com') else 'user')
                    session['logged_out'] = False
                    # Sincroniza dados com a nuvem Firestore automaticamente
                    ensure_user_synced_from_firebase(user['id'], min_interval=5)
                    return True
            except Exception:
                pass
    return False

# Cache de controle de sincronização para instâncias serverless (Vercel)
_user_last_firebase_pull = {}

def ensure_user_synced_from_firebase(user_id, min_interval=5, force=False):
    """
    Garante que os registros de dias, banco de horas e perfil no SQLite
    estejam sempre sincronizados de forma 100% automática com a nuvem Firebase Firestore.
    Essencial para o Vercel Serverless, onde instâncias são efêmeras e independentes.
    """
    global _user_last_firebase_pull
    now = time.time()
    last = _user_last_firebase_pull.get(user_id, 0)
    if force or (now - last > min_interval):
        _user_last_firebase_pull[user_id] = now
        try:
            conn = get_db()
            pull_from_firebase(user_id, conn)
            conn.close()
        except Exception as e:
            print(f"Aviso no auto-sync do Firebase para user_id {user_id}: {e}")

# Inicializa o banco de dados na inicialização do app (essencial para ambientes serverless como Vercel)
try:
    init_db()
except Exception as _db_init_err:
    print(f"Aviso na inicialização do banco: {_db_init_err}")

MONTH_NAMES_PT = {
    1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
    5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
    9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
}

WEEKDAY_NAMES_PT = {
    0: 'Segunda-feira', 1: 'Terça-feira', 2: 'Quarta-feira',
    3: 'Quinta-feira', 4: 'Sexta-feira', 5: 'Sábado', 6: 'Domingo'
}

def get_default_system_user():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, email, name, role FROM users WHERE LOWER(email) = 'p.nikolas3@gmail.com' LIMIT 1")
    user = cursor.fetchone()
    if not user:
        cursor.execute("SELECT id, email, name, role FROM users ORDER BY id ASC LIMIT 1")
        user = cursor.fetchone()
    conn.close()
    return user

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            if not restore_session_from_token():
                return redirect(url_for('login'))
        uid = session.get('user_id')
        if uid and request.method == 'GET':
            ensure_user_synced_from_firebase(uid, min_interval=5)
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def before_req():
    if not session.get('user_id'):
        restore_session_from_token()
    else:
        # Garante que o nome do usuário na sessão reflita o nome completo e atualizado
        if session.get('user_name') in ('Taijou BR', 'Administrador', None, ''):
            uid = session.get('user_id')
            try:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM users WHERE id = ?", (uid,))
                row = cursor.fetchone()
                conn.close()
                if row and row['name'] and row['name'] not in ('Taijou BR', 'Administrador'):
                    session['user_name'] = row['name']
            except Exception:
                pass

@app.context_processor
def inject_auth_info():
    token = ''
    if session.get('user_id') and session.get('user_email'):
        try:
            token = generate_auth_token(session['user_id'], session['user_email'])
        except Exception:
            token = ''
    return {'auth_token': token}

@app.after_request
def after_req(response):
    # Assegura que cabeçalhos de cookie sejam compatíveis com navegadores dentro de iframes
    cookies = response.headers.getlist('Set-Cookie')
    if cookies:
        new_cookies = []
        for c in cookies:
            if 'SameSite' not in c:
                c += '; SameSite=None'
            if 'Secure' not in c:
                c += '; Secure'
            if 'Partitioned' not in c:
                c += '; Partitioned'
            new_cookies.append(c)
        response.headers.setlist('Set-Cookie', new_cookies)
    return response

# ==============================================================================
# AUTENTICAÇÃO E CADASTRO
# ==============================================================================

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "app": "meu-controle-de-horas"}), 200

@app.route('/cadastro', methods=['GET', 'POST'], endpoint='register_user')
def register_user():
    # Se já logado, vai direto ao painel
    if session.get('user_id'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        cpf = (request.form.get('cpf') or '').strip()
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        if not email or '@' not in email:
            flash("Por favor, informe um endereço de e-mail válido.", "danger")
            return render_template('register_user.html', name=name, email=email, cpf=cpf, active_page='login')

        # Validador de CPF da Receita Federal (opcional no cadastro, validado quando informado)
        formatted_cpf = None
        if cpf:
            cleaned_cpf = clean_cpf(cpf)
            if len(cleaned_cpf) == 11:
                if not validate_cpf(cleaned_cpf):
                    flash("Aviso: Os dígitos do CPF não conferem com o cálculo oficial da Receita Federal, mas seu cadastro foi concluído com sucesso. Você poderá atualizá-lo nas configurações.", "warning")
                formatted_cpf = format_cpf(cleaned_cpf)
            elif len(cleaned_cpf) > 0:
                flash("O CPF deve conter 11 dígitos ou pode ser deixado em branco para preenchimento posterior.", "danger")
                return render_template('register_user.html', name=name, email=email, cpf=cpf, active_page='login')

        if not password or password != confirm_password:
            flash("A senha e a confirmação de senha não coincidem.", "danger")
            return render_template('register_user.html', name=name, email=email, cpf=formatted_cpf or cpf, active_page='login')

        if len(password) < 4:
            flash("A senha deve conter pelo menos 4 caracteres.", "danger")
            return render_template('register_user.html', name=name, email=email, cpf=formatted_cpf or cpf, active_page='login')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, role, name, cpf FROM users WHERE LOWER(email) = ?", (email,))
        existing = cursor.fetchone()
        new_hash = generate_password_hash(password)

        if existing:
            cursor.execute("""
                UPDATE users 
                SET password_hash = ?, 
                    name = COALESCE(?, name), 
                    cpf = COALESCE(?, cpf) 
                WHERE id = ?
            """, (new_hash, name or None, formatted_cpf, existing['id']))
            conn.commit()
            user_id = existing['id']
            user_role = existing['role'] or ('admin' if email in ('p.nikolas3@gmail.com', 'ncodestechnologies@gmail.com') else 'user')
            user_display_name = name or existing['name'] or email.split('@')[0]
        else:
            role = 'admin' if email in ('p.nikolas3@gmail.com', 'ncodestechnologies@gmail.com') else 'user'
            cursor.execute("""
                INSERT INTO users (email, password_hash, name, cpf, role) 
                VALUES (?, ?, ?, ?, ?)
            """, (email, new_hash, name or email.split('@')[0], formatted_cpf, role))
            user_id = cursor.lastrowid
            user_role = role
            user_display_name = name or email.split('@')[0]
            cursor.execute("""
                INSERT INTO settings (user_id, daily_hours_minutes, default_start, default_break_start, default_break_end, default_end, tolerance_minutes, bank_active, initial_balance_minutes)
                VALUES (?, 480, '08:00', '12:00', '13:00', '17:00', 10, 1, 0)
            """, (user_id,))
            conn.commit()

            # Sincroniza configurações no Firebase Firestore
            try:
                sync_settings_to_firebase(user_id, {
                    'daily_hours_minutes': 480,
                    'default_start': '08:00',
                    'default_break_start': '12:00',
                    'default_break_end': '13:00',
                    'default_end': '17:00',
                    'tolerance_minutes': 10,
                    'bank_active': 1,
                    'initial_balance_minutes': 0
                })
            except Exception:
                pass

        conn.close()

        session.clear()
        session['user_id'] = user_id
        session['user_email'] = email
        session['user_name'] = user_display_name
        session['user_role'] = user_role
        session['logged_out'] = False
        flash(f"Conta criada com sucesso! Bem-vindo(a), {user_display_name}!", "success")
        return redirect(url_for('dashboard'))

    return render_template('register_user.html', active_page='login')

@app.route('/', methods=['GET', 'POST'], endpoint='root_page')
@app.route('/login', methods=['GET', 'POST'], endpoint='login')
def login():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        pwd_clean = password.strip()

        admin_emails = ('p.nikolas3@gmail.com', 'ncodestechnologies@gmail.com')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,))
        user = cursor.fetchone()

        # Se for um dos emails administrativos e ainda não existir no banco, auto-cria como admin
        if not user and email in admin_emails:
            admin_name = "Taijou BR" if email == 'p.nikolas3@gmail.com' else "Administrador"
            cursor.execute("""
                INSERT INTO users (email, password_hash, name, role) 
                VALUES (?, ?, ?, 'admin')
            """, (email, generate_password_hash("Taijou13"), admin_name))
            uid = cursor.lastrowid
            cursor.execute("""
            INSERT INTO settings (user_id, daily_hours_minutes, saturday_hours_minutes, default_start, default_break_start, default_break_end, default_end, default_saturday_start, default_saturday_end, saturday_has_break, tolerance_minutes, bank_active, initial_balance_minutes)
            VALUES (?, 480, 240, '08:00', '12:00', '13:00', '17:00', '08:00', '12:00', 0, 10, 1, 0)
            """, (uid,))
            conn.commit()
            cursor.execute("SELECT * FROM users WHERE id = ?", (uid,))
            user = cursor.fetchone()

        conn.close()

        # Validação de senha: hash armazenado, ou fallback Taijou13 para administradores
        pwd_match = False
        if user and user['password_hash']:
            if check_password_hash(user['password_hash'], password) or check_password_hash(user['password_hash'], pwd_clean):
                pwd_match = True
            elif email in admin_emails and (pwd_clean == 'Taijou13' or pwd_clean.lower() == 'taijou13'):
                pwd_match = True
                try:
                    conn = get_db()
                    conn.cursor().execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash("Taijou13"), user['id']))
                    conn.commit()
                    conn.close()
                except Exception:
                    pass

        if user and pwd_match:
            session.clear()
            session['user_id'] = user['id']
            session['user_email'] = user['email']
            user_name = user['name']

            # Sincroniza dados completos e perfil do Firebase Firestore
            try:
                ensure_user_synced_from_firebase(user['id'], force=True)
                # Recarrega nome atualizado
                conn_name = get_db()
                c_name = conn_name.cursor()
                c_name.execute("SELECT name FROM users WHERE id = ?", (user['id'],))
                u_updated = c_name.fetchone()
                conn_name.close()
                if u_updated and u_updated['name']:
                    user_name = u_updated['name']
            except Exception:
                pass

            session['user_name'] = user_name or user['email'].split('@')[0]
            session['user_role'] = user['role'] or ('admin' if user['email'] in admin_emails else 'user')
            session['logged_out'] = False
            token = generate_auth_token(user['id'], user['email'])
            flash(f"Bem-vindo(a), {session['user_name']}!", "success")
            return redirect(url_for('dashboard', auth_token=token))
        else:
            if not user:
                flash(f"O e-mail '{email}' não foi encontrado. Se ainda não possui cadastro, clique em 'Criar Nova Conta' abaixo.", "danger")
            else:
                flash("Senha incorreta. Certifique-se de digitar a senha com as letras maiúsculas e minúsculas corretas. A senha do administrador é Taijou13.", "danger")

    if session.get('user_id') and not request.args.get('force'):
        return redirect(url_for('dashboard'))

    return render_template('login.html', active_page='login')

@app.route('/api/auth/google', methods=['POST'])
def auth_google():
    """Autenticação e criação de conta via Conta Google."""
    data = request.get_json(silent=True) or {}
    credential = data.get('credential')
    email = data.get('email')
    name = data.get('name')
    google_id = data.get('google_id')
    avatar_url = data.get('avatar_url')

    if credential:
        try:
            parts = credential.split('.')
            if len(parts) >= 2:
                padded = parts[1] + '=' * (-len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(padded).decode('utf-8'))
                email = payload.get('email', email)
                name = payload.get('name', name)
                google_id = payload.get('sub', google_id)
                avatar_url = payload.get('picture', avatar_url)
        except Exception as e:
            app.logger.warning(f"Erro ao decodificar token JWT do Google: {e}")

    email = (email or '').strip().lower()
    if not email or '@' not in email:
        return jsonify({'success': False, 'error': 'E-mail do Google inválido ou não informado.'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,))
    user = cursor.fetchone()

    if user:
        user_id = user['id']
        role = user['role'] or ('admin' if email in ('p.nikolas3@gmail.com', 'ncodestechnologies@gmail.com') else 'user')
        cursor.execute("""
            UPDATE users 
            SET google_id = COALESCE(google_id, ?), 
                avatar_url = COALESCE(avatar_url, ?),
                name = COALESCE(name, ?)
            WHERE id = ?
        """, (google_id, avatar_url, name, user_id))
        conn.commit()
    else:
        role = 'admin' if email in ('p.nikolas3@gmail.com', 'ncodestechnologies@gmail.com') else 'user'
        cursor.execute("""
            INSERT INTO users (email, name, role, google_id, avatar_url)
            VALUES (?, ?, ?, ?, ?)
        """, (email, name or email.split('@')[0], role, google_id, avatar_url))
        user_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO settings (user_id, daily_hours_minutes, default_start, default_break_start, default_break_end, default_end, tolerance_minutes, bank_active, initial_balance_minutes)
            VALUES (?, 480, '08:00', '12:00', '13:00', '17:00', 10, 1, 0)
        """, (user_id,))
        conn.commit()

        try:
            sync_settings_to_firebase(user_id, {
                'daily_hours_minutes': 480,
                'default_start': '08:00',
                'default_break_start': '12:00',
                'default_break_end': '13:00',
                'default_end': '17:00',
                'tolerance_minutes': 10,
                'bank_active': 1,
                'initial_balance_minutes': 0
            })
        except Exception:
            pass

    conn.close()

    session.clear()
    session['user_id'] = user_id
    session['user_email'] = email
    session['user_name'] = name or email.split('@')[0]
    session['user_role'] = role
    session['logged_out'] = False

    flash(f"Conectado com sucesso através da Conta Google ({email})!", "success")
    return jsonify({'success': True, 'redirect': url_for('dashboard')})

@app.route('/api/validate-cpf', methods=['POST'])
def api_validate_cpf():
    """Validador de CPF da Receita Federal via API JSON."""
    data = request.get_json(silent=True) or {}
    cpf = data.get('cpf', '')
    is_valid = validate_cpf(cpf)
    return jsonify({
        'valid': is_valid,
        'formatted': format_cpf(cpf) if is_valid else cpf,
        'message': 'CPF autêntico e válido' if is_valid else 'CPF inválido pelo cálculo oficial da Receita Federal'
    })

@app.route('/recuperar-senha', methods=['GET', 'POST'])
def recover_password():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        new_password = request.form.get('new_password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        if not new_password or new_password != confirm_password:
            flash("As senhas informadas não coincidem.", "danger")
            return render_template('recover.html', active_page='login')

        if len(new_password) < 4:
            flash("A senha deve conter pelo menos 4 caracteres.", "danger")
            return render_template('recover.html', active_page='login')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,))
        user = cursor.fetchone()

        if user:
            new_hash = generate_password_hash(new_password)
            cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user['id']))
            conn.commit()
            conn.close()
            flash("Senha alterada com sucesso! Você já pode entrar com a nova senha.", "success")
            return redirect(url_for('login'))
        else:
            conn.close()
            flash("Nenhuma conta encontrada com o e-mail informado.", "danger")

    return render_template('recover.html', active_page='login')

@app.route('/logout')
def logout():
    session.clear()
    session['logged_out'] = True
    flash("Sessão encerrada com segurança.", "info")
    return redirect(url_for('login', force=1))

# ==============================================================================
# DASHBOARD
# ==============================================================================

@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    
    # Nome do dia de hoje formatado em português
    today_display = f"{WEEKDAY_NAMES_PT[now.weekday()]}, {now.day:02d} de {MONTH_NAMES_PT[now.month]} de {now.year}"
    current_month_display = f"{MONTH_NAMES_PT[now.month]} de {now.year}"

    # Dados de Hoje
    today_days, settings = get_days_data_in_range(user_id, today_str, today_str)
    if today_days:
        today_metrics = today_days[0]
    else:
        # Se hoje ainda não tem registro
        today_metrics = calculate_day_metrics([], settings.get('daily_hours_minutes', 480), 'trabalho', settings.get('tolerance_minutes', 10))

    # Dados do Mês Atual
    start_of_month = f"{now.year}-{now.month:02d}-01"
    _, last_day_num = calendar.monthrange(now.year, now.month)
    end_of_month = f"{now.year}-{now.month:02d}-{last_day_num:02d}"

    month_summary = generate_report_summary(user_id, start_of_month, end_of_month)

    # Saldo acumulado do banco de horas
    cumulative_bank_mins, cumulative_bank_str = get_cumulative_bank_balance(user_id)

    # Últimos registros recentes (até 10)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT wd.id, wd.date, wd.day_type, wd.observation
        FROM work_days wd
        WHERE wd.user_id = ?
        ORDER BY wd.date DESC
        LIMIT 10
    """, (user_id,))
    recent_rows = cursor.fetchall()
    conn.close()

    recent_days = []
    for r in recent_rows:
        d_date = r['date']
        d_days, _ = get_days_data_in_range(user_id, d_date, d_date)
        if d_days:
            item = d_days[0]
            parts = item['date'].split('-')
            item['date_fmt'] = f"{parts[2]}/{parts[1]}/{parts[0]}"
            periods = item.get('periods', [])
            item['first_in'] = periods[0]['start_time'] if len(periods) > 0 else '-'
            item['break_out'] = periods[0]['end_time'] if len(periods) > 1 else ('Sem int.' if len(periods) == 1 else '-')
            item['break_in'] = periods[1]['start_time'] if len(periods) > 1 else ('Sem int.' if len(periods) == 1 else '-')
            item['last_out'] = periods[-1]['end_time'] if len(periods) > 0 else '-'
            recent_days.append(item)

    bank_breakdown = get_bank_debit_breakdown(user_id)

    return render_template(
        'dashboard.html',
        active_page='dashboard',
        today_str=today_str,
        today_display=today_display,
        current_month_display=current_month_display,
        today_metrics=today_metrics,
        month_summary=month_summary,
        cumulative_bank_mins=cumulative_bank_mins,
        cumulative_bank_str=cumulative_bank_str,
        bank_breakdown=bank_breakdown,
        recent_days=recent_days
    )

# ==============================================================================
# REGISTRO DE HORÁRIOS & MÚLTIPLOS PERÍODOS
# ==============================================================================

@app.route('/registro', methods=['GET', 'POST'])
@app.route('/registrar', methods=['GET', 'POST'])
@login_required
def register_time():
    user_id = session['user_id']
    settings = get_user_settings(user_id)

    if request.method == 'POST':
        date_val = (request.form.get('date') or '').strip()
        day_type = request.form.get('day_type') or 'trabalho'
        observation = (request.form.get('observation') or '').strip()
        p1_s = (request.form.get('p1_start') or '').strip()
        p1_e = (request.form.get('p1_end') or '').strip()
        p2_s = (request.form.get('p2_start') or '').strip()
        p2_e = (request.form.get('p2_end') or '').strip()
        p3_s = (request.form.get('p3_start') or '').strip()
        p3_e = (request.form.get('p3_end') or '').strip()
        p4_s = (request.form.get('p4_start') or '').strip()
        p4_e = (request.form.get('p4_end') or '').strip()
        overtime_paid = 1 if request.form.get('overtime_paid') in ('1', 'on', 'true') else 0

        # Detecção automática inteligente de intervalo / jornada contínua:
        # Se o usuário não colocou intervalo (preencheu apenas entrada e saída no período 1,
        # ou preencheu entrada na 1ª caixa e saída na última sem intervalo intermediário),
        # o sistema detecta automaticamente a jornada única.
        if p1_s and p2_e and not p1_e and not p2_s:
            raw_periods = [(p1_s, p2_e)]
            if p3_s or p3_e:
                raw_periods.append((p3_s, p3_e))
            if p4_s or p4_e:
                raw_periods.append((p4_s, p4_e))
        elif p1_s and p1_e and not p2_s and not p2_e and not p3_s and not p4_s:
            raw_periods = [(p1_s, p1_e)]
        else:
            raw_periods = [
                (p1_s, p1_e),
                (p2_s, p2_e),
                (p3_s, p3_e),
                (p4_s, p4_e),
            ]

        if not date_val:
            flash("A data é obrigatória.", "danger")
            return redirect(url_for('register_time'))

        # Validação de períodos
        if day_type == 'trabalho':
            is_valid, err_msg, cleaned_periods = validate_periods(raw_periods)
            if not is_valid:
                flash(err_msg, "danger")
                form_data = {
                    'date': date_val,
                    'day_type': day_type,
                    'observation': observation,
                    'overtime_paid': bool(overtime_paid),
                    'no_interval': False,
                    'p1_start': request.form.get('p1_start', ''),
                    'p1_end': request.form.get('p1_end', ''),
                    'p2_start': request.form.get('p2_start', ''),
                    'p2_end': request.form.get('p2_end', ''),
                    'p3_start': request.form.get('p3_start', ''),
                    'p3_end': request.form.get('p3_end', ''),
                    'p4_start': request.form.get('p4_start', ''),
                    'p4_end': request.form.get('p4_end', ''),
                }
                return render_template('register.html', is_edit=False, form_data=form_data, default_settings=settings, active_page='register')

            if not cleaned_periods:
                flash("Para dias de trabalho, informe ao menos o horário de entrada para registrar.", "danger")
                form_data = {
                    'date': date_val,
                    'day_type': day_type,
                    'observation': observation,
                    'overtime_paid': bool(overtime_paid),
                    'no_interval': False,
                    'p1_start': request.form.get('p1_start', ''),
                    'p1_end': request.form.get('p1_end', ''),
                    'p2_start': request.form.get('p2_start', ''),
                    'p2_end': request.form.get('p2_end', ''),
                    'p3_start': request.form.get('p3_start', ''),
                    'p3_end': request.form.get('p3_end', ''),
                    'p4_start': request.form.get('p4_start', ''),
                    'p4_end': request.form.get('p4_end', ''),
                }
                return render_template('register.html', is_edit=False, form_data=form_data, default_settings=settings, active_page='register')
        else:
            cleaned_periods = []

        conn = get_db()
        cursor = conn.cursor()

        # Verifica se já existe registro nesta data
        cursor.execute("SELECT id FROM work_days WHERE user_id = ? AND date = ?", (user_id, date_val))
        existing = cursor.fetchone()

        if existing:
            day_id = existing['id']
            cursor.execute("UPDATE work_days SET day_type = ?, observation = ?, overtime_paid = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                           (day_type, observation, overtime_paid, day_id))
            cursor.execute("DELETE FROM work_periods WHERE work_day_id = ?", (day_id,))
        else:
            cursor.execute("INSERT INTO work_days (user_id, date, day_type, observation, overtime_paid) VALUES (?, ?, ?, ?, ?)",
                           (user_id, date_val, day_type, observation, overtime_paid))
            day_id = cursor.lastrowid

        for idx, (st, et) in enumerate(cleaned_periods, start=1):
            cursor.execute("INSERT INTO work_periods (work_day_id, period_order, start_time, end_time) VALUES (?, ?, ?, ?)",
                           (day_id, idx, st, et or ''))

        conn.commit()
        conn.close()

        # Sincronização em segundo plano com o Firebase Firestore
        try:
            p_list = [{'period_order': idx, 'start_time': st, 'end_time': et} for idx, (st, et) in enumerate(cleaned_periods, start=1)]
            sync_work_day_to_firebase(user_id, date_val, day_type, observation, p_list, overtime_paid=overtime_paid)
        except Exception:
            pass

        has_open = any(not et for st, et in cleaned_periods)
        if has_open:
            flash(f"Horário registrado no momento do acontecimento! O dia {date_val} está em andamento. Volte para preencher a saída ou próximo turno quando acontecer.", "info")
        else:
            flash(f"Registro do dia {date_val} salvo com sucesso!", "success")
        return redirect(url_for('day_detail_view', date_str=date_val))

    # GET request
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
    try:
        t_dt = datetime.strptime(target_date, '%Y-%m-%d')
        is_target_saturday = (t_dt.weekday() == 5)
    except Exception:
        is_target_saturday = False

    # Checa se o dia já tem dados no banco
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM work_days WHERE user_id = ? AND date = ?", (user_id, target_date))
    existing_day = cursor.fetchone()
    existing_periods = []
    if existing_day:
        cursor.execute("SELECT * FROM work_periods WHERE work_day_id = ? ORDER BY period_order ASC", (existing_day['id'],))
        existing_periods = cursor.fetchall()
    conn.close()

    if existing_day:
        # Preenche com dados existentes do registro
        p1_st = existing_periods[0]['start_time'] if len(existing_periods) > 0 else ''
        p1_et = existing_periods[0]['end_time'] if len(existing_periods) > 0 else ''
        p2_st = existing_periods[1]['start_time'] if len(existing_periods) > 1 else ''
        p2_et = existing_periods[1]['end_time'] if len(existing_periods) > 1 else ''
        p3_st = existing_periods[2]['start_time'] if len(existing_periods) > 2 else ''
        p3_et = existing_periods[2]['end_time'] if len(existing_periods) > 2 else ''
        p4_st = existing_periods[3]['start_time'] if len(existing_periods) > 3 else ''
        p4_et = existing_periods[3]['end_time'] if len(existing_periods) > 3 else ''
        form_data = {
            'date': target_date,
            'day_type': existing_day['day_type'],
            'observation': existing_day['observation'] or '',
            'overtime_paid': bool(existing_day['overtime_paid']) if 'overtime_paid' in existing_day.keys() and existing_day['overtime_paid'] is not None else False,
            'no_interval': (len(existing_periods) == 1),
            'is_saturday': is_target_saturday,
            'p1_start': p1_st,
            'p1_end': p1_et,
            'p2_start': p2_st,
            'p2_end': p2_et,
            'p3_start': p3_st,
            'p3_end': p3_et,
            'p4_start': p4_st,
            'p4_end': p4_et,
        }
    else:
        # Novo registro: campos de períodos vazios para preenchimento 100% manual
        form_data = {
            'date': target_date,
            'day_type': 'trabalho',
            'observation': '',
            'overtime_paid': bool(settings.get('overtime_paid_default', 0)),
            'no_interval': False,
            'is_saturday': is_target_saturday,
            'p1_start': '',
            'p1_end': '',
            'p2_start': '',
            'p2_end': '',
            'p3_start': '',
            'p3_end': '',
            'p4_start': '',
            'p4_end': '',
        }

    return render_template('register.html', is_edit=bool(existing_day), day=existing_day, form_data=form_data, default_settings=settings, active_page='register')

# ==============================================================================
# EDIÇÃO E EXCLUSÃO
# ==============================================================================

@app.route('/registro/<int:day_id>/editar', methods=['GET', 'POST'])
@login_required
def edit_time(day_id):
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM work_days WHERE id = ? AND user_id = ?", (day_id, user_id))
    day = cursor.fetchone()
    if not day:
        conn.close()
        flash("Registro não encontrado.", "danger")
        return redirect(url_for('history_view'))

    settings = get_user_settings(user_id)

    if request.method == 'POST':
        day_type = request.form.get('day_type') or 'trabalho'
        observation = (request.form.get('observation') or '').strip()
        overtime_paid = 1 if request.form.get('overtime_paid') in ('1', 'on', 'true') else 0
        p1_s = (request.form.get('p1_start') or '').strip()
        p1_e = (request.form.get('p1_end') or '').strip()
        p2_s = (request.form.get('p2_start') or '').strip()
        p2_e = (request.form.get('p2_end') or '').strip()
        p3_s = (request.form.get('p3_start') or '').strip()
        p3_e = (request.form.get('p3_end') or '').strip()
        p4_s = (request.form.get('p4_start') or '').strip()
        p4_e = (request.form.get('p4_end') or '').strip()

        # Detecção automática inteligente de intervalo / jornada contínua:
        if p1_s and p2_e and not p1_e and not p2_s:
            raw_periods = [(p1_s, p2_e)]
            if p3_s or p3_e:
                raw_periods.append((p3_s, p3_e))
            if p4_s or p4_e:
                raw_periods.append((p4_s, p4_e))
        elif p1_s and p1_e and not p2_s and not p2_e and not p3_s and not p4_s:
            raw_periods = [(p1_s, p1_e)]
        else:
            raw_periods = [
                (p1_s, p1_e),
                (p2_s, p2_e),
                (p3_s, p3_e),
                (p4_s, p4_e),
            ]

        if day_type == 'trabalho':
            is_valid, err_msg, cleaned_periods = validate_periods(raw_periods)
            if not is_valid:
                conn.close()
                flash(err_msg, "danger")
                form_data = {
                    'date': day['date'],
                    'day_type': day_type,
                    'observation': observation,
                    'overtime_paid': bool(overtime_paid),
                    'no_interval': False,
                    'p1_start': request.form.get('p1_start', ''),
                    'p1_end': request.form.get('p1_end', ''),
                    'p2_start': request.form.get('p2_start', ''),
                    'p2_end': request.form.get('p2_end', ''),
                    'p3_start': request.form.get('p3_start', ''),
                    'p3_end': request.form.get('p3_end', ''),
                    'p4_start': request.form.get('p4_start', ''),
                    'p4_end': request.form.get('p4_end', ''),
                }
                return render_template('register.html', is_edit=True, day=day, form_data=form_data, default_settings=settings, active_page='history')

            if not cleaned_periods:
                conn.close()
                flash("Para dias de trabalho, informe ao menos o horário de entrada para registrar.", "danger")
                form_data = {
                    'date': day['date'],
                    'day_type': day_type,
                    'observation': observation,
                    'overtime_paid': bool(overtime_paid),
                    'no_interval': False,
                    'p1_start': request.form.get('p1_start', ''),
                    'p1_end': request.form.get('p1_end', ''),
                    'p2_start': request.form.get('p2_start', ''),
                    'p2_end': request.form.get('p2_end', ''),
                    'p3_start': request.form.get('p3_start', ''),
                    'p3_end': request.form.get('p3_end', ''),
                    'p4_start': request.form.get('p4_start', ''),
                    'p4_end': request.form.get('p4_end', ''),
                }
                return render_template('register.html', is_edit=True, day=day, form_data=form_data, default_settings=settings, active_page='history')
        else:
            # Folga, feriado, etc. -> sem períodos e sem contagem de horas
            cleaned_periods = []

        cursor.execute("UPDATE work_days SET day_type = ?, observation = ?, overtime_paid = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                       (day_type, observation, overtime_paid, day_id))
        cursor.execute("DELETE FROM work_periods WHERE work_day_id = ?", (day_id,))

        for idx, (st, et) in enumerate(cleaned_periods, start=1):
            cursor.execute("INSERT INTO work_periods (work_day_id, period_order, start_time, end_time) VALUES (?, ?, ?, ?)",
                           (day_id, idx, st, et or ''))

        conn.commit()
        conn.close()

        # Sincronização em segundo plano com o Firebase Firestore
        try:
            p_list = [{'period_order': idx, 'start_time': st, 'end_time': et} for idx, (st, et) in enumerate(cleaned_periods, start=1)]
            sync_work_day_to_firebase(user_id, day['date'], day_type, observation, p_list, overtime_paid=overtime_paid)
        except Exception:
            pass

        has_open = any(not et for st, et in cleaned_periods)
        if has_open:
            flash(f"Horário salvo! O dia {day['date']} está em andamento. Lembre-se de registrar a saída ao finalizar o período.", "info")
        else:
            flash(f"Registro do dia {day['date']} atualizado com sucesso!", "success")
        return redirect(url_for('day_detail_view', date_str=day['date']))

    cursor.execute("SELECT * FROM work_periods WHERE work_day_id = ? ORDER BY period_order ASC", (day_id,))
    periods = cursor.fetchall()
    conn.close()

    p1_st = periods[0]['start_time'] if len(periods) > 0 else ''
    p1_et = periods[0]['end_time'] if len(periods) > 0 else ''
    p2_st = periods[1]['start_time'] if len(periods) > 1 else ''
    p2_et = periods[1]['end_time'] if len(periods) > 1 else ''
    p3_st = periods[2]['start_time'] if len(periods) > 2 else ''
    p3_et = periods[2]['end_time'] if len(periods) > 2 else ''
    p4_st = periods[3]['start_time'] if len(periods) > 3 else ''
    p4_et = periods[3]['end_time'] if len(periods) > 3 else ''

    try:
        e_dt = datetime.strptime(day['date'], '%Y-%m-%d')
        is_edit_saturday = (e_dt.weekday() == 5)
    except Exception:
        is_edit_saturday = False

    form_data = {
        'date': day['date'],
        'day_type': day['day_type'],
        'observation': day['observation'] or '',
        'overtime_paid': bool(day['overtime_paid']) if 'overtime_paid' in day.keys() and day['overtime_paid'] is not None else False,
        'no_interval': (len(periods) == 1),
        'is_saturday': is_edit_saturday,
        'p1_start': p1_st,
        'p1_end': p1_et,
        'p2_start': p2_st,
        'p2_end': p2_et,
        'p3_start': p3_st,
        'p3_end': p3_et,
        'p4_start': p4_st,
        'p4_end': p4_et,
    }

    return render_template('register.html', is_edit=True, day=day, form_data=form_data, default_settings=settings, active_page='history')

@app.route('/registro/<int:day_id>/confirmar-exclusao')
@login_required
def confirm_delete(day_id):
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM work_days WHERE id = ? AND user_id = ?", (day_id, user_id))
    day = cursor.fetchone()
    conn.close()
    if not day:
        flash("Registro não encontrado.", "danger")
        return redirect(url_for('history_view'))

    parts = day['date'].split('-')
    date_fmt = f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else day['date']
    return render_template('confirm_delete.html', day=day, date_fmt=date_fmt, active_page='history')

@app.route('/registro/<int:day_id>/excluir', methods=['POST'])
@login_required
def delete_time(day_id):
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT date FROM work_days WHERE id = ? AND user_id = ?", (day_id, user_id))
    day = cursor.fetchone()
    if day:
        target_date = day['date']
        cursor.execute("DELETE FROM work_days WHERE id = ? AND user_id = ?", (day_id, user_id))
        conn.commit()
        conn.close()

        # Remove do Firebase Firestore
        try:
            delete_work_day_from_firebase(user_id, target_date)
        except Exception:
            pass

        flash(f"Registro do dia {target_date} excluído com sucesso.", "info")
    else:
        conn.close()
        flash("Registro não encontrado.", "danger")

    return redirect(url_for('history_view'))

# ==============================================================================
# CALENDÁRIO MENSAL & DETALHES DO DIA
# ==============================================================================

@app.route('/calendario')
@login_required
def calendar_view():
    user_id = session['user_id']
    now = datetime.now()

    try:
        year = int(request.args.get('year', now.year))
        month = int(request.args.get('month', now.month))
        if month < 1 or month > 12:
            month = now.month
    except (ValueError, TypeError):
        year = now.year
        month = now.month

    # Navegação anterior e posterior
    prev_month = 12 if month == 1 else month - 1
    prev_year = year - 1 if month == 1 else year
    next_month = 1 if month == 12 else month + 1
    next_year = year + 1 if month == 12 else year

    start_date = f"{year}-{month:02d}-01"
    _, last_day = calendar.monthrange(year, month)
    end_date = f"{year}-{month:02d}-{last_day:02d}"

    days_data, _ = get_days_data_in_range(user_id, start_date, end_date)
    days_lookup = {d['date']: d for d in days_data}

    # Resumo do mês do calendário e saldo acumulado total do banco de horas
    month_summary = generate_report_summary(user_id, start_date, end_date)
    cumulative_bank_mins, cumulative_bank_str = get_cumulative_bank_balance(user_id)

    # Gera grade de semanas (calendário com domingo como primeiro dia)
    cal = calendar.Calendar(firstweekday=6) # 6 = Domingo
    raw_month = cal.monthdayscalendar(year, month)

    today_str = now.strftime('%Y-%m-%d')
    month_weeks = []

    for week in raw_month:
        week_days = []
        for day_num in week:
            if day_num == 0:
                week_days.append({'day_number': 0})
            else:
                date_str = f"{year}-{month:02d}-{day_num:02d}"
                d_info = days_lookup.get(date_str)
                is_today = (date_str == today_str)

                if d_info:
                    day_type = d_info.get('day_type', 'trabalho')
                    status = d_info['status']
                    has_record = True
                    total_worked_str = d_info['total_worked_str']
                    balance_str = d_info['balance_str']
                else:
                    day_type = 'vazio'
                    status = 'vazio'
                    has_record = False
                    total_worked_str = '-'
                    balance_str = '00:00'

                week_days.append({
                    'day_number': day_num,
                    'date_str': date_str,
                    'is_today': is_today,
                    'day_type': day_type,
                    'status': status,
                    'has_record': has_record,
                    'total_worked_str': total_worked_str,
                    'balance_str': balance_str
                })
        month_weeks.append(week_days)

    bank_breakdown = get_bank_debit_breakdown(user_id)

    return render_template(
        'calendar.html',
        active_page='calendar',
        year=year,
        month=month,
        month_name=MONTH_NAMES_PT[month],
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        month_weeks=month_weeks,
        month_summary=month_summary,
        cumulative_bank_mins=cumulative_bank_mins,
        cumulative_bank_str=cumulative_bank_str,
        bank_breakdown=bank_breakdown
    )

@app.route('/dia/<date_str>')
@login_required
def day_detail_view(date_str):
    user_id = session['user_id']
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        flash("Data inválida.", "danger")
        return redirect(url_for('calendar_view'))

    parts = date_str.split('-')
    date_fmt = f"{parts[2]}/{parts[1]}/{parts[0]}"
    day_name_pt = WEEKDAY_NAMES_PT[dt.weekday()]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM work_days WHERE user_id = ? AND date = ?", (user_id, date_str))
    day = cursor.fetchone()
    conn.close()

    if not day:
        # Se não encontrou no banco local, tenta buscar do Firebase Firestore
        ensure_user_synced_from_firebase(user_id, min_interval=5, force=True)
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM work_days WHERE user_id = ? AND date = ?", (user_id, date_str))
        day = cursor.fetchone()
        conn.close()

    days_data, settings = get_days_data_in_range(user_id, date_str, date_str)

    if days_data:
        metrics = days_data[0]
        has_record = True
        periods = metrics.get('periods', [])
        observation = day['observation'] if day else ''
    else:
        daily_expected = settings.get('daily_hours_minutes', 480)
        tolerance = settings.get('tolerance_minutes', 10)
        metrics = calculate_day_metrics([], daily_expected, 'trabalho', tolerance)
        has_record = False
        periods = []
        observation = ''

    return render_template(
        'day_detail.html',
        active_page='calendar',
        date_str=date_str,
        date_fmt=date_fmt,
        day_name_pt=day_name_pt,
        year=dt.year,
        month=dt.month,
        has_record=has_record,
        day=day,
        metrics=metrics,
        periods=periods,
        observation=observation
    )

# ==============================================================================
# HISTÓRICO
# ==============================================================================

@app.route('/historico')
@login_required
def history_view():
    user_id = session['user_id']
    date_start = request.args.get('date_start', '').strip()
    date_end = request.args.get('date_end', '').strip()
    month_filter = request.args.get('month_filter', '').strip()
    status_filter = request.args.get('status_filter', '').strip()

    # Se filtrou por mês específico (YYYY-MM)
    if month_filter and not date_start and not date_end:
        try:
            parts = month_filter.split('-')
            y, m = int(parts[0]), int(parts[1])
            _, last_d = calendar.monthrange(y, m)
            date_start = f"{y}-{m:02d}-01"
            date_end = f"{y}-{m:02d}-{last_d:02d}"
        except Exception:
            pass

    # Padrão: últimos 90 dias se nenhum filtro for passado
    if not date_start:
        now = datetime.now()
        date_start = (now - timedelta(days=90)).strftime('%Y-%m-%d')
    if not date_end:
        date_end = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')

    days_data, _ = get_days_data_in_range(user_id, date_start, date_end)

    filtered_records = []
    for item in reversed(days_data): # Mais recentes primeiro
        parts = item['date'].split('-')
        item['date_fmt'] = f"{parts[2]}/{parts[1]}/{parts[0]}"
        periods = item.get('periods', [])
        item['first_in'] = periods[0]['start_time'] if len(periods) > 0 else '-'
        item['break_out'] = periods[0]['end_time'] if len(periods) > 1 else ('Sem int.' if len(periods) == 1 else '-')
        item['break_in'] = periods[1]['start_time'] if len(periods) > 1 else ('Sem int.' if len(periods) == 1 else '-')
        item['last_out'] = periods[-1]['end_time'] if len(periods) > 0 else '-'

        # Aplica filtro de status
        if status_filter == 'positivo' and item['balance_minutes'] <= 0:
            continue
        elif status_filter == 'negativo' and item['balance_minutes'] >= 0:
            continue
        elif status_filter == 'zerado' and item['balance_minutes'] != 0:
            continue
        elif status_filter == 'falta' and item['day_type'] != 'falta':
            continue
        elif status_filter == 'folga' and item['day_type'] not in ('folga', 'feriado', 'atestado', 'compensacao'):
            continue

        filtered_records.append(item)

    filters = {
        'date_start': request.args.get('date_start', ''),
        'date_end': request.args.get('date_end', ''),
        'month_filter': month_filter,
        'status_filter': status_filter
    }

    return render_template(
        'history.html',
        active_page='history',
        records=filtered_records,
        filters=filters
    )

# ==============================================================================
# RELATÓRIOS & EXPORTAÇÕES (100% PYTHON - POR MÊS SELECIONADO)
# ==============================================================================

MONTH_LIST_PT = [
    (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
    (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
    (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro')
]

def _parse_report_month(req_args):
    """
    Identifica com precisão o mês e o ano selecionados pelo usuário,
    suportando query params (month, year), seletores HTML (month_picker YYYY-MM)
    ou datas de fallback. Garante que qualquer mês (ex: Agosto/08) seja selecionado com precisão.
    """
    now = datetime.now()
    year = None
    month = None

    # 1. Seletor de mês nativo (YYYY-MM)
    month_picker = (req_args.get('month_picker') or '').strip()
    if month_picker and '-' in month_picker:
        try:
            parts = month_picker.split('-')
            year = int(parts[0])
            month = int(parts[1])
        except (ValueError, IndexError):
            pass

    # 2. Parâmetro 'month' no formato YYYY-MM ou numérico
    if not (year and month):
        m_param = (req_args.get('month') or '').strip()
        y_param = (req_args.get('year') or '').strip()
        if '-' in m_param:
            try:
                parts = m_param.split('-')
                year = int(parts[0])
                month = int(parts[1])
            except (ValueError, IndexError):
                pass
        elif m_param:
            try:
                month = int(m_param)
                year = int(y_param) if y_param else now.year
            except ValueError:
                pass

    # 3. Fallback para start_date ou start (ex: "2026-08-01")
    if not (year and month):
        st = (req_args.get('start_date') or req_args.get('start') or '').strip()
        if len(st) >= 7 and st[4] == '-':
            try:
                year = int(st[:4])
                month = int(st[5:7])
            except (ValueError, IndexError):
                pass

    # 4. Fallback padrão: Mês e Ano atuais
    if not year or not month or month < 1 or month > 12 or year < 2000 or year > 2100:
        year = now.year
        month = now.month

    start_date = f"{year}-{month:02d}-01"
    _, last_d = calendar.monthrange(year, month)
    end_date = f"{year}-{month:02d}-{last_d:02d}"

    prev_month = 12 if month == 1 else month - 1
    prev_year = year - 1 if month == 1 else year
    next_month = 1 if month == 12 else month + 1
    next_year = year + 1 if month == 12 else year

    return {
        'year': year,
        'month': month,
        'month_name': MONTH_NAMES_PT.get(month, f"Mês {month}"),
        'month_picker_val': f"{year}-{month:02d}",
        'start_date': start_date,
        'end_date': end_date,
        'last_day': last_d,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year
    }

@app.route('/relatorios')
@login_required
def reports_view():
    user_id = session['user_id']
    info = _parse_report_month(request.args)
    now = datetime.now()

    summary = generate_report_summary(user_id, info['start_date'], info['end_date'])
    cumulative_bank_mins, cumulative_bank_str = get_cumulative_bank_balance(user_id)
    bank_breakdown = get_bank_debit_breakdown(user_id)

    # Anos disponíveis para o seletor (3 anos atrás até 3 anos à frente)
    available_years = list(range(now.year - 3, now.year + 4))
    if info['year'] not in available_years:
        available_years.append(info['year'])
        available_years.sort()

    return render_template(
        'reports.html',
        active_page='reports',
        selected_year=info['year'],
        selected_month=info['month'],
        month_name=info['month_name'],
        month_picker_val=info['month_picker_val'],
        start_date=info['start_date'],
        end_date=info['end_date'],
        last_day=info['last_day'],
        prev_month=info['prev_month'],
        prev_year=info['prev_year'],
        next_month=info['next_month'],
        next_year=info['next_year'],
        all_months=MONTH_LIST_PT,
        available_years=available_years,
        summary=summary,
        cumulative_bank_mins=cumulative_bank_mins,
        cumulative_bank_str=cumulative_bank_str,
        bank_breakdown=bank_breakdown,
        current_year=now.year,
        current_month=now.month
    )

@app.route('/relatorios/exportar/csv')
@login_required
def export_csv():
    user_id = session['user_id']
    info = _parse_report_month(request.args)
    csv_stream = export_to_csv(user_id, info['start_date'], info['end_date'])
    month_slug = info['month_name'].lower()
    filename = f"relatorio_{month_slug}_{info['year']}.csv"
    return send_file(
        csv_stream,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=filename
    )

@app.route('/relatorios/exportar/excel')
@login_required
def export_excel():
    user_id = session['user_id']
    info = _parse_report_month(request.args)
    excel_stream = export_to_excel(user_id, info['start_date'], info['end_date'])
    month_slug = info['month_name'].lower()
    filename = f"relatorio_{month_slug}_{info['year']}.xlsx"
    return send_file(
        excel_stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

@app.route('/relatorios/exportar/pdf')
@login_required
def export_pdf():
    user_id = session['user_id']
    info = _parse_report_month(request.args)
    pdf_stream = export_to_pdf(user_id, info['start_date'], info['end_date'])
    month_slug = info['month_name'].lower()
    filename = f"relatorio_{month_slug}_{info['year']}.pdf"
    return send_file(
        pdf_stream,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )

# ==============================================================================
# CONFIGURAÇÕES
# ==============================================================================

@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def settings_view():
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'save_work_settings':
            daily_hours_str = (request.form.get('daily_hours') or '08:00').strip()
            daily_hours_mins = time_to_minutes(daily_hours_str)
            if daily_hours_mins <= 0:
                daily_hours_mins = 480

            saturday_hours_str = (request.form.get('saturday_hours') or '04:00').strip()
            saturday_hours_mins = time_to_minutes(saturday_hours_str)
            if saturday_hours_mins <= 0:
                saturday_hours_mins = 240

            default_saturday_start = request.form.get('default_saturday_start') or '08:00'
            default_saturday_end = request.form.get('default_saturday_end') or '12:00'
            saturday_has_break = 1 if request.form.get('saturday_has_break') == '1' else 0

            tolerance = int(request.form.get('tolerance_minutes') or 0)
            default_start = request.form.get('default_start') or '08:00'
            default_break_start = request.form.get('default_break_start') or '12:00'
            default_break_end = request.form.get('default_break_end') or '13:00'
            default_end = request.form.get('default_end') or '17:00'
            bank_active = 1 if request.form.get('bank_active') == '1' else 0
            overtime_paid_default = 1 if request.form.get('overtime_paid_default') in ('1', 'on', 'true') else 0

            # Saldo inicial
            raw_init = (request.form.get('initial_balance') or '00:00').strip()
            is_neg = raw_init.startswith('-')
            clean_init = raw_init.lstrip('+-')
            init_mins = time_to_minutes(clean_init)
            initial_balance_mins = -init_mins if is_neg else init_mins

            cursor.execute("""
                UPDATE settings SET
                    daily_hours_minutes = ?,
                    saturday_hours_minutes = ?,
                    default_start = ?,
                    default_break_start = ?,
                    default_break_end = ?,
                    default_end = ?,
                    default_saturday_start = ?,
                    default_saturday_end = ?,
                    saturday_has_break = ?,
                    tolerance_minutes = ?,
                    bank_active = ?,
                    initial_balance_minutes = ?,
                    overtime_paid_default = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (
                daily_hours_mins, saturday_hours_mins, default_start, default_break_start,
                default_break_end, default_end, default_saturday_start, default_saturday_end,
                saturday_has_break, tolerance, bank_active,
                initial_balance_mins, overtime_paid_default, user_id
            ))
            conn.commit()

            # Sincroniza configurações no Firebase
            try:
                sync_settings_to_firebase(user_id, {
                    'daily_hours_minutes': daily_hours_mins,
                    'saturday_hours_minutes': saturday_hours_mins,
                    'default_start': default_start,
                    'default_break_start': default_break_start,
                    'default_break_end': default_break_end,
                    'default_end': default_end,
                    'default_saturday_start': default_saturday_start,
                    'default_saturday_end': default_saturday_end,
                    'saturday_has_break': saturday_has_break,
                    'tolerance_minutes': tolerance,
                    'bank_active': bank_active,
                    'initial_balance_minutes': initial_balance_mins
                })
            except Exception:
                pass

            flash("Configurações de jornada (dias úteis e sábados) atualizadas com sucesso!", "success")

        elif action == 'sync_firebase':
            ok, msg = sync_all_local_to_firebase(user_id, conn)
            if ok:
                flash(f"☁️ Firebase Firestore: {msg}", "success")
            else:
                flash(f"Aviso de sincronização Firebase: {msg}", "warning")

        elif action == 'pull_firebase':
            ok, msg = pull_from_firebase(user_id, conn)
            if ok:
                flash(f"☁️ Restauração Firebase: {msg}", "success")
            else:
                flash(f"Aviso de restauração Firebase: {msg}", "warning")

        elif action == 'update_account':
            new_name = (request.form.get('name') or '').strip()
            new_email = (request.form.get('email') or '').strip().lower()
            new_cpf = (request.form.get('cpf') or '').strip()
            new_pwd = request.form.get('new_password') or ''
            confirm_pwd = request.form.get('confirm_password') or ''

            if not new_email:
                flash("O e-mail não pode ser vazio.", "danger")
            else:
                formatted_cpf = None
                if new_cpf:
                    if not validate_cpf(new_cpf):
                        flash("O CPF informado é inválido pelo algoritmo oficial da Receita Federal.", "danger")
                        conn.close()
                        return redirect(url_for('settings_view'))
                    formatted_cpf = format_cpf(new_cpf)

                if new_pwd:
                    if new_pwd != confirm_pwd:
                        flash("A nova senha e a confirmação não conferem.", "danger")
                    elif len(new_pwd) < 4:
                        flash("A nova senha deve ter pelo menos 4 caracteres.", "danger")
                    else:
                        new_hash = generate_password_hash(new_pwd)
                        cursor.execute("""
                            UPDATE users 
                            SET email = ?, password_hash = ?, name = COALESCE(?, name), cpf = COALESCE(?, cpf) 
                            WHERE id = ?
                        """, (new_email, new_hash, new_name or None, formatted_cpf, user_id))
                        conn.commit()
                        session['user_email'] = new_email
                        if new_name:
                            session['user_name'] = new_name
                        flash("Dados cadastrais, CPF e senha atualizados com sucesso!", "success")
                else:
                    cursor.execute("""
                        UPDATE users 
                        SET email = ?, name = COALESCE(?, name), cpf = COALESCE(?, cpf) 
                        WHERE id = ?
                    """, (new_email, new_name or None, formatted_cpf, user_id))
                    conn.commit()
                    session['user_email'] = new_email
                    if new_name:
                        session['user_name'] = new_name
                    flash("Dados cadastrais e CPF atualizados com sucesso!", "success")

                # Sincroniza imediatamente o perfil com o Firebase Firestore para persistência definitiva na nuvem
                try:
                    cursor.execute("SELECT name, cpf, role FROM users WHERE id = ?", (user_id,))
                    u_fresh = cursor.fetchone()
                    sync_user_profile_to_firebase(
                        user_id=user_id,
                        email=new_email,
                        name=u_fresh['name'] if u_fresh else (new_name or ''),
                        cpf=u_fresh['cpf'] if u_fresh else (formatted_cpf or ''),
                        role=u_fresh['role'] if u_fresh else 'user'
                    )
                except Exception as _sync_err:
                    print(f"Aviso ao sincronizar perfil no Firebase: {_sync_err}")

        elif action == 'reset_all_data':
            cursor.execute("DELETE FROM work_periods WHERE work_day_id IN (SELECT id FROM work_days WHERE user_id = ?)", (user_id,))
            cursor.execute("DELETE FROM work_days WHERE user_id = ?", (user_id,))
            cursor.execute("UPDATE settings SET initial_balance_minutes = 0 WHERE user_id = ?", (user_id,))
            conn.commit()

            # Reseta dados no Firebase Firestore também
            try:
                reset_user_data_in_firebase(user_id)
            except Exception:
                pass

            flash("Todos os registros de horas e períodos foram excluídos localmente e da nuvem Firebase!", "success")

        elif action == 'reset_system':
            cursor.execute("SELECT role, email FROM users WHERE id = ?", (user_id,))
            u_check = cursor.fetchone()
            if u_check and (u_check['role'] == 'admin' or u_check['email'] in ('p.nikolas3@gmail.com', 'ncodestechnologies@gmail.com')):
                conn.close()
                reset_all_data()
                session.clear()
                flash("Sistema resetado com sucesso! A base de dados, usuários e registros foram reinicializados aos padrões de fábrica.", "success")
                return redirect(url_for('login'))
            else:
                flash("Apenas o Administrador Master pode executar o reset completo do sistema.", "danger")

        conn.close()
        return redirect(url_for('settings_view'))

    # GET
    cursor.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,))
    settings_row = cursor.fetchone()
    cursor.execute("SELECT id, email, name, cpf, role, google_id FROM users WHERE id = ?", (user_id,))
    user_row = cursor.fetchone()

    # Se o banco SQLite local não possuir CPF ou nome gravado (ex: após novo deploy ou cold start serverless),
    # tenta recuperar automaticamente do Firestore para manter os dados cadastrais sempre preenchidos
    if user_row and (not user_row['cpf'] or not user_row['name']):
        try:
            prof = pull_user_profile_from_firebase(user_id=user_id, email=user_row['email'])
            if prof and (prof.get('cpf') or prof.get('name')):
                fb_cpf = prof.get('cpf')
                fb_name = prof.get('name')
                cursor.execute("""
                    UPDATE users
                    SET name = COALESCE(NULLIF(?, ''), name),
                        cpf = COALESCE(NULLIF(?, ''), cpf)
                    WHERE id = ?
                """, (fb_name, fb_cpf, user_id))
                conn.commit()
                cursor.execute("SELECT id, email, name, cpf, role, google_id FROM users WHERE id = ?", (user_id,))
                user_row = cursor.fetchone()
                if fb_name:
                    session['user_name'] = fb_name
        except Exception:
            pass

    conn.close()

    s_data = dict(settings_row) if settings_row else {}
    s_data['daily_hours_str'] = minutes_to_time_str(s_data.get('daily_hours_minutes', 480))
    s_data['saturday_hours_str'] = minutes_to_time_str(s_data.get('saturday_hours_minutes', 240))
    s_data['initial_balance_str'] = minutes_to_balance_str(s_data.get('initial_balance_minutes', 0))

    fb_status = get_firebase_status()

    return render_template(
        'settings.html',
        active_page='settings',
        settings=s_data,
        user_email=user_row['email'] if user_row else '',
        user_data=dict(user_row) if user_row else {},
        firebase_status=fb_status
    )

# ==============================================================================
# INICIALIZAÇÃO DO SERVIDOR
# ==============================================================================

if __name__ == '__main__':
    # Inicializa o banco de dados e cria tabelas/usuário padrão se necessário
    init_db()

    parser = argparse.ArgumentParser(description="Meu Controle de Horas - Servidor Python")
    parser.add_argument('--port', type=int, default=3000, help="Porta para o servidor")
    parser.add_argument('--host', type=str, default='0.0.0.0', help="Host para o servidor")
    args, _ = parser.parse_known_args()

    print(f"==================================================", flush=True)
    print(f"Meu Controle de Horas — Iniciando servidor Flask", flush=True)
    print(f"Host: {args.host} | Porta: {args.port}", flush=True)
    print(f"100% Python • HTML5 • CSS3 • Zero JavaScript", flush=True)
    print(f"==================================================", flush=True)
    app.run(host=args.host, port=args.port, debug=False)
