import os
import sqlite3
import shutil
from werkzeug.security import generate_password_hash

# Detecta se está em ambiente serverless (Vercel / AWS Lambda) ou se o diretório do app é somente-leitura
IS_SERVERLESS = bool(os.environ.get('VERCEL') or os.environ.get('AWS_LAMBDA_FUNCTION_NAME') or os.environ.get('LAMBDA_TASK_ROOT'))

DEFAULT_DB_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, 'database.db')

# No Vercel Serverless, o filesystem do código (/var/task) é somente-leitura.
# O único local com permissão de escrita para SQLite é a pasta /tmp.
def get_db_path():
    if IS_SERVERLESS or not os.access(DEFAULT_DB_DIR, os.W_OK):
        tmp_path = '/tmp/database.db'
        if not os.path.exists(tmp_path) and os.path.exists(DEFAULT_DB_PATH):
            try:
                shutil.copyfile(DEFAULT_DB_PATH, tmp_path)
            except Exception:
                pass
        return tmp_path
    return DEFAULT_DB_PATH

DB_PATH = get_db_path()
_initialized = False

def get_db():
    global _initialized, DB_PATH
    DB_PATH = get_db_path()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    # Auto-inicializa o banco caso as tabelas não existam (essencial para cold starts na Vercel)
    if not _initialized:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
            if not cursor.fetchone():
                _initialized = True
                conn.close()
                init_db()
                conn = sqlite3.connect(DB_PATH, timeout=15)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON;")
            else:
                _initialized = True
        except Exception:
            _initialized = True

    return conn

def init_db():
    global DB_PATH
    DB_PATH = get_db_path()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT,
        name TEXT,
        cpf TEXT,
        role TEXT DEFAULT 'user',
        google_id TEXT,
        avatar_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Garantir que colunas adicionadas existam caso a tabela já tenha sido criada anteriormente
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN name TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN cpf TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
    except Exception:
        pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        daily_hours_minutes INTEGER DEFAULT 480,
        saturday_hours_minutes INTEGER DEFAULT 240,
        default_start TEXT DEFAULT '08:00',
        default_break_start TEXT DEFAULT '12:00',
        default_break_end TEXT DEFAULT '13:00',
        default_end TEXT DEFAULT '17:00',
        default_saturday_start TEXT DEFAULT '08:00',
        default_saturday_end TEXT DEFAULT '12:00',
        saturday_has_break INTEGER DEFAULT 0,
        tolerance_minutes INTEGER DEFAULT 10,
        bank_active INTEGER DEFAULT 1,
        initial_balance_minutes INTEGER DEFAULT 0,
        overtime_paid_default INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    # Migrações para colunas adicionais em settings
    for col_def in [
        "ALTER TABLE settings ADD COLUMN saturday_hours_minutes INTEGER DEFAULT 240",
        "ALTER TABLE settings ADD COLUMN default_saturday_start TEXT DEFAULT '08:00'",
        "ALTER TABLE settings ADD COLUMN default_saturday_end TEXT DEFAULT '12:00'",
        "ALTER TABLE settings ADD COLUMN saturday_has_break INTEGER DEFAULT 0",
        "ALTER TABLE settings ADD COLUMN overtime_paid_default INTEGER DEFAULT 0"
    ]:
        try:
            cursor.execute(col_def)
        except Exception:
            pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS work_days (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        day_type TEXT DEFAULT 'trabalho',
        observation TEXT,
        overtime_paid INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, date),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    # Migrações para work_days
    for col_def in [
        "ALTER TABLE work_days ADD COLUMN overtime_paid INTEGER DEFAULT 0"
    ]:
        try:
            cursor.execute(col_def)
        except Exception:
            pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS work_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        work_day_id INTEGER NOT NULL,
        period_order INTEGER NOT NULL DEFAULT 1,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        FOREIGN KEY (work_day_id) REFERENCES work_days(id) ON DELETE CASCADE
    );
    """)

    # Configuração dos usuários administrativos principais
    admin_accounts = [
        ("p.nikolas3@gmail.com", "Nikolas Pereira dos Santos", "Taijou13"),
        ("ncodestechnologies@gmail.com", "Taijou BR", "Taijou13")
    ]
    for email, name, pwd in admin_accounts:
        pwd_hash = generate_password_hash(pwd)
        cursor.execute("SELECT id FROM users WHERE LOWER(email) = ?", (email.lower(),))
        existing = cursor.fetchone()
        if not existing:
            cursor.execute("""
                INSERT INTO users (email, password_hash, name, role) 
                VALUES (?, ?, ?, 'admin')
            """, (email, pwd_hash, name))
            uid = cursor.lastrowid
            cursor.execute("""
            INSERT INTO settings (user_id, daily_hours_minutes, saturday_hours_minutes, default_start, default_break_start, default_break_end, default_end, default_saturday_start, default_saturday_end, saturday_has_break, tolerance_minutes, bank_active, initial_balance_minutes)
            VALUES (?, 480, 240, '08:00', '12:00', '13:00', '17:00', '08:00', '12:00', 0, 10, 1, 0)
            """, (uid,))
        else:
            cursor.execute("""
                UPDATE users 
                SET password_hash = ?, role = 'admin', name = COALESCE(name, ?)
                WHERE id = ?
            """, (pwd_hash, name, existing['id']))
            cursor.execute("SELECT id FROM settings WHERE user_id = ?", (existing['id'],))
            if not cursor.fetchone():
                cursor.execute("""
                INSERT INTO settings (user_id, daily_hours_minutes, saturday_hours_minutes, default_start, default_break_start, default_break_end, default_end, default_saturday_start, default_saturday_end, saturday_has_break, tolerance_minutes, bank_active, initial_balance_minutes)
                VALUES (?, 480, 240, '08:00', '12:00', '13:00', '17:00', '08:00', '12:00', 0, 10, 1, 0)
                """, (existing['id'],))
    conn.commit()

    conn.close()

def reset_all_data():
    """
    Executa o reset completo solicitado pelo usuário:
    - Recria tabelas com schema atualizado
    - Restaura p.nikolas3@gmail.com com a senha Taijou13 como Admin master
    - Restaura configurações padrão
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS work_periods")
    cursor.execute("DROP TABLE IF EXISTS work_days")
    cursor.execute("DROP TABLE IF EXISTS settings")
    cursor.execute("DROP TABLE IF EXISTS users")
    conn.commit()
    conn.close()

    init_db()
