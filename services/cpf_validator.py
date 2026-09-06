import re

def clean_cpf(cpf: str) -> str:
    """Remove pontuação e caracteres não numéricos do CPF."""
    if not cpf:
        return ""
    return re.sub(r'\D', '', str(cpf))

def format_cpf(cpf: str) -> str:
    """Formata CPF no padrão 000.000.000-00 se tiver 11 dígitos."""
    cleaned = clean_cpf(cpf)
    if len(cleaned) == 11:
        return f"{cleaned[:3]}.{cleaned[3:6]}.{cleaned[6:9]}-{cleaned[9:]}"
    return cpf or ""

def validate_cpf(cpf: str) -> bool:
    """
    Valida se um CPF é matematicamente autêntico segundo o algoritmo oficial
    da Receita Federal do Brasil (módulo 11 para os dois dígitos verificadores).
    """
    cleaned = clean_cpf(cpf)
    
    if len(cleaned) != 11:
        return False
    
    # Rejeita CPFs com todos os dígitos iguais (ex: 111.111.111-11)
    if len(set(cleaned)) == 1:
        return False
    
    digits = [int(c) for c in cleaned]
    
    # 1º Dígito Verificador (pesos de 10 a 2)
    soma_1 = sum(digits[i] * (10 - i) for i in range(9))
    resto_1 = (soma_1 * 10) % 11
    d1 = 0 if resto_1 in (10, 11) else resto_1
    
    if d1 != digits[9]:
        return False
    
    # 2º Dígito Verificador (pesos de 11 a 2)
    soma_2 = sum(digits[i] * (11 - i) for i in range(10))
    resto_2 = (soma_2 * 10) % 11
    d2 = 0 if resto_2 in (10, 11) else resto_2
    
    if d2 != digits[10]:
        return False
        
    return True
