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
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_file, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

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
    pull_from_firebase
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)
app.secret_key = os.environ.get('SECRET_KEY', 'controle-horas-secret-key-prod-2026')
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_NAME'] = 'horas_session'

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
    cursor.execute("SELECT id, email, name, role FROM users WHERE LOWER(email) = 'ncodestechnologies@gmail.com' LIMIT 1")
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
            return redirect(url_for('register_user'))
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def before_req():
    # Mantém a sessão sem forçar auto-login para permitir cadastro de novas contas
    pass

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

@app.route('/')
@app.route('/api')
@app.route('/api/')
@app.route('/api/index')
@app.route('/api/index.py')
def root():
    """Tela inicial: se logado, vai ao dashboard; se não logado, tela de criação de conta."""
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('register_user'))

@app.route('/cadastro', methods=['GET', 'POST'])
def register_user():
    cfg = load_config() or {}
    oauth_client_id = cfg.get('oAuthClientId', '870123577586-pg4be1a64u837udo6eqatc0kr5g5ikb5.apps.googleusercontent.com')

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        cpf = (request.form.get('cpf') or '').strip()
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        if not email or '@' not in email:
            flash("Por favor, informe um endereço de e-mail válido.", "danger")
            return render_template('register_user.html', name=name, email=email, cpf=cpf, oauth_client_id=oauth_client_id, active_page='login')

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
                return render_template('register_user.html', name=name, email=email, cpf=cpf, oauth_client_id=oauth_client_id, active_page='login')

        if not password or password != confirm_password:
            flash("A senha e a confirmação de senha não coincidem.", "danger")
            return render_template('register_user.html', name=name, email=email, cpf=formatted_cpf or cpf, oauth_client_id=oauth_client_id, active_page='login')

        if len(password) < 4:
            flash("A senha deve conter pelo menos 4 caracteres.", "danger")
            return render_template('register_user.html', name=name, email=email, cpf=formatted_cpf or cpf, oauth_client_id=oauth_client_id, active_page='login')

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
            user_role = existing['role'] or ('admin' if email == 'ncodestechnologies@gmail.com' else 'user')
            user_display_name = name or existing['name'] or email.split('@')[0]
        else:
            role = 'admin' if email == 'ncodestechnologies@gmail.com' else 'user'
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

    return render_template('register_user.html', oauth_client_id=oauth_client_id, active_page='login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    cfg = load_config() or {}
    oauth_client_id = cfg.get('oAuthClientId', '870123577586-pg4be1a64u837udo6eqatc0kr5g5ikb5.apps.googleusercontent.com')

    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,))
        user = cursor.fetchone()
        conn.close()

        if user and user['password_hash'] and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['user_email'] = user['email']
            session['user_name'] = user['name'] or user['email'].split('@')[0]
            session['user_role'] = user['role'] or ('admin' if user['email'] == 'ncodestechnologies@gmail.com' else 'user')
            session['logged_out'] = False
            flash(f"Bem-vindo(a), {session['user_name']}!", "success")
            return redirect(url_for('dashboard'))
        else:
            flash("E-mail ou senha incorretos. Por favor, confira os dados informados.", "danger")

    if session.get('user_id') and not request.args.get('force'):
        return redirect(url_for('dashboard'))

    return render_template('login.html', default_email="", oauth_client_id=oauth_client_id, active_page='login')

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
        role = user['role'] or ('admin' if email == 'ncodestechnologies@gmail.com' else 'user')
        cursor.execute("""
            UPDATE users 
            SET google_id = COALESCE(google_id, ?), 
                avatar_url = COALESCE(avatar_url, ?),
                name = COALESCE(name, ?)
            WHERE id = ?
        """, (google_id, avatar_url, name, user_id))
        conn.commit()
    else:
        role = 'admin' if email == 'ncodestechnologies@gmail.com' else 'user'
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
            item['break_out'] = periods[0]['end_time'] if len(periods) > 1 else '-'
            item['break_in'] = periods[1]['start_time'] if len(periods) > 1 else '-'
            item['last_out'] = periods[-1]['end_time'] if len(periods) > 0 else '-'
            recent_days.append(item)

    return render_template(
        'dashboard.html',
        active_page='dashboard',
        today_display=today_display,
        current_month_display=current_month_display,
        today_metrics=today_metrics,
        month_summary=month_summary,
        cumulative_bank_mins=cumulative_bank_mins,
        cumulative_bank_str=cumulative_bank_str,
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

        # Coleta os períodos informados
        raw_periods = [
            (request.form.get('p1_start'), request.form.get('p1_end')),
            (request.form.get('p2_start'), request.form.get('p2_end')),
            (request.form.get('p3_start'), request.form.get('p3_end')),
            (request.form.get('p4_start'), request.form.get('p4_end')),
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
                flash("Para dias de trabalho, informe ao menos um período (entrada e saída).", "danger")
                form_data = {
                    'date': date_val,
                    'day_type': day_type,
                    'observation': observation,
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
            # Folga, feriado, atestado, compensação -> sem períodos e sem contagem de horas
            cleaned_periods = []

        conn = get_db()
        cursor = conn.cursor()

        # Verifica se já existe registro nesta data
        cursor.execute("SELECT id FROM work_days WHERE user_id = ? AND date = ?", (user_id, date_val))
        existing = cursor.fetchone()

        if existing:
            day_id = existing['id']
            cursor.execute("UPDATE work_days SET day_type = ?, observation = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                           (day_type, observation, day_id))
            cursor.execute("DELETE FROM work_periods WHERE work_day_id = ?", (day_id,))
        else:
            cursor.execute("INSERT INTO work_days (user_id, date, day_type, observation) VALUES (?, ?, ?, ?)",
                           (user_id, date_val, day_type, observation))
            day_id = cursor.lastrowid

        for idx, (st, et) in enumerate(cleaned_periods, start=1):
            cursor.execute("INSERT INTO work_periods (work_day_id, period_order, start_time, end_time) VALUES (?, ?, ?, ?)",
                           (day_id, idx, st, et))

        conn.commit()
        conn.close()

        # Sincronização em segundo plano com o Firebase Firestore
        try:
            p_list = [{'period_order': idx, 'start_time': st, 'end_time': et} for idx, (st, et) in enumerate(cleaned_periods, start=1)]
            sync_work_day_to_firebase(user_id, date_val, day_type, observation, p_list)
        except Exception:
            pass

        flash(f"Registro do dia {date_val} salvo com sucesso!", "success")
        return redirect(url_for('day_detail_view', date_str=date_val))

    # GET request
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
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
        # Inicia tudo completamente limpo/zerado para não precisar apagar caso seja folga/feriado
        form_data = {
            'date': target_date,
            'day_type': 'trabalho',
            'observation': '',
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

        raw_periods = [
            (request.form.get('p1_start'), request.form.get('p1_end')),
            (request.form.get('p2_start'), request.form.get('p2_end')),
            (request.form.get('p3_start'), request.form.get('p3_end')),
            (request.form.get('p4_start'), request.form.get('p4_end')),
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
                flash("Para dias de trabalho, informe ao menos um período (entrada e saída).", "danger")
                form_data = {
                    'date': day['date'],
                    'day_type': day_type,
                    'observation': observation,
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

        cursor.execute("UPDATE work_days SET day_type = ?, observation = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                       (day_type, observation, day_id))
        cursor.execute("DELETE FROM work_periods WHERE work_day_id = ?", (day_id,))

        for idx, (st, et) in enumerate(cleaned_periods, start=1):
            cursor.execute("INSERT INTO work_periods (work_day_id, period_order, start_time, end_time) VALUES (?, ?, ?, ?)",
                           (day_id, idx, st, et))

        conn.commit()
        conn.close()

        # Sincronização em segundo plano com o Firebase Firestore
        try:
            p_list = [{'period_order': idx, 'start_time': st, 'end_time': et} for idx, (st, et) in enumerate(cleaned_periods, start=1)]
            sync_work_day_to_firebase(user_id, day['date'], day_type, observation, p_list)
        except Exception:
            pass

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

    form_data = {
        'date': day['date'],
        'day_type': day['day_type'],
        'observation': day['observation'] or '',
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
        cumulative_bank_str=cumulative_bank_str
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
        item['break_out'] = periods[0]['end_time'] if len(periods) > 1 else '-'
        item['break_in'] = periods[1]['start_time'] if len(periods) > 1 else '-'
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
# RELATÓRIOS & EXPORTAÇÕES (100% PYTHON)
# ==============================================================================

@app.route('/relatorios')
@login_required
def reports_view():
    user_id = session['user_id']
    preset = request.args.get('preset', 'this_month')
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()

    now = datetime.now()

    if preset == 'this_month' and not (start_date and end_date and preset == 'custom'):
        start_date = f"{now.year}-{now.month:02d}-01"
        _, last_d = calendar.monthrange(now.year, now.month)
        end_date = f"{now.year}-{now.month:02d}-{last_d:02d}"
    elif preset == 'last_month':
        m = 12 if now.month == 1 else now.month - 1
        y = now.year - 1 if now.month == 1 else now.year
        start_date = f"{y}-{m:02d}-01"
        _, last_d = calendar.monthrange(y, m)
        end_date = f"{y}-{m:02d}-{last_d:02d}"
    elif preset == 'this_week':
        # Começa no Domingo da semana atual
        start_dt = now - timedelta(days=(now.weekday() + 1) % 7)
        end_dt = start_dt + timedelta(days=6)
        start_date = start_dt.strftime('%Y-%m-%d')
        end_date = end_dt.strftime('%Y-%m-%d')
    else:
        if not start_date:
            start_date = f"{now.year}-{now.month:02d}-01"
        if not end_date:
            _, last_d = calendar.monthrange(now.year, now.month)
            end_date = f"{now.year}-{now.month:02d}-{last_d:02d}"

    summary = generate_report_summary(user_id, start_date, end_date)
    cumulative_bank_mins, cumulative_bank_str = get_cumulative_bank_balance(user_id)

    return render_template(
        'reports.html',
        active_page='reports',
        preset=preset,
        summary=summary,
        cumulative_bank_mins=cumulative_bank_mins,
        cumulative_bank_str=cumulative_bank_str
    )

@app.route('/relatorios/exportar/csv')
@login_required
def export_csv():
    user_id = session['user_id']
    start = request.args.get('start', datetime.now().strftime('%Y-%m-01'))
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))

    csv_stream = export_to_csv(user_id, start, end)
    filename = f"controle_horas_{start}_a_{end}.csv"
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
    start = request.args.get('start', datetime.now().strftime('%Y-%m-01'))
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))

    excel_stream = export_to_excel(user_id, start, end)
    filename = f"controle_horas_{start}_a_{end}.xlsx"
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
    start = request.args.get('start', datetime.now().strftime('%Y-%m-01'))
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))

    pdf_stream = export_to_pdf(user_id, start, end)
    filename = f"controle_horas_{start}_a_{end}.pdf"
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

            tolerance = int(request.form.get('tolerance_minutes') or 0)
            default_start = request.form.get('default_start') or '08:00'
            default_break_start = request.form.get('default_break_start') or '12:00'
            default_break_end = request.form.get('default_break_end') or '13:00'
            default_end = request.form.get('default_end') or '17:00'
            bank_active = 1 if request.form.get('bank_active') == '1' else 0

            # Saldo inicial
            raw_init = (request.form.get('initial_balance') or '00:00').strip()
            is_neg = raw_init.startswith('-')
            clean_init = raw_init.lstrip('+-')
            init_mins = time_to_minutes(clean_init)
            initial_balance_mins = -init_mins if is_neg else init_mins

            cursor.execute("""
                UPDATE settings SET
                    daily_hours_minutes = ?,
                    default_start = ?,
                    default_break_start = ?,
                    default_break_end = ?,
                    default_end = ?,
                    tolerance_minutes = ?,
                    bank_active = ?,
                    initial_balance_minutes = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (
                daily_hours_mins, default_start, default_break_start,
                default_break_end, default_end, tolerance, bank_active,
                initial_balance_mins, user_id
            ))
            conn.commit()

            # Sincroniza configurações no Firebase
            try:
                sync_settings_to_firebase(user_id, {
                    'daily_hours_minutes': daily_hours_mins,
                    'default_start': default_start,
                    'default_break_start': default_break_start,
                    'default_break_end': default_break_end,
                    'default_end': default_end,
                    'tolerance_minutes': tolerance,
                    'bank_active': bank_active,
                    'initial_balance_minutes': initial_balance_mins
                })
            except Exception:
                pass

            flash("Configurações de jornada e banco de horas atualizadas com sucesso!", "success")

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
            if u_check and (u_check['role'] == 'admin' or u_check['email'] == 'ncodestechnologies@gmail.com'):
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
    conn.close()

    s_data = dict(settings_row) if settings_row else {}
    s_data['daily_hours_str'] = minutes_to_time_str(s_data.get('daily_hours_minutes', 480))
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

    print(f"==================================================")
    print(f"Meu Controle de Horas — Iniciando servidor Flask")
    print(f"Host: {args.host} | Porta: {args.port}")
    print(f"100% Python • HTML5 • CSS3 • Zero JavaScript")
    print(f"==================================================")
    app.run(host=args.host, port=args.port, debug=False)
