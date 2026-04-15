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
from .models import Contrato, Cuota, Pago, ConfiguracionSistema, DetallePago

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
    if fecha_inicio_pago_str:
        try:
            fecha_base = datetime.strptime(fecha_inicio_pago_str, '%Y-%m-%d').date()
        except ValueError:
             fecha_base = contrato.fecha_contrato + relativedelta(months=1)
    else:
        fecha_base = contrato.fecha_contrato + relativedelta(months=1)

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

def actualizar_moras_masivo(contratos_qs):
    """
    Versión de ultra-alto rendimiento para actualizar la mora de múltiples contratos a la vez.
    Ideal para usar en vistas de listados de clientes donde iterar uno a uno sobrecarga la BD.
    """
    hoy = date.today()
    config = ConfiguracionSistema.objects.first()
    porcentaje_mora = config.mora_porcentaje if config else Decimal('3.00')

    # Filtrar cuotas candidatas a cambiar
    cuotas = Cuota.objects.filter(
        contrato__in=contratos_qs,
        estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
    )
    
    cuotas_a_actualizar = []
    
    for cuota in cuotas:
        # Si la fecha de vencimiento es MENOR a hoy, YA VENCIÓ.
        if cuota.fecha_vencimiento < hoy:
            mora_calcular = Decimal('0.00')

            # Respetar exención manual de mora
            if cuota.mora_exenta:
                nuevo_estado = 'PENDIENTE' if cuota.saldo_pendiente > 0 else 'PAGADO'
                if cuota.estado != nuevo_estado or cuota.valor_mora != mora_calcular:
                    cuota.estado = nuevo_estado
                    cuota.valor_mora = mora_calcular
                    cuotas_a_actualizar.append(cuota)
                continue
            
            # Calcular Mora Única (Porcentual)
            mora_calcular = (cuota.valor_capital * porcentaje_mora) / Decimal('100.00')
            mora_calcular = mora_calcular.quantize(Decimal('0.01'), rounding='ROUND_HALF_UP')
            
            # Asegurar mínimo de $0.01 si el porcentaje dio 0 por ser cuota muy pequeña
            if mora_calcular < Decimal('0.01') and porcentaje_mora > 0:
                mora_calcular = Decimal('0.01')

            # Actualizar estado y mora
            if cuota.estado != 'VENCIDO' or cuota.valor_mora != mora_calcular:
                cuota.estado = 'VENCIDO'
                cuota.valor_mora = mora_calcular
                cuotas_a_actualizar.append(cuota)
                
    if cuotas_a_actualizar:
        Cuota.objects.bulk_update(cuotas_a_actualizar, ['estado', 'valor_mora'])

    # Actualizar bandera global de los contratos
    contratos_con_mora = set(Cuota.objects.filter(
        contrato__in=contratos_qs, estado='VENCIDO'
    ).values_list('contrato_id', flat=True))

    contratos_qs.filter(id__in=contratos_con_mora).exclude(esta_en_mora=True).update(esta_en_mora=True)
    contratos_qs.exclude(id__in=contratos_con_mora).filter(esta_en_mora=True).update(esta_en_mora=False)

def actualizar_moras_contrato(contrato_id):
    """
    Versión corregida: Marca VENCIDO inmediatamente si pasa la fecha,
    y aplica mora según los días de atraso configurados en Django Admin.
    Usa Cuota.objects.filter() para evitar caché del ORM.
    """
    contrato = Contrato.objects.get(id=contrato_id)
    hoy = date.today()
    
    # Intentamos leer configuración, si no existe, usamos valores por defecto
    config = ConfiguracionSistema.objects.first()
    
    # Valores por defecto si el admin olvidó configurar
    porcentaje_mora = config.mora_porcentaje if config else Decimal('3.00')

    # IMPORTANTE: Usar Cuota.objects.filter para evitar caché del ORM
    cuotas_no_pagadas = Cuota.objects.filter(
        contrato_id=contrato_id,
        estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
    )

    for cuota in cuotas_no_pagadas:
        # Si la fecha de vencimiento es MENOR a hoy, YA VENCIÓ.
        if cuota.fecha_vencimiento < hoy:
            
            mora_calcular = Decimal('0.00')

            # Respetar exención manual de mora
            if cuota.mora_exenta:
                # Si está exenta, NO se cobra mora
                cuota.estado = 'PENDIENTE' if cuota.saldo_pendiente > 0 else 'PAGADO'
                cuota.valor_mora = Decimal('0.00')
                cuota.save()
                continue
            
            # Calcular Mora Única (Porcentual)
            mora_calcular = (cuota.valor_capital * porcentaje_mora) / Decimal('100.00')
            mora_calcular = mora_calcular.quantize(Decimal('0.01'), rounding='ROUND_HALF_UP')
            
            # Asegurar mínimo de $0.01 si el porcentaje dio 0 por ser cuota muy pequeña
            if mora_calcular < Decimal('0.01') and porcentaje_mora > 0:
                mora_calcular = Decimal('0.01')

            # Actualizar estado y mora - VENCIDO tiene prioridad sobre PARCIAL
            cuota.estado = 'VENCIDO'
            cuota.valor_mora = mora_calcular
            cuota.save()

    # Actualizar bandera global del contrato
    tiene_mora = Cuota.objects.filter(contrato_id=contrato_id, estado='VENCIDO').exists()
    if contrato.esta_en_mora != tiene_mora:
        contrato.esta_en_mora = tiene_mora
        contrato.save()

# ==========================================
# 3. PROCESADOR DE PAGOS
# ==========================================
@transaction.atomic
def registrar_pago_cliente(contrato_id, monto, metodo_pago, evidencia_img, usuario_vendedor, fecha_pago=None, cuota_origen_id=None):
    contrato = Contrato.objects.get(id=contrato_id)
    dinero_disponible = Decimal(monto)
    
    # 1. Validar y procesar FECHA
    if not fecha_pago:
        fecha_real = date.today()
    else:
        # Puede venir como string 'YYYY-MM-DD' o ya como objeto date
        if isinstance(fecha_pago, str):
            try:
                fecha_real = datetime.strptime(fecha_pago, '%Y-%m-%d').date()
            except ValueError:
                fecha_real = date.today()
        else:
            fecha_real = fecha_pago

    cuota_origen_obj = None
    start_numero_cuota = 0
    if cuota_origen_id:
        try:
            cuota_origen_obj = contrato.cuotas.get(id=cuota_origen_id)
            start_numero_cuota = cuota_origen_obj.numero_cuota
        except Cuota.DoesNotExist:
            pass # Fallback a comportamiento normal
            
    # Calcular nuevo número de transacción
    from django.db.models import Max
    last_pago = Pago.objects.filter(contrato=contrato).aggregate(Max('numero_transaccion'))
    new_num = (last_pago['numero_transaccion__max'] or 0) + 1

    nuevo_pago = Pago.objects.create(
        contrato=contrato,
        fecha_pago=fecha_real,
        numero_transaccion=new_num,
        monto=monto,
        metodo_pago=metodo_pago,
        comprobante_imagen=evidencia_img,
        registrado_por=usuario_vendedor,
        cuota_origen=cuota_origen_obj
    )

    # 2. Definir lista de cuotas a afectar
    # Lógica: Si elige una cuota específica, comenzamos desde esa en adelante.
            
    # Obtenemos las pendientes desde el punto de partida (o todas si no hay punto partida)
    # Nota: Permitimos pagar 'VENCIDO', 'PENDIENTE', 'PARCIAL'.
    # Si el usuario selecciona la cuota #5, y debe la #3, el sistema pagará la #5 y siguientes,
    # IGNORANDO la #3. Esto es lo que el usuario pidió ("seleccionar qué cuota estoy pagando").
    # Si no selecciona nada, el comportamiento por defecto es pagar las más antiguas primero.
    
    qs = contrato.cuotas.filter(
        estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
    )
    
    if start_numero_cuota > 0:
        qs = qs.filter(numero_cuota__gte=start_numero_cuota)
        
    cuotas_pendientes = qs.order_by('numero_cuota')

    # ... (inside registrar_pago_cliente)
    
    # IMPORTACION CORRECTA DENTRO DE LA FUNCIÓN PARA EVITAR CIRCULAR IMPORT
    from .models import DetallePago
    
    # Procesar cuotas pendientes de la lista inicial
    for cuota in cuotas_pendientes:
        if dinero_disponible <= 0: break

        total_deuda_cuota = cuota.total_a_pagar
        falta_por_pagar = total_deuda_cuota - cuota.valor_pagado

        # Tolerance: treat amounts under $0.01 as zero
        if falta_por_pagar < Decimal('0.01'):
            cuota.estado = 'PAGADO'
            cuota.fecha_ultimo_pago = fecha_real
            cuota.save()
            continue

        monto_aplicado_a_esta_cuota = Decimal('0.00')

        if dinero_disponible >= falta_por_pagar:
            # Cubre toda la cuota
            monto_aplicado_a_esta_cuota = falta_por_pagar
            cuota.valor_pagado += falta_por_pagar
            cuota.estado = 'PAGADO'
            cuota.fecha_ultimo_pago = fecha_real
            dinero_disponible -= falta_por_pagar
        else:
            # Pago parcial
            monto_aplicado_a_esta_cuota = dinero_disponible
            cuota.valor_pagado += dinero_disponible
            
            # Recálculo estado
            new_remaining = falta_por_pagar - monto_aplicado_a_esta_cuota
            if new_remaining < Decimal('0.01'):
                cuota.estado = 'PAGADO'
            else:
                cuota.estado = 'PARCIAL'
            
            cuota.fecha_ultimo_pago = fecha_real 
            dinero_disponible = Decimal('0') 
        
        cuota.save()

        # Registrar detalle del pago
        if monto_aplicado_a_esta_cuota > 0:
            DetallePago.objects.create(
                pago=nuevo_pago,
                cuota=cuota,
                monto_aplicado=monto_aplicado_a_esta_cuota
            )

    # Lógica de Excedente (Surplus) para cuotas futuras
    # Si sobra dinero y NO se seleccionó una cuota específica de inicio (para evitar saltos raros),
    # aplicamos el sobrante a las siguientes cuotas PENDIENTES que no estaban en la lista original (ej. futuras)
    if dinero_disponible > 0:
        # Buscar futuras cuotas que no estaban en el query inicial o que se generaron después
        ultima_cuota_procesada = cuotas_pendientes.last()
        numero_inicio = (ultima_cuota_procesada.numero_cuota + 1) if ultima_cuota_procesada else 1
        
        # Obtenemos las siguientes cuotas cronológicamente
        otras_cuotas = contrato.cuotas.filter(
            numero_cuota__gte=numero_inicio
        ).order_by('numero_cuota')

        for cuota_futura in otras_cuotas:
             if dinero_disponible <= 0: break
             
             total_deuda = cuota_futura.total_a_pagar
             falta = total_deuda - cuota_futura.valor_pagado
             
             # Si la cuota ya está pagada (poco probable pero posible), saltar
             if falta < Decimal('0.01'): continue

             monto_aplicado = Decimal('0.00')

             if dinero_disponible >= falta:
                 monto_aplicado = falta
                 cuota_futura.valor_pagado += falta
                 cuota_futura.estado = 'PAGADO'
                 cuota_futura.fecha_ultimo_pago = fecha_real
                 dinero_disponible -= falta
             else:
                 monto_aplicado = dinero_disponible
                 cuota_futura.valor_pagado += dinero_disponible
                 # Si cubrió una parte, es PARCIAL
                 # Si cubrió todo (caso raro float), es PAGADO
                 remaining = falta - monto_aplicado
                 if remaining < Decimal('0.01'):
                     cuota_futura.estado = 'PAGADO'
                 else:
                     cuota_futura.estado = 'PARCIAL'
                     
                 cuota_futura.fecha_ultimo_pago = fecha_real
                 dinero_disponible = Decimal('0')
             
             cuota_futura.save()
             
             if monto_aplicado > 0:
                DetallePago.objects.create(
                    pago=nuevo_pago,
                    cuota=cuota_futura,
                    monto_aplicado=monto_aplicado
                )

    # Si AÚN sobra dinero (ya no hay cuotas generadas o se pagó TODO el contrato), queda a favor.
    if dinero_disponible > 0:
        texto_saldo = f" | Saldo a favor remanente: ${dinero_disponible:.2f}"
        if nuevo_pago.observacion:
            nuevo_pago.observacion += texto_saldo
        else:
            nuevo_pago.observacion = texto_saldo.strip(" | ")
        nuevo_pago.save()
    
    actualizar_moras_contrato(contrato.id)
    return nuevo_pago

@transaction.atomic
def recalcular_deuda_contrato(contrato_id):
    """
    Restaura valor_pagado en 0 y vuelve a aplicar TODOS los pagos existentes 
    en orden cronológico. Crucial para cuando se edita o elimina un pago intermedio.
    NOTA: NO modifica mora_exenta (eso es control manual del admin).
    """
    contrato = Contrato.objects.get(id=contrato_id)
    
    # 1. Resetear valor_pagado y mora de TODAS las cuotas
    contrato.cuotas.all().update(valor_pagado=0, fecha_ultimo_pago=None, valor_mora=0, estado='PENDIENTE')
        
    # Destruir registros de detalles de pago (ya que se regenerarán)
    from .models import DetallePago
    DetallePago.objects.filter(pago__contrato_id=contrato_id).delete()

    # 2. Obtener todos los pagos en orden cronológico, EXCLUYENDO LA ENTRADA
    pagos = contrato.pago_set.filter(es_entrada=False).order_by('fecha_pago', 'id')
    
    # 3. Re-aplicar lógica de pago para cada uno (FIFO o basado en origen)
    for pago in pagos:
        dinero_disponible = pago.monto
        fecha_pago = pago.fecha_pago
        
        # Limpiar saldo a favor viejo si existe, ya que lo recalcularemos
        if pago.observacion and " | Saldo a favor remanente:" in pago.observacion:
            pago.observacion = pago.observacion.split(" | Saldo a favor remanente:")[0]
            pago.save(update_fields=['observacion'])
        
        # Punto de inicio para distribuir el pago
        start_num = pago.cuota_origen.numero_cuota if pago.cuota_origen else 1
        cuotas_afectadas = contrato.cuotas.filter(numero_cuota__gte=start_num).order_by('numero_cuota')
        
        # --- VIAJE EN EL TIEMPO: Aplicar moras vigentes HASTA la fecha de este pago ---
        config = ConfiguracionSistema.objects.first()
        porcentaje_mora = config.mora_porcentaje if config else Decimal('3.00')
        
        for cuota in cuotas_afectadas:
             # Si en la fecha que se hizo este pago, esta cuota ya estaba vencida:
             if cuota.fecha_vencimiento < fecha_pago:
                  if not cuota.mora_exenta:
                       # Calcular Mora Única (Porcentual)
                       mora_calcular = (cuota.valor_capital * porcentaje_mora) / Decimal('100.00')
                       mora_calcular = mora_calcular.quantize(Decimal('0.01'), rounding='ROUND_HALF_UP')
                       if mora_calcular < Decimal('0.01') and porcentaje_mora > 0:
                            mora_calcular = Decimal('0.01')
                       
                       # Solo aplicar si la cuota no estaba ya pagada en su totalidad en ese viaje en el tiempo
                       saldo = cuota.valor_capital - cuota.valor_pagado
                       if saldo > Decimal('0.01'):
                            cuota.valor_mora = mora_calcular
                            cuota.estado = 'VENCIDO'
                            cuota.save(update_fields=['valor_mora', 'estado'])
        # -----------------------------------------------------------------------------
        
        # Refrescar cuotas desde la BD para tener datos actualizados
        for cuota in cuotas_afectadas:
            if dinero_disponible <= 0: 
                break

            # Refrescar el objeto desde la BD
            cuota.refresh_from_db()
            
            total_deuda_cuota = cuota.total_a_pagar
            falta_por_pagar = total_deuda_cuota - cuota.valor_pagado

            if falta_por_pagar < Decimal('0.01'):
                continue  # Ya está pagada

            monto_aplicado = Decimal('0.00')

            if dinero_disponible >= falta_por_pagar:
                monto_aplicado = falta_por_pagar
                cuota.valor_pagado += falta_por_pagar
                cuota.fecha_ultimo_pago = fecha_pago
                dinero_disponible -= falta_por_pagar
            else:
                monto_aplicado = dinero_disponible
                cuota.valor_pagado += dinero_disponible
                cuota.fecha_ultimo_pago = fecha_pago
                dinero_disponible = 0
            
            cuota.save(update_fields=['valor_pagado', 'fecha_ultimo_pago'])
            
            # Registrar detalle si aplicamos dinero
            if monto_aplicado > 0:
                DetallePago.objects.create(
                    pago=pago,
                    cuota=cuota,
                    monto_aplicado=monto_aplicado
                )
                
        # Si aún sobra dinero (ya no hay cuotas o pagó todo), documentar a favor
        if dinero_disponible > 0:
            texto_saldo = f" | Saldo a favor remanente: ${dinero_disponible:.2f}"
            if pago.observacion:
                pago.observacion += texto_saldo
            else:
                pago.observacion = texto_saldo.strip(" | ")
            pago.save(update_fields=['observacion'])
    
    # 4. Recalcular estados de TODAS las cuotas basándose en pagos y fechas
    hoy = date.today()
    for cuota in contrato.cuotas.all():
        cuota.refresh_from_db()  # Asegurar datos frescos
        saldo = cuota.saldo_pendiente
        
        if saldo < Decimal('0.01'):
            cuota.estado = 'PAGADO'
        elif cuota.fecha_vencimiento < hoy and not cuota.mora_exenta:
            # VENCIDO tiene prioridad sobre PARCIAL cuando está vencido
            cuota.estado = 'VENCIDO'
        elif cuota.valor_pagado > 0:
            cuota.estado = 'PARCIAL'
        elif cuota.fecha_vencimiento < hoy:
            cuota.estado = 'VENCIDO'
        else:
            cuota.estado = 'PENDIENTE'
        
        cuota.save(update_fields=['estado'])
            
    # 5. Actualizar moras (respetando mora_exenta)
    actualizar_moras_contrato(contrato.id)

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
    
    from weasyprint import HTML
    
    html_string = render_to_string('reportes/plantilla_contrato.html', context)
    
    from io import BytesIO
    result_file = BytesIO()
    
    base_url = settings.BASE_URL if hasattr(settings, 'BASE_URL') else 'http://127.0.0.1:8000'
    HTML(string=html_string, base_url=base_url).write_pdf(result_file)

    filename = f"Contrato_{contrato.id}_{contrato.cliente.apellidos}.pdf"
    contrato.archivo_contrato_pdf.save(filename, ContentFile(result_file.getvalue()))
    
    return contrato.archivo_contrato_pdf.url


# ==========================================
# 5. GENERADOR DE RECIBO DE ENTRADA
# ==========================================
def _parse_bank_details(observacion):
    """
    Helper para extraer datos bancarios de la observación del pago.
    """
    datos_bancarios = None
    if "Banco:" in observacion and "Cuenta/Comp:" in observacion:
        try:
            resto_banco = observacion.split("Banco:")[1]
            if ". Cuenta/Comp:" in resto_banco:
                parte_banco, _, parte_cuenta = resto_banco.partition(". Cuenta/Comp:")
                datos_bancarios = {
                    'banco': parte_banco.strip(),
                    'cuenta': parte_cuenta.rstrip(".").strip()
                }
            else:
                parte_banco = resto_banco.split("Cuenta/Comp:")[0].strip().rstrip(".")
                parte_cuenta = resto_banco.split("Cuenta/Comp:")[1].strip().rstrip(".")
                datos_bancarios = {
                    'banco': parte_banco,
                    'cuenta': parte_cuenta
                }
        except Exception:
            pass
    return datos_bancarios

def generar_recibo_entrada_buffer(contrato_id):
    """
    Genera el PDF del recibo de entrada y retorna el buffer (BytesIO).
    """
    contrato = Contrato.objects.get(id=contrato_id)
    config = ConfiguracionSistema.objects.first()
    
    pago_entrada = contrato.pago_set.order_by('id').first()
    
    metodo_real = 'EFECTIVO'
    datos_bancarios = None

    if pago_entrada:
        obs = pago_entrada.observacion or ""
        if 'TRANSFERENCIA' in obs:
            metodo_real = 'TRANSFERENCIA BANCARIA'
            datos_bancarios = _parse_bank_details(obs)
        elif 'DEPOSITO' in obs:
            metodo_real = 'DEPÓSITO'
        elif pago_entrada.metodo_pago == 'EFECTIVO':
            metodo_real = 'EFECTIVO'

    context = {
        'contrato': contrato,
        'cliente': contrato.cliente,
        'empresa': config,
        'metodo_real_pago': metodo_real,
        'datos_bancarios': datos_bancarios,
        'saldo_pendiente': contrato.saldo_a_financiar,
        'fecha_actual': datetime.now(),
        'base_url': settings.BASE_URL if hasattr(settings, 'BASE_URL') else 'http://127.0.0.1:8000',
    }
    
    from weasyprint import HTML
    
    html_string = render_to_string('reportes/recibo_entrada.html', context)
    
    from io import BytesIO
    result_file = BytesIO()
    
    base_url = settings.BASE_URL if hasattr(settings, 'BASE_URL') else 'http://127.0.0.1:8000'
    HTML(string=html_string, base_url=base_url).write_pdf(result_file)
        
    result_file.seek(0)
    return result_file

# ==========================================
# 6. GENERADOR DE RECIBO DE PAGO MENSUAL
# ==========================================
def generar_recibo_pago_buffer(cuota_id):
    """
    Genera el PDF del recibo de pago mensual para una cuota y retorna el buffer (BytesIO).
    """
    cuota = Cuota.objects.get(id=cuota_id)
    contrato = cuota.contrato
    config = ConfiguracionSistema.objects.first()
    
    # Verificar que la cuota tenga pagos
    if cuota.valor_pagado <= 0:
        return None
    
    # Usamos la fecha de vencimiento de la cuota (como aparece en la tabla de amortización)
    fecha_pago = cuota.fecha_vencimiento
    monto_pagado = cuota.valor_pagado
    
    # Saldo pendiente global del contrato
    saldo_pendiente = sum(c.total_a_pagar - c.valor_pagado for c in contrato.cuotas.all())
    
    # Determinar método de pago buscando en pagos recientes
    # Buscamos un pago que coincida con la fecha (aproximación razonable)
    metodo_real = 'EFECTIVO'
    datos_bancarios = None
    
    # Buscar el pago más reciente que cubra esta cuota
    pago_asociado = contrato.pago_set.filter(fecha_pago=fecha_pago).order_by('-id').first()
    
    if pago_asociado:
        obs = pago_asociado.observacion or ""
        if 'TRANSFERENCIA' in obs:
            metodo_real = 'TRANSFERENCIA BANCARIA'
            datos_bancarios = _parse_bank_details(obs)
        elif 'DEPOSITO' in obs:
            metodo_real = 'DEPÓSITO'
        elif pago_asociado.metodo_pago == 'EFECTIVO':
            metodo_real = 'EFECTIVO'

    context = {
        'contrato': contrato,
        'cliente': contrato.cliente,
        'cuota': cuota,
        'empresa': config,
        'fecha_pago': fecha_pago,
        'monto_pagado': monto_pagado,
        'metodo_real_pago': metodo_real,
        'datos_bancarios': datos_bancarios,
        'saldo_pendiente': saldo_pendiente,
        'fecha_actual': datetime.now(),
        'base_url': settings.BASE_URL if hasattr(settings, 'BASE_URL') else 'http://127.0.0.1:8000',
    }
    
    from weasyprint import HTML
    
    html_string = render_to_string('reportes/recibo_pago_mensual.html', context)
    
    from io import BytesIO
    result_file = BytesIO()
    
    # Usamos WeasyPrint para soportar CSS moderno (Flexbox, Grid)
    # base_url apunta a la raiz para cargar imagenes estaticas
    base_url = settings.BASE_URL if hasattr(settings, 'BASE_URL') else 'http://127.0.0.1:8000'
    HTML(string=html_string, base_url=base_url).write_pdf(result_file)
        
    result_file.seek(0)
    return result_file

def generar_recibo_transaccion_buffer(pago_id):
    """
    Genera el PDF del recibo para una transacción específica (Pago).
    """
    pago = Pago.objects.get(id=pago_id)
    contrato = pago.contrato
    config = ConfiguracionSistema.objects.first()
    
    # Datos directos del pago
    fecha_pago = pago.fecha_pago
    monto_pagado = pago.monto
    
    # Saldo pendiente global del contrato (al momento actual)
    saldo_pendiente = sum(c.total_a_pagar - c.valor_pagado for c in contrato.cuotas.all())
    
    # Determinar método de pago y detalles
    metodo_real = 'EFECTIVO'
    datos_bancarios = None
    
    obs = pago.observacion or ""
    if 'TRANSFERENCIA' in obs or pago.metodo_pago == 'TRANSFERENCIA':
        metodo_real = 'TRANSFERENCIA BANCARIA'
        datos_bancarios = _parse_bank_details(obs)
    elif 'DEPOSITO' in obs:
        metodo_real = 'DEPÓSITO'
    elif pago.metodo_pago == 'EFECTIVO':
        metodo_real = 'EFECTIVO'

    # Calcular qué cuotas cubrió este pago para mostrarlas (Opcional)
    cuotas_cubiertas = []
    # Usamos los detalles
    detalles = pago.detalles.all().order_by('cuota__numero_cuota')
    for d in detalles:
        cuotas_cubiertas.append(str(d.cuota.numero_cuota))
    
    cuotas_str = ", ".join(cuotas_cubiertas) if cuotas_cubiertas else "Abono General"

    context = {
        'contrato': contrato,
        'cliente': contrato.cliente,
        'pago': pago,
        'empresa': config,
        'fecha_pago': fecha_pago,
        'monto_pagado': monto_pagado,
        'metodo_real_pago': metodo_real,
        'datos_bancarios': datos_bancarios,
        'saldo_pendiente': saldo_pendiente,
        'cuotas_str': cuotas_str,
        'fecha_actual': datetime.now(),
        'base_url': settings.BASE_URL if hasattr(settings, 'BASE_URL') else 'http://127.0.0.1:8000',
    }
    
    from weasyprint import HTML
    
    # Usaremos una nueva plantilla o la misma adaptada
    html_string = render_to_string('reportes/recibo_transaccion.html', context)
    
    from io import BytesIO
    result_file = BytesIO()
    
    base_url = settings.BASE_URL if hasattr(settings, 'BASE_URL') else 'http://127.0.0.1:8000'
    HTML(string=html_string, base_url=base_url).write_pdf(result_file)
        
    result_file.seek(0)
    return result_file