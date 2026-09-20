import os
import json
import logging
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'firebase-applet-config.json')

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Erro ao ler firebase-applet-config.json: {e}")
        return None

def get_base_url():
    cfg = load_config()
    if not cfg:
        return None, None
    project_id = cfg.get('projectId')
    database_id = cfg.get('firestoreDatabaseId', '(default)')
    api_key = cfg.get('apiKey')
    if not project_id or not api_key:
        return None, None
    url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/{database_id}/documents"
    return url, api_key

def to_firestore_value(val):
    if val is None:
        return {'nullValue': None}
    elif isinstance(val, bool):
        return {'booleanValue': val}
    elif isinstance(val, int):
        return {'integerValue': str(val)}
    elif isinstance(val, float):
        return {'doubleValue': val}
    elif isinstance(val, str):
        return {'stringValue': val}
    elif isinstance(val, list):
        return {'arrayValue': {'values': [to_firestore_value(x) for x in val]}}
    elif isinstance(val, dict):
        return {'mapValue': {'fields': {k: to_firestore_value(v) for k, v in val.items()}}}
    return {'stringValue': str(val)}

def from_firestore_value(val_obj):
    if not isinstance(val_obj, dict):
        return val_obj
    if 'stringValue' in val_obj:
        return val_obj['stringValue']
    elif 'integerValue' in val_obj:
        return int(val_obj['integerValue'])
    elif 'doubleValue' in val_obj:
        return float(val_obj['doubleValue'])
    elif 'booleanValue' in val_obj:
        return val_obj['booleanValue']
    elif 'nullValue' in val_obj:
        return None
    elif 'arrayValue' in val_obj:
        return [from_firestore_value(v) for v in val_obj['arrayValue'].get('values', [])]
    elif 'mapValue' in val_obj:
        return {k: from_firestore_value(v) for k, v in val_obj['mapValue'].get('fields', {}).items()}
    return None

def get_firebase_status():
    cfg = load_config()
    if not cfg:
        return {
            'enabled': False,
            'status': 'Não configurado',
            'database_id': '',
            'project_id': ''
        }
    base_url, api_key = get_base_url()
    database_id = cfg.get('firestoreDatabaseId', '')
    project_id = cfg.get('projectId', '')
    
    # Test ping
    try:
        r = requests.post(
            f"{base_url}:runQuery?key={api_key}",
            json={'structuredQuery': {'from': [{'collectionId': 'settings'}], 'limit': 1}},
            timeout=4
        )
        connected = (r.status_code == 200)
    except Exception:
        connected = False

    return {
        'enabled': True,
        'connected': connected,
        'status': 'Conectado e Sincronizado' if connected else 'Configurado (Offline)',
        'database_id': database_id,
        'project_id': project_id
    }

def sync_user_profile_to_firebase(user_id, email, name, cpf=None, role=None):
    """
    Sincroniza os dados cadastrais (nome, CPF, perfil) do usuário no Firestore
    para garantir persistência entre sessões, dispositivos (celular/desktop) e deploys.
    """
    base_url, api_key = get_base_url()
    if not base_url:
        return False
    doc_id = f"user_{user_id}"
    payload = {
        'userId': str(user_id),
        'email': str(email or '').lower().strip(),
        'name': str(name or ''),
        'cpf': str(cpf or ''),
        'role': str(role or 'user'),
        'updatedAt': datetime.utcnow().isoformat()
    }
    body = {'fields': {k: to_firestore_value(v) for k, v in payload.items()}}
    try:
        r = requests.patch(
            f"{base_url}/users/{doc_id}?key={api_key}",
            json=body,
            timeout=5
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"Erro ao sincronizar perfil do usuário no Firebase: {e}")
        return False

def pull_user_profile_from_firebase(user_id=None, email=None):
    """
    Recupera os dados de perfil (nome, CPF, role) salvos na nuvem Firestore.
    Suporta busca direta por ID do usuário ou consulta por e-mail.
    """
    base_url, api_key = get_base_url()
    if not base_url:
        return None
    try:
        # 1. Busca direta pelo documento doc_id
        if user_id:
            doc_id = f"user_{user_id}"
            r = requests.get(f"{base_url}/users/{doc_id}?key={api_key}", timeout=5)
            if r.status_code == 200:
                fields = r.json().get('fields', {})
                name = fields.get('name', {}).get('stringValue', '').strip()
                cpf = fields.get('cpf', {}).get('stringValue', '').strip()
                mail = fields.get('email', {}).get('stringValue', '').strip()
                role = fields.get('role', {}).get('stringValue', 'user').strip()
                return {
                    'name': name or None,
                    'cpf': cpf or None,
                    'email': mail or None,
                    'role': role or 'user'
                }

        # 2. Busca pelo e-mail do usuário se o doc_id direto não retornou
        if email:
            query_body = {
                'structuredQuery': {
                    'from': [{'collectionId': 'users'}],
                    'where': {
                        'fieldFilter': {
                            'field': {'fieldPath': 'email'},
                            'op': 'EQUAL',
                            'value': {'stringValue': str(email).lower().strip()}
                        }
                    },
                    'limit': 1
                }
            }
            r_q = requests.post(f"{base_url}:runQuery?key={api_key}", json=query_body, timeout=6)
            if r_q.status_code == 200:
                items = r_q.json()
                for item in items:
                    doc = item.get('document')
                    if doc:
                        fields = doc.get('fields', {})
                        name = fields.get('name', {}).get('stringValue', '').strip()
                        cpf = fields.get('cpf', {}).get('stringValue', '').strip()
                        mail = fields.get('email', {}).get('stringValue', '').strip()
                        role = fields.get('role', {}).get('stringValue', 'user').strip()
                        return {
                            'name': name or None,
                            'cpf': cpf or None,
                            'email': mail or None,
                            'role': role or 'user'
                        }
        return None
    except Exception as e:
        logger.error(f"Erro ao buscar perfil no Firebase: {e}")
        return None

def sync_settings_to_firebase(user_id, settings_data):
    base_url, api_key = get_base_url()
    if not base_url:
        return False
    doc_id = f"user_{user_id}"
    payload = {
        'userId': str(user_id),
        'dailyHoursMinutes': settings_data.get('daily_hours_minutes', 480),
        'defaultStart': settings_data.get('default_start', '08:00'),
        'defaultBreakStart': settings_data.get('default_break_start', '12:00'),
        'defaultBreakEnd': settings_data.get('default_break_end', '13:00'),
        'defaultEnd': settings_data.get('default_end', '17:00'),
        'toleranceMinutes': settings_data.get('tolerance_minutes', 10),
        'bankActive': bool(settings_data.get('bank_active', 1)),
        'initialBalanceMinutes': settings_data.get('initial_balance_minutes', 0),
        'updatedAt': datetime.utcnow().isoformat()
    }
    body = {'fields': {k: to_firestore_value(v) for k, v in payload.items()}}
    try:
        r = requests.patch(
            f"{base_url}/settings/{doc_id}?key={api_key}",
            json=body,
            timeout=5
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"Erro ao sincronizar configurações no Firebase: {e}")
        return False

def sync_work_day_to_firebase(user_id, date, day_type, observation, periods, overtime_paid=0):
    base_url, api_key = get_base_url()
    if not base_url:
        return False
    doc_id = f"user_{user_id}_{date}"
    clean_periods = []
    for p in periods:
        clean_periods.append({
            'periodOrder': p.get('period_order', 1),
            'startTime': p.get('start_time', ''),
            'endTime': p.get('end_time', '')
        })
    payload = {
        'userId': str(user_id),
        'date': str(date),
        'dayType': str(day_type or 'trabalho'),
        'observation': str(observation or ''),
        'overtimePaid': bool(overtime_paid),
        'periods': clean_periods,
        'updatedAt': datetime.utcnow().isoformat()
    }
    body = {'fields': {k: to_firestore_value(v) for k, v in payload.items()}}
    try:
        r = requests.patch(
            f"{base_url}/work_days/{doc_id}?key={api_key}",
            json=body,
            timeout=5
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"Erro ao salvar jornada no Firebase: {e}")
        return False

def delete_work_day_from_firebase(user_id, date):
    base_url, api_key = get_base_url()
    if not base_url:
        return False
    doc_id = f"user_{user_id}_{date}"
    try:
        r = requests.delete(f"{base_url}/work_days/{doc_id}?key={api_key}", timeout=5)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.error(f"Erro ao deletar jornada no Firebase: {e}")
        return False

def reset_user_data_in_firebase(user_id):
    base_url, api_key = get_base_url()
    if not base_url:
        return False
    try:
        query_body = {
            'structuredQuery': {
                'from': [{'collectionId': 'work_days'}],
                'where': {
                    'fieldFilter': {
                        'field': {'fieldPath': 'userId'},
                        'op': 'EQUAL',
                        'value': {'stringValue': str(user_id)}
                    }
                }
            }
        }
        r = requests.post(f"{base_url}:runQuery?key={api_key}", json=query_body, timeout=8)
        if r.status_code == 200:
            items = r.json()
            for item in items:
                doc = item.get('document')
                if doc and 'name' in doc:
                    doc_path = doc['name'].split('/documents/')[-1]
                    requests.delete(f"{base_url}/{doc_path}?key={api_key}", timeout=4)
        return True
    except Exception as e:
        logger.error(f"Erro ao resetar dados no Firebase: {e}")
        return False

def sync_all_local_to_firebase(user_id, conn):
    """
    Exporta todos os registros, configurações e perfil do usuário atual para a nuvem Firestore.
    """
    base_url, api_key = get_base_url()
    if not base_url:
        return False, "Firebase não configurado"
    try:
        cursor = conn.cursor()

        # 0. Sync User Profile (Nome, CPF, E-mail)
        cursor.execute("SELECT id, email, name, cpf, role FROM users WHERE id = ?", (user_id,))
        u_row = cursor.fetchone()
        if u_row:
            sync_user_profile_to_firebase(
                user_id=u_row['id'],
                email=u_row['email'],
                name=u_row['name'],
                cpf=u_row['cpf'],
                role=u_row['role']
            )

        # 1. Sync Settings
        cursor.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,))
        s_row = cursor.fetchone()
        if s_row:
            sync_settings_to_firebase(user_id, dict(s_row))

        # 2. Sync all Work Days
        cursor.execute("SELECT * FROM work_days WHERE user_id = ? ORDER BY date ASC", (user_id,))
        days = cursor.fetchall()
        count = 0
        for d in days:
            cursor.execute("SELECT period_order, start_time, end_time FROM work_periods WHERE work_day_id = ? ORDER BY period_order ASC", (d['id'],))
            periods = [dict(p) for p in cursor.fetchall()]
            sync_work_day_to_firebase(
                user_id,
                d['date'],
                d['day_type'],
                d['observation'],
                periods,
                overtime_paid=d['overtime_paid'] if 'overtime_paid' in d.keys() else 0
            )
            count += 1

        return True, f"Perfil, configurações e {count} dias sincronizados com o Firestore!"
    except Exception as e:
        logger.error(f"Erro na sincronização completa: {e}")
        return False, f"Falha na sincronização: {str(e)}"

def pull_from_firebase(user_id, conn):
    """
    Restaura/sincroniza dados do Firebase Firestore para a base de dados local de forma paralela e ultrarrápida.
    """
    base_url, api_key = get_base_url()
    if not base_url:
        return False, "Firebase não configurado"
    try:
        cursor = conn.cursor()

        # Determina o e-mail do usuário para busca de perfil se necessário
        cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        u_row = cursor.fetchone()
        u_email = u_row['email'] if u_row else None

        doc_id = f"user_{user_id}"
        query_body = {
            'structuredQuery': {
                'from': [{'collectionId': 'work_days'}],
                'where': {
                    'fieldFilter': {
                        'field': {'fieldPath': 'userId'},
                        'op': 'EQUAL',
                        'value': {'stringValue': str(user_id)}
                    }
                }
            }
        }

        # Busca paralela: perfil, configurações e jornadas simultaneamente
        def _fetch_profile():
            try:
                return pull_user_profile_from_firebase(user_id=user_id, email=u_email)
            except Exception:
                return None

        def _fetch_settings():
            try:
                return requests.get(f"{base_url}/settings/{doc_id}?key={api_key}", timeout=5)
            except Exception:
                return None

        def _fetch_days():
            try:
                return requests.post(f"{base_url}:runQuery?key={api_key}", json=query_body, timeout=8)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=3) as executor:
            fut_prof = executor.submit(_fetch_profile)
            fut_settings = executor.submit(_fetch_settings)
            fut_days = executor.submit(_fetch_days)

            prof = fut_prof.result()
            r_set = fut_settings.result()
            r_q = fut_days.result()

        # 0. Atualiza Perfil
        if prof and (prof.get('name') or prof.get('cpf')):
            cursor.execute("""
                UPDATE users 
                SET name = COALESCE(NULLIF(?, ''), name),
                    cpf = COALESCE(NULLIF(?, ''), cpf)
                WHERE id = ?
            """, (prof.get('name'), prof.get('cpf'), user_id))
            conn.commit()

        # 1. Atualiza Settings
        if r_set and r_set.status_code == 200:
            fields = r_set.json().get('fields', {})
            daily_hours = int(fields.get('dailyHoursMinutes', {}).get('integerValue', 480))
            def_start = fields.get('defaultStart', {}).get('stringValue', '08:00')
            def_bstart = fields.get('defaultBreakStart', {}).get('stringValue', '12:00')
            def_bend = fields.get('defaultBreakEnd', {}).get('stringValue', '13:00')
            def_end = fields.get('defaultEnd', {}).get('stringValue', '17:00')
            tol = int(fields.get('toleranceMinutes', {}).get('integerValue', 10))
            bank = 1 if fields.get('bankActive', {}).get('booleanValue', True) else 0
            init_bal = int(fields.get('initialBalanceMinutes', {}).get('integerValue', 0))

            cursor.execute("""
                UPDATE settings 
                SET daily_hours_minutes = ?, default_start = ?, default_break_start = ?,
                    default_break_end = ?, default_end = ?, tolerance_minutes = ?,
                    bank_active = ?, initial_balance_minutes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (daily_hours, def_start, def_bstart, def_bend, def_end, tol, bank, init_bal, user_id))
            conn.commit()

        # 2. Atualiza Work Days
        restored_days = 0
        if r_q and r_q.status_code == 200:
            items = r_q.json()
            for item in items:
                doc = item.get('document')
                if not doc:
                    continue
                fields = doc.get('fields', {})
                d_date = fields.get('date', {}).get('stringValue')
                d_type = fields.get('dayType', {}).get('stringValue', 'trabalho')
                d_obs = fields.get('observation', {}).get('stringValue', '')
                d_ot_paid = 1 if fields.get('overtimePaid', {}).get('booleanValue', False) else 0

                if not d_date:
                    continue

                cursor.execute("""
                    INSERT INTO work_days (user_id, date, day_type, observation, overtime_paid, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, date) DO UPDATE SET
                        day_type = excluded.day_type,
                        observation = excluded.observation,
                        overtime_paid = excluded.overtime_paid,
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, d_date, d_type, d_obs, d_ot_paid))

                cursor.execute("SELECT id FROM work_days WHERE user_id = ? AND date = ?", (user_id, d_date))
                day_row = cursor.fetchone()
                if day_row:
                    w_day_id = day_row[0]
                    cursor.execute("DELETE FROM work_periods WHERE work_day_id = ?", (w_day_id,))

                    periods_val = fields.get('periods', {}).get('arrayValue', {}).get('values', [])
                    for p_raw in periods_val:
                        p_map = p_raw.get('mapValue', {}).get('fields', {})
                        p_order = int(p_map.get('periodOrder', {}).get('integerValue', 1))
                        p_st = p_map.get('startTime', {}).get('stringValue', '')
                        p_et = p_map.get('endTime', {}).get('stringValue', '')
                        if p_st or p_et:
                            cursor.execute("""
                                INSERT INTO work_periods (work_day_id, period_order, start_time, end_time)
                                VALUES (?, ?, ?, ?)
                            """, (w_day_id, p_order, p_st, p_et))

                restored_days += 1

        conn.commit()
        return True, f"{restored_days} registros recuperados da nuvem Firebase!"
    except Exception as e:
        logger.error(f"Erro ao restaurar do Firebase: {e}")
        return False, f"Falha ao restaurar: {str(e)}"

