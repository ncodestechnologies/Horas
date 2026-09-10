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

def calculate_day_metrics(periods: list, daily_expected_minutes: int = 480, day_type: str = 'trabalho', tolerance_minutes: int = 0, overtime_paid: bool = False):
    """
    Calcula métricas para um dia de trabalho.
    periods: lista de tuplas ou dicionários [{'start_time': '08:00', 'end_time': '12:00'}, ...]
    overtime_paid: se True, as horas extras foram pagas e não são somadas ao saldo de banco (não abatem negativo).
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
            'overtime_paid': False,
            'overtime_paid_minutes': 0,
            'overtime_paid_str': '00:00',
            'missing_minutes': 0,
            'missing_str': '00:00',
            'balance_minutes': 0,
            'balance_str': '00:00',
            'raw_balance_minutes': 0,
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
            'overtime_paid': False,
            'overtime_paid_minutes': 0,
            'overtime_paid_str': '00:00',
            'missing_minutes': daily_expected_minutes,
            'missing_str': minutes_to_time_str(daily_expected_minutes),
            'balance_minutes': -daily_expected_minutes,
            'balance_str': minutes_to_balance_str(-daily_expected_minutes),
            'raw_balance_minutes': -daily_expected_minutes,
            'status': 'falta',
            'periods_count': 0,
            'periods': [],
            'first_in': '-',
            'last_out': '-'
        }

    total_worked = 0
    clean_periods = []
    is_in_progress = False
    
    # Ordena períodos por horário inicial
    sorted_periods = sorted(periods, key=lambda p: time_to_minutes(p.get('start_time', '00:00') if isinstance(p, dict) else p[0]))

    for p in sorted_periods:
        st = p.get('start_time') if isinstance(p, dict) else p[0]
        et = p.get('end_time') if isinstance(p, dict) else p[1]
        st = (st or '').strip()
        et = (et or '').strip()

        if st and et:
            dur = calculate_period_duration(st, et)
            crosses = time_to_minutes(et) < time_to_minutes(st)
            if dur > 0:
                total_worked += dur
                clean_periods.append({
                    'start_time': st,
                    'end_time': et,
                    'duration_minutes': dur,
                    'duration_str': minutes_to_time_str(dur),
                    'crosses_midnight': crosses,
                    'end_time_display': f"{et} (+1d)" if crosses else et,
                    'is_open': False
                })
        elif st and not et:
            # Período parcial registrado em tempo real (em andamento)
            is_in_progress = True
            clean_periods.append({
                'start_time': st,
                'end_time': '',
                'duration_minutes': 0,
                'duration_str': 'Em andamento',
                'crosses_midnight': False,
                'end_time_display': 'Em aberto',
                'is_open': True
            })

    # Calcula intervalos entre períodos consecutivos fechados
    break_minutes = 0
    for i in range(len(clean_periods) - 1):
        curr_end = clean_periods[i]['end_time']
        next_start = clean_periods[i + 1]['start_time']
        if curr_end and next_start:
            b_dur = calculate_period_duration(curr_end, next_start)
            break_minutes += b_dur

    first_in = clean_periods[0]['start_time'] if clean_periods else '-'
    if clean_periods:
        if clean_periods[-1].get('is_open'):
            last_out = 'Em aberto'
            last_out_display = 'Em aberto (trabalhando)'
        else:
            last_out = clean_periods[-1]['end_time']
            last_out_display = clean_periods[-1].get('end_time_display', last_out)
    else:
        last_out = '-'
        last_out_display = '-'

    has_overnight = any(p.get('crosses_midnight') for p in clean_periods)

    # Saldo e tolerância
    raw_balance = total_worked - daily_expected_minutes
    effective_balance = raw_balance

    # Se estiver dentro da margem de tolerância (ex: +/- 10 min), saldo é zero
    if tolerance_minutes > 0 and abs(raw_balance) <= tolerance_minutes:
        effective_balance = 0

    if is_in_progress:
        # Dia em andamento (horários parciais registrados no momento do acontecimento)
        # O colaborador ainda está trabalhando: NÃO gera saldo negativo nem débito antecipado!
        status = 'em_andamento'
        if effective_balance > 0:
            overtime = effective_balance
            missing = 0
            bank_balance = 0 if overtime_paid else effective_balance
            balance_str = minutes_to_balance_str(bank_balance) if not overtime_paid else "00:00"
        else:
            overtime = 0
            missing = max(0, daily_expected_minutes - total_worked)
            bank_balance = 0
            balance_str = "Em aberto"
    elif effective_balance > 0:
        overtime = effective_balance
        missing = 0
        if overtime_paid:
            # Hora extra foi paga em folha: NÃO computa no saldo positivo do banco de horas,
            # impedindo que abata ou compense saldo devedor/negativo
            bank_balance = 0
            balance_str = "00:00"
        else:
            bank_balance = effective_balance
            balance_str = minutes_to_balance_str(effective_balance)
        status = 'registrado'
    elif effective_balance < 0:
        overtime = 0
        missing = abs(effective_balance)
        bank_balance = effective_balance
        balance_str = minutes_to_balance_str(effective_balance)
        status = 'incompleto'
    else:
        overtime = 0
        missing = 0
        bank_balance = 0
        balance_str = "00:00"
        status = 'registrado'

    if not clean_periods:
        status = 'vazio'

    return {
        'total_worked_minutes': total_worked,
        'total_worked_str': minutes_to_time_str(total_worked),
        'break_minutes': break_minutes,
        'break_str': minutes_to_time_str(break_minutes),
        'has_break': (len(clean_periods) > 1 and break_minutes > 0),
        'expected_minutes': daily_expected_minutes,
        'expected_str': minutes_to_time_str(daily_expected_minutes),
        'overtime_minutes': overtime,
        'overtime_str': minutes_to_time_str(overtime),
        'overtime_paid': bool(overtime_paid),
        'overtime_paid_minutes': overtime if overtime_paid else 0,
        'overtime_paid_str': minutes_to_time_str(overtime if overtime_paid else 0),
        'missing_minutes': missing,
        'missing_str': minutes_to_time_str(missing),
        'balance_minutes': bank_balance,
        'balance_str': balance_str,
        'raw_balance_minutes': effective_balance,
        'status': status,
        'is_in_progress': is_in_progress,
        'periods_count': len(clean_periods),
        'periods': clean_periods,
        'first_in': first_in,
        'last_out': last_out,
        'last_out_display': last_out_display,
        'has_overnight': has_overnight
    }

def validate_periods(periods_input: list):
    """
    Valida lista de períodos recebidos do formulário.
    Suporta preenchimento parcial em tempo real (ex: entrada registrada sem saída ainda).
    Retorna (is_valid: bool, error_message: str, cleaned_periods: list)
    """
    cleaned = []
    has_open_previous = False
    open_idx = 0

    for idx, (st, et) in enumerate(periods_input, start=1):
        st = (st or '').strip()
        et = (et or '').strip()
        
        # Ambos vazios: ignora
        if not st and not et:
            continue

        # Saída informada sem horário de entrada: inválido
        if not st and et:
            return False, f"O Período {idx} possui horário de saída ('{et}') sem horário de entrada. Informe a entrada primeiro.", []

        # Se o período anterior ainda está em aberto e o usuário tentou iniciar outro:
        if has_open_previous:
            return False, f"O Período {open_idx} ainda está em aberto. Preencha o horário de término dele antes de iniciar o Período {idx}.", []

        # Validar formato da entrada
        parts_st = st.split(':')
        if len(parts_st) != 2 or not parts_st[0].isdigit() or not parts_st[1].isdigit():
            return False, f"Formato inválido na Entrada do Período {idx} ('{st}'). Utilize HH:MM.", []
        h, m = int(parts_st[0]), int(parts_st[1])
        if h < 0 or h > 23 or m < 0 or m > 59:
            return False, f"Horário fora dos limites na Entrada do Período {idx} ('{st}'). Horas: 00-23, Minutos: 00-59.", []

        # Validar saída caso preenchida
        if et:
            parts_et = et.split(':')
            if len(parts_et) != 2 or not parts_et[0].isdigit() or not parts_et[1].isdigit():
                return False, f"Formato inválido na Saída do Período {idx} ('{et}'). Utilize HH:MM.", []
            h_e, m_e = int(parts_et[0]), int(parts_et[1])
            if h_e < 0 or h_e > 23 or m_e < 0 or m_e > 59:
                return False, f"Horário fora dos limites na Saída do Período {idx} ('{et}'). Horas: 00-23, Minutos: 00-59.", []

            dur = calculate_period_duration(st, et)
            if dur == 0:
                return False, f"O Período {idx} possui horário de entrada igual à saída ({st}).", []
        else:
            # Período parcial em aberto
            has_open_previous = True
            open_idx = idx

        cleaned.append((st, et))

    if not cleaned:
        return True, "", []

    # Checar sobreposição de períodos fechados
    closed_periods = [p for p in cleaned if p[0] and p[1]]
    for i in range(len(closed_periods)):
        s1 = time_to_minutes(closed_periods[i][0])
        e1 = time_to_minutes(closed_periods[i][1])
        if e1 < s1:
            e1 += 1440
        for j in range(i + 1, len(closed_periods)):
            s2 = time_to_minutes(closed_periods[j][0])
            e2 = time_to_minutes(closed_periods[j][1])
            if e2 < s2:
                e2 += 1440
            
            # Checa se [s1, e1] e [s2, e2] se sobrepõem
            if max(s1, s2) < min(e1, e2):
                return False, f"Os períodos ({closed_periods[i][0]}-{closed_periods[i][1]}) e ({closed_periods[j][0]}-{closed_periods[j][1]}) estão sobrepostos.", []

    return True, "", cleaned
