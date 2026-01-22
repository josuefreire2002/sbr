from django import template

register = template.Library()

@register.filter
def numero_a_letras(numero):
    """
    Convierte un número a texto en español (hasta millones).
    Ejemplo: 9000 -> NUEVE MIL
    """
    try:
        numero = int(float(numero))
    except (ValueError, TypeError):
        return str(numero)

    if numero == 0:
        return 'CERO'

    UNIDADES = {
        0: '', 1: 'UN', 2: 'DOS', 3: 'TRES', 4: 'CUATRO', 5: 'CINCO',
        6: 'SEIS', 7: 'SIETE', 8: 'OCHO', 9: 'NUEVE'
    }
    
    DECENAS = {
        10: 'DIEZ', 11: 'ONCE', 12: 'DOCE', 13: 'TRECE', 14: 'CATORCE', 15: 'QUINCE',
        20: 'VEINTE', 30: 'TREINTA', 40: 'CUARENTA', 50: 'CINCUENTA',
        60: 'SESENTA', 70: 'SETENTA', 80: 'OCHENTA', 90: 'NOVENTA'
    }

    CENTENAS = {
        100: 'CIEN', 200: 'DOSCIENTOS', 300: 'TRESCIENTOS', 400: 'CUATROCIENTOS',
        500: 'QUINIENTOS', 600: 'SEISCIENTOS', 700: 'SETECIENTOS', 
        800: 'OCHOCIENTOS', 900: 'NOVECIENTOS'
    }

    texto = ''

    # Millones
    millones = numero // 1000000
    if millones > 0:
        if millones == 1:
            texto += 'UN MILLÓN '
        else:
            texto += numero_a_letras(millones) + ' MILLONES '
        numero %= 1000000

    # Miles
    miles = numero // 1000
    if miles > 0:
        if miles == 1:
            texto += 'MIL '
        else:
            # Recursión simple para cientos de miles
            if miles > 1000:
                 texto += numero_a_letras(miles) + ' MIL '
            else:
                 # Lógica manual para miles simples para evitar recursión infinita o compleja
                 c = miles // 100
                 d = (miles % 100) // 10
                 u = miles % 10

                 if miles == 100:
                     texto += 'CIEN MIL '
                 else:
                     if c > 0:
                         if c == 1 and (d > 0 or u > 0):
                             texto += 'CIENTO '
                         else:
                             texto += CENTENAS.get(c * 100, '') + ' '
                     
                     if d == 1 and u < 6:
                         texto += DECENAS.get(10 + u, '') + ' MIL '
                     elif d > 0:
                         texto += DECENAS.get(d * 10, '')
                         if u > 0:
                             texto += ' Y ' + UNIDADES.get(u, '')
                         texto += ' MIL '
                     elif u > 0:
                         if u == 1 and c == 0 and d == 0:
                             # Caso "1000" ya manejado
                             pass
                         else:
                             texto += UNIDADES.get(u, '') + ' MIL '

        numero %= 1000

    # Centenas
    c = numero // 100
    if c > 0:
        if numero == 100:
            texto += 'CIEN '
        elif c == 1:
            texto += 'CIENTO '
        else:
            texto += CENTENAS.get(c * 100, '') + ' '
    
    numero %= 100

    # Decenas y Unidades
    if numero > 0:
        if numero < 16:
             if numero < 10:
                 texto += UNIDADES.get(numero, '')
             else:
                 texto += DECENAS.get(numero, '')
        else:
             d = numero // 10
             u = numero % 10
             texto += DECENAS.get(d * 10, '')
             if u > 0:
                 texto += ' Y ' + UNIDADES.get(u, '')

    return texto.strip()

@register.filter
def fecha_letras(fecha):
    """Convierte fecha a formato texto: '15 días del mes de Enero de 2026'"""
    if not fecha:
        return ''
    
    meses = {
        1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
        7: 'Julio', 8: 'Agosto', 9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
    }
    
    return f"{fecha.day} días del mes de {meses[fecha.month]} de {fecha.year}"

@register.filter
def nombre_mes(fecha):
    """Retorna solo el nombre del mes en español"""
    if not fecha: return ''
    meses_dict = {
        1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
        7: 'Julio', 8: 'Agosto', 9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
    }
    return meses_dict.get(fecha.month, '')

@register.filter
def dia_sin_cero(fecha):
    """Retorna el día como número entero (sin ceros a la izquierda)"""
    if not fecha: return ''
    return str(fecha.day)
