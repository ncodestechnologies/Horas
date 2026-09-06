"""
Exportação de relatórios em CSV, Excel (.xlsx) e PDF.
Gerados 100% no servidor Python.
"""
import io
import csv
from datetime import datetime
from services.reports import generate_report_summary

def export_to_csv(user_id: int, start_date: str, end_date: str) -> io.BytesIO:
    """Gera arquivo CSV codificado com UTF-8 BOM e separador ';' para Excel no Brasil."""
    summary = generate_report_summary(user_id, start_date, end_date)
    
    output = io.StringIO()
    # UTF-8 BOM para garantir compatibilidade com Excel
    writer = csv.writer(output, delimiter=';')
    
    # Cabeçalho do Relatório
    writer.writerow(["MEU CONTROLE DE HORAS - RELATÓRIO"])
    writer.writerow([f"Período: {start_date} a {end_date}"])
    writer.writerow([f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}"])
    writer.writerow([])
    
    # Resumo
    writer.writerow(["RESUMO DO PERÍODO"])
    writer.writerow(["Total Trabalhado", summary['total_worked_str']])
    writer.writerow(["Total Previsto", summary['total_expected_str']])
    writer.writerow(["Horas Extras", summary['total_overtime_str']])
    writer.writerow(["Horas Faltantes", summary['total_missing_str']])
    writer.writerow(["Saldo do Período", summary['total_balance_str']])
    writer.writerow(["Dias Trabalhados", summary['worked_days_count']])
    writer.writerow(["Dias de Folga", summary['off_days_count']])
    writer.writerow(["Média Diária", summary['average_daily_str']])
    writer.writerow([])
    
    # Tabela detalhada
    writer.writerow([
        "Data", "Tipo", "Entrada", "Saída Int.", "Retorno Int.", "Saída Final",
        "Trabalhado", "Previsto", "Extras", "Faltantes", "Saldo", "Observações"
    ])
    
    for d in summary['days']:
        periods = d.get('periods', [])
        # Extrai horários
        first_in = periods[0]['start_time'] if len(periods) > 0 else '-'
        break_out = periods[0]['end_time'] if len(periods) > 1 else '-'
        break_in = periods[1]['start_time'] if len(periods) > 1 else '-'
        last_out = periods[-1]['end_time'] if len(periods) > 0 else '-'
        
        # Se tiver mais de 2 períodos, indica detalhes
        if len(periods) > 2:
            obs_periods = " [" + ", ".join([f"{p['start_time']}-{p['end_time']}" for p in periods]) + "]"
        else:
            obs_periods = ""

        # Formata data para DD/MM/AAAA
        dt_parts = d['date'].split('-')
        data_fmt = f"{dt_parts[2]}/{dt_parts[1]}/{dt_parts[0]}" if len(dt_parts) == 3 else d['date']
        
        tipo_fmt = {
            'trabalho': 'Normal',
            'folga': 'Folga',
            'feriado': 'Feriado',
            'falta': 'Falta (A Pagar)',
            'atestado': 'Atestado',
            'compensacao': 'Compensação'
        }.get(d['day_type'], d['day_type'].capitalize())

        writer.writerow([
            data_fmt,
            tipo_fmt,
            first_in,
            break_out,
            break_in,
            last_out,
            d['total_worked_str'],
            d['expected_str'],
            d['overtime_str'],
            d['missing_str'],
            d['balance_str'],
            (d['observation'] + obs_periods).strip()
        ])

    bytes_output = io.BytesIO()
    # Adiciona BOM para que o Excel abra sem problemas de acentuação
    bytes_output.write(b'\xef\xbb\xbf')
    bytes_output.write(output.getvalue().encode('utf-8'))
    bytes_output.seek(0)
    return bytes_output

def export_to_excel(user_id: int, start_date: str, end_date: str) -> io.BytesIO:
    """Gera planilha Excel (.xlsx) estilizada e formatada usando openpyxl."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    summary = generate_report_summary(user_id, start_date, end_date)
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Controle de Horas"
    ws.views.sheetView[0].showGridLines = True

    # Estilos
    title_font = Font(name="Calibri", size=16, bold=True, color="1E3A8A")
    subtitle_font = Font(name="Calibri", size=11, italic=True, color="64748B")
    section_font = Font(name="Calibri", size=12, bold=True, color="0F172A")
    
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    
    subtotal_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    subtotal_font = Font(name="Calibri", size=11, bold=True, color="0F172A")
    
    thin_border_side = Side(border_style="thin", color="CBD5E1")
    border_all = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    
    pos_font = Font(name="Calibri", size=10, bold=True, color="059669")
    neg_font = Font(name="Calibri", size=10, bold=True, color="DC2626")
    neutral_font = Font(name="Calibri", size=10, color="0F172A")
    
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # Título
    ws["A1"] = "MEU CONTROLE DE HORAS"
    ws["A1"].font = title_font
    ws["A2"] = f"Relatório de {start_date} a {end_date} • Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws["A2"].font = subtitle_font

    # Bloco Resumo
    ws["A4"] = "RESUMO GERAL"
    ws["A4"].font = section_font

    cards = [
        ("Total Trabalhado", summary['total_worked_str']),
        ("Total Previsto", summary['total_expected_str']),
        ("Horas Extras", summary['total_overtime_str']),
        ("Horas Faltantes", summary['total_missing_str']),
        ("Saldo Período", summary['total_balance_str']),
        ("Dias Trabalhados", str(summary['worked_days_count'])),
        ("Média Diária", summary['average_daily_str']),
    ]

    row_idx = 5
    for label, val in cards:
        ws.cell(row=row_idx, column=1, value=label).font = Font(name="Calibri", size=10, bold=True, color="334155")
        val_cell = ws.cell(row=row_idx, column=2, value=val)
        val_cell.font = Font(name="Calibri", size=10, bold=True)
        if "+" in val:
            val_cell.font = pos_font
        elif "-" in val:
            val_cell.font = neg_font
        row_idx += 1

    # Cabeçalho da Tabela
    table_start_row = row_idx + 2
    headers = [
        "Data", "Tipo", "Entrada", "Saída Int.", "Retorno Int.", "Saída Final",
        "Trabalhado", "Previsto", "Extras", "Faltantes", "Saldo", "Observações"
    ]

    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=table_start_row, column=col_idx, value=h)
        c.fill = header_fill
        c.font = header_font
        c.alignment = align_center
        c.border = border_all

    curr_row = table_start_row + 1
    for d in summary['days']:
        periods = d.get('periods', [])
        first_in = periods[0]['start_time'] if len(periods) > 0 else '-'
        break_out = periods[0]['end_time'] if len(periods) > 1 else '-'
        break_in = periods[1]['start_time'] if len(periods) > 1 else '-'
        last_out = periods[-1]['end_time'] if len(periods) > 0 else '-'

        dt_parts = d['date'].split('-')
        data_fmt = f"{dt_parts[2]}/{dt_parts[1]}/{dt_parts[0]}" if len(dt_parts) == 3 else d['date']
        
        tipo_fmt = {
            'trabalho': 'Normal',
            'folga': 'Folga',
            'feriado': 'Feriado',
            'falta': 'Falta (A Pagar)',
            'atestado': 'Atestado',
            'compensacao': 'Compensação'
        }.get(d['day_type'], d['day_type'].capitalize())

        vals = [
            (data_fmt, align_center, neutral_font),
            (tipo_fmt, align_center, neutral_font),
            (first_in, align_center, neutral_font),
            (break_out, align_center, neutral_font),
            (break_in, align_center, neutral_font),
            (last_out, align_center, neutral_font),
            (d['total_worked_str'], align_center, Font(name="Calibri", size=10, bold=True)),
            (d['expected_str'], align_center, neutral_font),
            (d['overtime_str'], align_center, pos_font if d['overtime_minutes'] > 0 else neutral_font),
            (d['missing_str'], align_center, neg_font if d['missing_minutes'] > 0 else neutral_font),
            (d['balance_str'], align_center, pos_font if '+' in d['balance_str'] else (neg_font if '-' in d['balance_str'] else neutral_font)),
            (d['observation'], align_left, neutral_font)
        ]

        row_fill = PatternFill(start_color="FFFFFF" if curr_row % 2 == 0 else "F8FAFC", fill_type="solid")

        for col_idx, (val, align, font) in enumerate(vals, start=1):
            cell = ws.cell(row=curr_row, column=col_idx, value=val)
            cell.alignment = align
            cell.font = font
            cell.border = border_all
            cell.fill = row_fill

        curr_row += 1

    # Linha de Totais
    total_cells = [
        ("TOTAL", align_center, subtotal_font),
        ("", align_center, subtotal_font),
        ("", align_center, subtotal_font),
        ("", align_center, subtotal_font),
        ("", align_center, subtotal_font),
        ("", align_center, subtotal_font),
        (summary['total_worked_str'], align_center, subtotal_font),
        (summary['total_expected_str'], align_center, subtotal_font),
        (summary['total_overtime_str'], align_center, pos_font),
        (summary['total_missing_str'], align_center, neg_font),
        (summary['total_balance_str'], align_center, pos_font if '+' in summary['total_balance_str'] else neg_font),
        ("", align_left, subtotal_font)
    ]
    for col_idx, (val, align, font) in enumerate(total_cells, start=1):
        cell = ws.cell(row=curr_row, column=col_idx, value=val)
        cell.alignment = align
        cell.font = font
        cell.border = border_all
        cell.fill = subtotal_fill

    # Ajuste automático de largura das colunas
    col_widths = {1: 12, 2: 12, 3: 10, 4: 11, 5: 11, 6: 11, 7: 12, 8: 11, 9: 10, 10: 10, 11: 10, 12: 30}
    for col_num, width in col_widths.items():
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_num)].width = width

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

def export_to_pdf(user_id: int, start_date: str, end_date: str) -> io.BytesIO:
    """Gera relatório PDF diagramado e estilizado usando reportlab."""
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

    summary = generate_report_summary(user_id, start_date, end_date)

    output = io.BytesIO()
    # Usar orientação paisagem (landscape) para que caibam todas as colunas perfeitamente
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(letter),
        leftMargin=30,
        rightMargin=30,
        topMargin=30,
        bottomMargin=30
    )

    story = []
    styles = getSampleStyleSheet()

    # Estilos customizados
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#1e3a8a')
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#64748b')
    )
    th_style = ParagraphStyle(
        'TableHeader',
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        alignment=1, # Center
        textColor=colors.white
    )
    td_style = ParagraphStyle(
        'TableCell',
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        alignment=1,
        textColor=colors.HexColor('#0f172a')
    )
    td_left = ParagraphStyle(
        'TableCellLeft',
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        alignment=0,
        textColor=colors.HexColor('#0f172a')
    )
    td_bold = ParagraphStyle(
        'TableCellBold',
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        alignment=1,
        textColor=colors.HexColor('#0f172a')
    )

    story.append(Paragraph("Meu Controle de Horas — Relatório de Jornada", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"Período selecionado: <b>{start_date}</b> a <b>{end_date}</b> • Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", subtitle_style))
    story.append(Spacer(1, 14))

    # Tabela de Métricas Resumo
    metrics_data = [
        [
            Paragraph("<b>Total Trabalhado</b><br/><font size=12 color='#1e3a8a'>" + summary['total_worked_str'] + "</font>", td_style),
            Paragraph("<b>Total Previsto</b><br/><font size=12 color='#475569'>" + summary['total_expected_str'] + "</font>", td_style),
            Paragraph("<b>Horas Extras</b><br/><font size=12 color='#059669'>" + summary['total_overtime_str'] + "</font>", td_style),
            Paragraph("<b>Horas Faltantes</b><br/><font size=12 color='#dc2626'>" + summary['total_missing_str'] + "</font>", td_style),
            Paragraph("<b>Saldo Período</b><br/><font size=12 color='" + ('#059669' if '+' in summary['total_balance_str'] else '#dc2626') + "'>" + summary['total_balance_str'] + "</font>", td_style),
            Paragraph("<b>Dias Trabalhados</b><br/><font size=12 color='#0f172a'>" + str(summary['worked_days_count']) + "</font>", td_style),
            Paragraph("<b>Média Diária</b><br/><font size=12 color='#0f172a'>" + summary['average_daily_str'] + "</font>", td_style),
        ]
    ]
    metrics_table = Table(metrics_data, colWidths=[100, 100, 100, 100, 100, 100, 100])
    metrics_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 14))

    # Tabela de Detalhes
    headers = [
        Paragraph("Data", th_style),
        Paragraph("Tipo", th_style),
        Paragraph("Entrada", th_style),
        Paragraph("Saída Int.", th_style),
        Paragraph("Retorno", th_style),
        Paragraph("Saída Fin.", th_style),
        Paragraph("Trabalhado", th_style),
        Paragraph("Previsto", th_style),
        Paragraph("Extras", th_style),
        Paragraph("Faltantes", th_style),
        Paragraph("Saldo", th_style),
        Paragraph("Observações", th_style),
    ]
    table_rows = [headers]

    for d in summary['days']:
        periods = d.get('periods', [])
        first_in = periods[0]['start_time'] if len(periods) > 0 else '-'
        break_out = periods[0]['end_time'] if len(periods) > 1 else '-'
        break_in = periods[1]['start_time'] if len(periods) > 1 else '-'
        last_out = periods[-1]['end_time'] if len(periods) > 0 else '-'

        dt_parts = d['date'].split('-')
        data_fmt = f"{dt_parts[2]}/{dt_parts[1]}" if len(dt_parts) == 3 else d['date']
        
        tipo_fmt = {
            'trabalho': 'Normal',
            'folga': 'Folga',
            'feriado': 'Feriado',
            'falta': 'Falta (Pagar)',
            'atestado': 'Atestado',
            'compensacao': 'Comp.'
        }.get(d['day_type'], d['day_type'].capitalize())

        saldo_color = '#059669' if '+' in d['balance_str'] else ('#dc2626' if '-' in d['balance_str'] else '#0f172a')

        table_rows.append([
            Paragraph(data_fmt, td_style),
            Paragraph(tipo_fmt, td_style),
            Paragraph(first_in, td_style),
            Paragraph(break_out, td_style),
            Paragraph(break_in, td_style),
            Paragraph(last_out, td_style),
            Paragraph(f"<b>{d['total_worked_str']}</b>", td_style),
            Paragraph(d['expected_str'], td_style),
            Paragraph(f"<font color='#059669'>{d['overtime_str']}</font>" if d['overtime_minutes'] > 0 else "-", td_style),
            Paragraph(f"<font color='#dc2626'>{d['missing_str']}</font>" if d['missing_minutes'] > 0 else "-", td_style),
            Paragraph(f"<font color='{saldo_color}'><b>{d['balance_str']}</b></font>", td_style),
            Paragraph(d['observation'][:35] + ('...' if len(d['observation']) > 35 else ''), td_left),
        ])

    # Linha de Totais no rodapé
    table_rows.append([
        Paragraph("<b>TOTAL</b>", td_bold),
        Paragraph("", td_style),
        Paragraph("", td_style),
        Paragraph("", td_style),
        Paragraph("", td_style),
        Paragraph("", td_style),
        Paragraph(f"<b>{summary['total_worked_str']}</b>", td_bold),
        Paragraph(f"<b>{summary['total_expected_str']}</b>", td_bold),
        Paragraph(f"<b><font color='#059669'>{summary['total_overtime_str']}</font></b>", td_style),
        Paragraph(f"<b><font color='#dc2626'>{summary['total_missing_str']}</font></b>", td_style),
        Paragraph(f"<b><font color='{'#059669' if '+' in summary['total_balance_str'] else '#dc2626'}'>{summary['total_balance_str']}</font></b>", td_style),
        Paragraph("", td_style),
    ])

    col_widths = [50, 48, 42, 48, 48, 48, 55, 50, 45, 48, 50, 190]
    detail_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    
    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a8a')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f1f5f9')),
    ]
    # Alternating row colors
    for r in range(1, len(table_rows) - 1):
        if r % 2 == 0:
            t_style.append(('BACKGROUND', (0, r), (-1, r), colors.HexColor('#f8fafc')))

    detail_table.setStyle(TableStyle(t_style))
    story.append(detail_table)

    doc.build(story)
    output.seek(0)
    return output
