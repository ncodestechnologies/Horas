"""
Cálculos de jornada de trabalho, intervalos, horas extras, faltantes e banco de horas.
Executado 100% no Python.
"""
from datetime import datetime, time

def time_to_minutes(time_str: str) -> int:
    """Converte string 'HH:MM' em minutos desde a meia-noite."""
    if not time_str or ':' not in time_str:
        return 0
    parts = time_str.strip().split(':')
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        return hours * 60 + minutes
    except (ValueError, IndexError):
        return 0

def minutes_to_time_str(minutes: int) -> str:
    """Converte minutos inteiros para string 'HH:MM'."""
    minutes = max(0, int(minutes))
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"

def minutes_to_balance_str(minutes: int) -> str:
    """Converte minutos em string de saldo com sinal '+HH:MM' ou '-HH:MM'."""
    mins = int(minutes)
    if mins > 0:
        h = mins // 60
        m = mins % 60
        return f"+{h:02d}:{m:02d}"
    elif mins < 0:
        abs_m = abs(mins)
        h = abs_m // 60
        m = abs_m % 60
        return f"-{h:02d}:{m:02d}"
    else:
        return "00:00"

def calculate_period_duration(start_str: str, end_str: str) -> int:
    """
    Calcula a duração em minutos entre start_str e end_str ('HH:MM').
    Reconhece jornadas noturnas que atravessam a meia-noite.
    Exemplo: 22:00 até 06:00 => 8 horas (480 minutos).
    """
    start_min = time_to_minutes(start_str)
    end_min = time_to_minutes(end_str)

    if end_min < start_min:
        # Atravessou a meia-noite: adiciona 1440 minutos (24 horas)
        return (end_min + 1440) - start_min
    elif end_min == start_min:
        return 0
    else:
        return end_min - start_min

def calculate_day_metrics(periods: list, daily_expected_minutes: int = 480, day_type: str = 'trabalho', tolerance_minutes: int = 0):
    """
    Calcula métricas para um dia de trabalho.
    periods: lista de tuplas ou dicionários [{'start_time': '08:00', 'end_time': '12:00'}, ...]
    Retorna dicionário completo com os cálculos.
    """
    if day_type in ('folga', 'feriado', 'atestado', 'compensacao'):
        return {
            'total_worked_minutes': 0,
            'total_worked_str': '00:00',
            'break_minutes': 0,
            'break_str': '00:00',
            'expected_minutes': 0,
            'expected_str': '00:00',
            'overtime_minutes': 0,
            'overtime_str': '00:00',
            'missing_minutes': 0,
            'missing_str': '00:00',
            'balance_minutes': 0,
            'balance_str': '00:00',
            'status': day_type,
            'periods_count': 0,
            'periods': [],
            'first_in': '-',
            'last_out': '-'
        }
    elif day_type == 'falta':
        # Falta sem justificativa / dia a pagar
        # 0 horas trabalhadas, carga diária esperada completa gerada como débito a pagar
        return {
            'total_worked_minutes': 0,
            'total_worked_str': '00:00',
            'break_minutes': 0,
            'break_str': '00:00',
            'expected_minutes': daily_expected_minutes,
            'expected_str': minutes_to_time_str(daily_expected_minutes),
            'overtime_minutes': 0,
            'overtime_str': '00:00',
            'missing_minutes': daily_expected_minutes,
            'missing_str': minutes_to_time_str(daily_expected_minutes),
            'balance_minutes': -daily_expected_minutes,
            'balance_str': minutes_to_balance_str(-daily_expected_minutes),
            'status': 'falta',
            'periods_count': 0,
            'periods': [],
            'first_in': '-',
            'last_out': '-'
        }

    total_worked = 0
    clean_periods = []
    
    # Ordena períodos por horário inicial
    sorted_periods = sorted(periods, key=lambda p: time_to_minutes(p.get('start_time', '00:00') if isinstance(p, dict) else p[0]))

    for p in sorted_periods:
        st = p.get('start_time') if isinstance(p, dict) else p[0]
        et = p.get('end_time') if isinstance(p, dict) else p[1]
        if st and et:
            dur = calculate_period_duration(st, et)
            if dur > 0:
                total_worked += dur
                clean_periods.append({
                    'start_time': st,
                    'end_time': et,
                    'duration_minutes': dur,
                    'duration_str': minutes_to_time_str(dur)
                })

    # Calcula intervalos entre períodos consecutivos
    break_minutes = 0
    for i in range(len(clean_periods) - 1):
        curr_end = clean_periods[i]['end_time']
        next_start = clean_periods[i + 1]['start_time']
        # intervalo entre curr_end e next_start
        b_dur = calculate_period_duration(curr_end, next_start)
        break_minutes += b_dur

    first_in = clean_periods[0]['start_time'] if clean_periods else '-'
    last_out = clean_periods[-1]['end_time'] if clean_periods else '-'

    # Saldo e tolerância
    raw_balance = total_worked - daily_expected_minutes
    effective_balance = raw_balance

    # Se estiver dentro da margem de tolerância (ex: +/- 10 min), saldo é zero
    if tolerance_minutes > 0 and abs(raw_balance) <= tolerance_minutes:
        effective_balance = 0

    if effective_balance > 0:
        overtime = effective_balance
        missing = 0
    elif effective_balance < 0:
        overtime = 0
        missing = abs(effective_balance)
    else:
        overtime = 0
        missing = 0

    # Status: 'completo', 'positivo', 'negativo', 'incompleto', 'vazio'
    if not clean_periods:
        status = 'vazio'
    elif effective_balance < 0:
        status = 'incompleto'
    else:
        status = 'registrado'

    return {
        'total_worked_minutes': total_worked,
        'total_worked_str': minutes_to_time_str(total_worked),
        'break_minutes': break_minutes,
        'break_str': minutes_to_time_str(break_minutes),
        'expected_minutes': daily_expected_minutes,
        'expected_str': minutes_to_time_str(daily_expected_minutes),
        'overtime_minutes': overtime,
        'overtime_str': minutes_to_time_str(overtime),
        'missing_minutes': missing,
        'missing_str': minutes_to_time_str(missing),
        'balance_minutes': effective_balance,
        'balance_str': minutes_to_balance_str(effective_balance),
        'status': status,
        'periods_count': len(clean_periods),
        'periods': clean_periods,
        'first_in': first_in,
        'last_out': last_out
    }

def validate_periods(periods_input: list):
    """
    Valida lista de períodos recebidos do formulário.
    Retorna (is_valid: bool, error_message: str, cleaned_periods: list)
    """
    cleaned = []
    for idx, (st, et) in enumerate(periods_input, start=1):
        st = (st or '').strip()
        et = (et or '').strip()
        
        # Ambos vazios: ignora
        if not st and not et:
            continue
        # Um preenchido e outro vazio: erro
        if not st or not et:
            return False, f"O Período {idx} está incompleto. Preencha o horário de entrada e saída.", []
        
        # Validar formato
        for val, label in [(st, f"Entrada do Período {idx}"), (et, f"Saída do Período {idx}")]:
            parts = val.split(':')
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                return False, f"Formato inválido em {label} ('{val}'). Utilize HH:MM.", []
            h, m = int(parts[0]), int(parts[1])
            if h < 0 or h > 23 or m < 0 or m > 59:
                return False, f"Horário fora dos limites em {label} ('{val}'). Horas: 00-23, Minutos: 00-59.", []

        dur = calculate_period_duration(st, et)
        if dur == 0:
            return False, f"O Período {idx} possui horário de entrada igual à saída ({st}).", []

        cleaned.append((st, et))

    if not cleaned:
        return True, "", []

    # Checar sobreposição de períodos (para jornadas sem cruzar meia-noite simples)
    # Convertemos em intervalos para verificação
    for i in range(len(cleaned)):
        s1 = time_to_minutes(cleaned[i][0])
        e1 = time_to_minutes(cleaned[i][1])
        if e1 < s1:
            e1 += 1440
        for j in range(i + 1, len(cleaned)):
            s2 = time_to_minutes(cleaned[j][0])
            e2 = time_to_minutes(cleaned[j][1])
            if e2 < s2:
                e2 += 1440
            
            # Checa se [s1, e1] e [s2, e2] se sobrepõem
            if max(s1, s2) < min(e1, e2):
                return False, f"Os períodos ({cleaned[i][0]}-{cleaned[i][1]}) e ({cleaned[j][0]}-{cleaned[j][1]}) estão sobrepostos.", []

    return True, "", cleaned
