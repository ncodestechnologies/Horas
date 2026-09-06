"""
Serviço de geração de relatórios e agregação de dados.
Executado 100% no Python.
"""
from datetime import datetime, timedelta, date
from database.db import get_db
from services.calculations import (
    calculate_day_metrics,
    minutes_to_time_str,
    minutes_to_balance_str
)

def get_user_settings(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        'daily_hours_minutes': 480,
        'default_start': '08:00',
        'default_break_start': '12:00',
        'default_break_end': '13:00',
        'default_end': '17:00',
        'tolerance_minutes': 10,
        'bank_active': 1,
        'initial_balance_minutes': 0
    }

def get_days_data_in_range(user_id: int, start_date: str, end_date: str):
    """
    Busca todos os dias registrados e seus períodos no intervalo [start_date, end_date].
    Retorna lista ordenada por data crescente.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT wd.id as day_id, wd.date, wd.day_type, wd.observation,
               wp.period_order, wp.start_time, wp.end_time
        FROM work_days wd
        LEFT JOIN work_periods wp ON wd.id = wp.work_day_id
        WHERE wd.user_id = ? AND wd.date >= ? AND wd.date <= ?
        ORDER BY wd.date ASC, wp.period_order ASC
    """, (user_id, start_date, end_date))
    rows = cursor.fetchall()
    conn.close()

    days_dict = {}
    for r in rows:
        d_id = r['day_id']
        d_date = r['date']
        if d_date not in days_dict:
            days_dict[d_date] = {
                'id': d_id,
                'date': d_date,
                'day_type': r['day_type'],
                'observation': r['observation'] or '',
                'periods': []
            }
        if r['start_time'] and r['end_time']:
            days_dict[d_date]['periods'].append({
                'start_time': r['start_time'],
                'end_time': r['end_time']
            })

    settings = get_user_settings(user_id)
    daily_expected = settings.get('daily_hours_minutes', 480)
    tolerance = settings.get('tolerance_minutes', 10)

    results = []
    for d_date in sorted(days_dict.keys()):
        item = days_dict[d_date]
        metrics = calculate_day_metrics(
            item['periods'],
            daily_expected_minutes=daily_expected,
            day_type=item['day_type'],
            tolerance_minutes=tolerance
        )
        item.update(metrics)
        results.append(item)

    return results, settings

def generate_report_summary(user_id: int, start_date: str, end_date: str):
    """
    Gera relatório completo com métricas totais e lista detalhada de dias.
    """
    days, settings = get_days_data_in_range(user_id, start_date, end_date)

    total_worked_mins = 0
    total_expected_mins = 0
    total_overtime_mins = 0
    total_missing_mins = 0
    total_balance_mins = 0
    worked_days_count = 0
    off_days_count = 0

    unexcused_absences_count = 0

    for d in days:
        if d['day_type'] == 'trabalho' and d['periods_count'] > 0:
            worked_days_count += 1
            total_worked_mins += d['total_worked_minutes']
            total_expected_mins += d['expected_minutes']
            total_overtime_mins += d['overtime_minutes']
            total_missing_mins += d['missing_minutes']
            total_balance_mins += d['balance_minutes']
        elif d['day_type'] == 'falta':
            # Falta sem justificativa / dia a pagar
            unexcused_absences_count += 1
            total_expected_mins += d['expected_minutes']
            total_missing_mins += d['missing_minutes']
            total_balance_mins += d['balance_minutes']
        elif d['day_type'] in ('folga', 'feriado', 'atestado', 'compensacao'):
            off_days_count += 1

    average_daily_mins = int(total_worked_mins / worked_days_count) if worked_days_count > 0 else 0

    return {
        'start_date': start_date,
        'end_date': end_date,
        'total_worked_minutes': total_worked_mins,
        'total_worked_str': minutes_to_time_str(total_worked_mins),
        'total_expected_minutes': total_expected_mins,
        'total_expected_str': minutes_to_time_str(total_expected_mins),
        'total_overtime_minutes': total_overtime_mins,
        'total_overtime_str': minutes_to_time_str(total_overtime_mins),
        'total_missing_minutes': total_missing_mins,
        'total_missing_str': minutes_to_time_str(total_missing_mins),
        'total_balance_minutes': total_balance_mins,
        'total_balance_str': minutes_to_balance_str(total_balance_mins),
        'worked_days_count': worked_days_count,
        'off_days_count': off_days_count,
        'unexcused_absences_count': unexcused_absences_count,
        'average_daily_str': minutes_to_time_str(average_daily_mins),
        'days': days,
        'settings': settings
    }

def get_cumulative_bank_balance(user_id: int, up_to_date: str = None):
    """
    Calcula o saldo acumulado total do banco de horas até a data especificada (ou total).
    Inclui saldo inicial cadastrado nas configurações.
    """
    settings = get_user_settings(user_id)
    if not settings.get('bank_active', 1):
        return 0, "00:00"

    initial_balance = settings.get('initial_balance_minutes', 0)
    
    conn = get_db()
    cursor = conn.cursor()
    if up_to_date:
        cursor.execute("""
            SELECT wd.id, wd.day_type, wp.start_time, wp.end_time
            FROM work_days wd
            LEFT JOIN work_periods wp ON wd.id = wp.work_day_id
            WHERE wd.user_id = ? AND wd.date <= ?
            ORDER BY wd.date ASC, wp.period_order ASC
        """, (user_id, up_to_date))
    else:
        cursor.execute("""
            SELECT wd.id, wd.day_type, wp.start_time, wp.end_time
            FROM work_days wd
            LEFT JOIN work_periods wp ON wd.id = wp.work_day_id
            WHERE wd.user_id = ?
            ORDER BY wd.date ASC, wp.period_order ASC
        """, (user_id,))

    rows = cursor.fetchall()
    conn.close()

    grouped = {}
    for r in rows:
        d_id = r['id']
        if d_id not in grouped:
            grouped[d_id] = {'day_type': r['day_type'], 'periods': []}
        if r['start_time'] and r['end_time']:
            grouped[d_id]['periods'].append({'start_time': r['start_time'], 'end_time': r['end_time']})

    daily_expected = settings.get('daily_hours_minutes', 480)
    tolerance = settings.get('tolerance_minutes', 10)
    
    total_balance = initial_balance
    for d_id, data in grouped.items():
        if data['day_type'] == 'trabalho' and data['periods']:
            m = calculate_day_metrics(data['periods'], daily_expected, 'trabalho', tolerance)
            total_balance += m['balance_minutes']
        elif data['day_type'] == 'falta':
            m = calculate_day_metrics([], daily_expected, 'falta', tolerance)
            total_balance += m['balance_minutes']

    return total_balance, minutes_to_balance_str(total_balance)
