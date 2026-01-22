import os
from decimal import Decimal
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.conf import settings
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
# Esta es la clave para que funcione en Linux y Windows indistintamente:
from django.contrib.staticfiles import finders 

from xhtml2pdf import pisa
from .models import Contrato, Cuota, Pago, ConfiguracionSistema

# ==========================================
# UTILIDAD: CALLBACK UNIVERSAL (WINDOWS/LINUX)
# ==========================================
def link_callback(uri, rel):
    """
    Convierte URLs relativas en rutas absolutas del sistema de archivos.
    Funciona en Dev (Windows) y Prod (Linux) usando los finders de Django.
    """
    result = None
    
    # 1. Si es un archivo MEDIA (Logos subidos, fotos)
    if uri.startswith(settings.MEDIA_URL):
        path = os.path.join(
            settings.MEDIA_ROOT, 
            uri.replace(settings.MEDIA_URL, "")
        )
        # En Linux/Windows esto une las rutas correctamente con / o \ según corresponda
        if os.path.isfile(path):
            return path

    # 2. Si es un archivo STATIC (CSS, imagenes fijas)
    elif uri.startswith(settings.STATIC_URL):
        # Quitamos el prefijo '/static/' para buscar el archivo
        path_relativo = uri.replace(settings.STATIC_URL, "")
        
        # Le preguntamos a Django dónde está el archivo realmente
        result = finders.find(path_relativo)
        
        if result:
            if isinstance(result, (list, tuple)):
                result = result[0]
            return result
            
        # Fallback para Producción (cuando finders no busca en apps sino en STATIC_ROOT)
        if settings.STATIC_ROOT:
            path = os.path.join(settings.STATIC_ROOT, path_relativo)
            if os.path.isfile(path):
                return path

    # Si no lo encuentra, devuelve la URI original
    return uri

# ==========================================
# 1. GENERADOR DE TABLA DE AMORTIZACIÓN
# ==========================================
def generar_tabla_amortizacion(contrato_id, fecha_inicio_pago_str=None):
    contrato = Contrato.objects.get(id=contrato_id)
    contrato.cuotas.all().delete()
    
    saldo_actual = contrato.saldo_a_financiar
    plazo_meses = contrato.numero_cuotas
    
    if plazo_meses <= 0: return False
        
    cuota_base = round(saldo_actual / plazo_meses, 2)
    lista_cuotas_a_crear = []
    
    # Lógica de Fecha de Inicio
    # NOTA: Comentado temporalmente para pruebas de mora
    # TODO: Descomentar cuando las pruebas de mora estén completas
    if fecha_inicio_pago_str:
        try:
            fecha_base = datetime.strptime(fecha_inicio_pago_str, '%Y-%m-%d').date()
        except ValueError:
            # fecha_base = contrato.fecha_contrato + relativedelta(months=1)  # <-- Original
            fecha_base = contrato.fecha_contrato  # <-- Temporal para pruebas
    else:
        # fecha_base = contrato.fecha_contrato + relativedelta(months=1)  # <-- Original
        fecha_base = contrato.fecha_contrato  # <-- Temporal para pruebas

    for i in range(1, plazo_meses + 1):
        # La cuota 1 es la fecha elegida, la 2 es un mes después, etc.
        if i == 1:
            fecha_vencimiento = fecha_base
        else:
            fecha_vencimiento = fecha_base + relativedelta(months=i-1)
        
        # Ajuste de centavos final
        if i == plazo_meses:
            valor_capital_cuota = saldo_actual
        else:
            valor_capital_cuota = cuota_base

        saldo_actual -= valor_capital_cuota

        cuota = Cuota(
            contrato=contrato,
            numero_cuota=i,
            fecha_vencimiento=fecha_vencimiento,
            valor_capital=valor_capital_cuota,
            estado='PENDIENTE',
            valor_pagado=0,
            valor_mora=0
        )
        lista_cuotas_a_crear.append(cuota)

    Cuota.objects.bulk_create(lista_cuotas_a_crear)
    return True

# ==========================================
# 2. LOGICA DE MORAS (AUTOMATICA)
# ==========================================
# En services.py -> reemplazar la función actualizar_moras_contrato

def actualizar_moras_contrato(contrato_id):
    """
    Versión corregida: Marca VENCIDO inmediatamente si pasa la fecha,
    y aplica mora según los días de atraso configurados en Django Admin.
    """
    contrato = Contrato.objects.get(id=contrato_id)
    hoy = date.today()
    
    # Intentamos leer configuración, si no existe, usamos valores por defecto
    config = ConfiguracionSistema.objects.first()
    
    # Valores por defecto si el admin olvidó configurar
    dias_leve = config.mora_leve_dias if config else 1
    val_leve  = Decimal(str(config.mora_leve_valor)) if config else Decimal('5.00')
    dias_med  = config.mora_media_dias if config else 10
    val_med   = Decimal(str(config.mora_media_valor)) if config else Decimal('10.00')
    dias_grav = config.mora_grave_dias if config else 20
    val_grav  = Decimal(str(config.mora_grave_valor)) if config else Decimal('20.00')

    cuotas_pendientes = contrato.cuotas.filter(
        estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
    )

    for cuota in cuotas_pendientes:
        # Si la fecha de vencimiento es MENOR a hoy, YA VENCIÓ.
        if cuota.fecha_vencimiento < hoy:
            
            dias_retraso = (hoy - cuota.fecha_vencimiento).days
            mora_calcular = Decimal('0.00')

            # ⭐ NUEVO: Respetar exención manual de mora
            if not cuota.mora_exenta:
                # Calcular Mora según nivel de atraso (el más alto que aplique)
                if dias_retraso >= dias_grav:
                    mora_calcular = val_grav
                elif dias_retraso >= dias_med:
                    mora_calcular = val_med
                elif dias_retraso >= dias_leve:
                    mora_calcular = val_leve

            # Siempre actualizar estado y mora si está vencido
            cuota.estado = 'VENCIDO'
            cuota.valor_mora = mora_calcular
            cuota.save()

    # Actualizar bandera global del contrato
    tiene_mora = contrato.cuotas.filter(estado='VENCIDO').exists()
    if contrato.esta_en_mora != tiene_mora:
        contrato.esta_en_mora = tiene_mora
        contrato.save()

# ==========================================
# 3. PROCESADOR DE PAGOS
# ==========================================
@transaction.atomic
def registrar_pago_cliente(contrato_id, monto, metodo_pago, evidencia_img, usuario_vendedor):
    contrato = Contrato.objects.get(id=contrato_id)
    dinero_disponible = Decimal(monto)
    
    nuevo_pago = Pago.objects.create(
        contrato=contrato,
        fecha_pago=date.today(),
        monto=monto,
        metodo_pago=metodo_pago,
        comprobante_imagen=evidencia_img,
        registrado_por=usuario_vendedor
    )

    cuotas_pendientes = contrato.cuotas.filter(
        estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
    ).order_by('numero_cuota')

    for cuota in cuotas_pendientes:
        if dinero_disponible <= 0: break

        total_deuda_cuota = cuota.total_a_pagar
        falta_por_pagar = total_deuda_cuota - cuota.valor_pagado

        # Tolerance: treat amounts under $0.01 as zero (fixes decimal precision issues)
        if falta_por_pagar < Decimal('0.01'):
            cuota.estado = 'PAGADO'
            cuota.fecha_ultimo_pago = date.today()
            cuota.save()
            continue

        if dinero_disponible >= falta_por_pagar:
            cuota.valor_pagado += falta_por_pagar
            cuota.estado = 'PAGADO'
            cuota.fecha_ultimo_pago = date.today()
            dinero_disponible -= falta_por_pagar
        else:
            cuota.valor_pagado += dinero_disponible
            # Check if remaining balance after payment is negligible
            new_remaining = falta_por_pagar - dinero_disponible
            if new_remaining < Decimal('0.01'):
                cuota.estado = 'PAGADO'
            else:
                cuota.estado = 'PARCIAL'
            dinero_disponible = 0
        
        cuota.save()

    if dinero_disponible > 0:
        nuevo_pago.observacion = f"Pago procesado. Saldo a favor: ${dinero_disponible}"
        nuevo_pago.save()
    
    actualizar_moras_contrato(contrato.id)
    return nuevo_pago

# ==========================================
# 4. GENERADOR DE PDF
# ==========================================
def generar_pdf_contrato(contrato_id):
    contrato = Contrato.objects.get(id=contrato_id)
    config = ConfiguracionSistema.objects.first()
    
    # Obtener el pago de entrada (el primero registrado)
    pago_entrada = contrato.pago_set.order_by('id').first()
    
    metodo_real = 'EFECTIVO'
    datos_bancarios = None

    if pago_entrada:
        # Lógica para determinar el método real y detalles desde la observación
        obs = pago_entrada.observacion or ""
        
        if 'TRANSFERENCIA' in obs:
            metodo_real = 'TRANSFERENCIA BANCARIA'
            # Intentar extraer banco y cuenta
            # Formato esperado: "Pago de Entrada (TRANSFERENCIA). Banco: X. Cuenta/Comp: Y."
            try:
                # Buscamos los delimitadores exactos que usamos en views.py
                if "Banco:" in obs and "Cuenta/Comp:" in obs:
                    # Todo lo que está después de 'Banco:'
                    resto_banco = obs.split("Banco:")[1]
                    
                    # Separamos por el delimitador que sigue al banco: ". Cuenta/Comp:"
                    # Usamos partition para seguridad
                    if ". Cuenta/Comp:" in resto_banco:
                        parte_banco, _, parte_cuenta = resto_banco.partition(". Cuenta/Comp:")
                        
                        datos_bancarios = {
                            'banco': parte_banco.strip(),
                            'cuenta': parte_cuenta.rstrip(".").strip() # Quitamos el punto final
                        }
                    else:
                        # Fallback por si acaso el formato varió ligeramente (ej. falta espacio)
                        # Intento split simple por 'Cuenta/Comp:'
                        parte_banco = resto_banco.split("Cuenta/Comp:")[0].strip().rstrip(".")
                        parte_cuenta = resto_banco.split("Cuenta/Comp:")[1].strip().rstrip(".")
                        datos_bancarios = {
                            'banco': parte_banco,
                            'cuenta': parte_cuenta
                        }
            except Exception as e:
                # En caso de error, dejamos datos_bancarios en None para que salga el default
                print(f"Error parsing bank details: {e}")
                pass
                
        elif 'DEPOSITO' in obs:
            metodo_real = 'DEPÓSITO'
        elif pago_entrada.metodo_pago == 'EFECTIVO':
            metodo_real = 'EFECTIVO'

    context = {
        'contrato': contrato,
        'cliente': contrato.cliente,
        'lote': contrato.lote,
        'empresa': config,
        'cuotas': contrato.cuotas.all(),
        'metodo_real_pago': metodo_real,
        'datos_bancarios': datos_bancarios,
        'base_url': settings.BASE_URL if hasattr(settings, 'BASE_URL') else 'http://127.0.0.1:8000',
        'fecha_actual': date.today(),
    }
    
    html_string = render_to_string('reportes/plantilla_contrato.html', context)
    
    from io import BytesIO
    result_file = BytesIO()
    
    # El link_callback actualizado usa 'finders' y funciona en Linux/Windows
    pisa_status = pisa.CreatePDF(
        html_string,
        dest=result_file,
        link_callback=link_callback 
    )

    if pisa_status.err:
        return None

    filename = f"Contrato_{contrato.id}_{contrato.cliente.apellidos}.pdf"
    contrato.archivo_contrato_pdf.save(filename, ContentFile(result_file.getvalue()))
    
    return contrato.archivo_contrato_pdf.url