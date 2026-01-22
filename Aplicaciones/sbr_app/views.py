from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Q
from django.http import FileResponse, HttpResponse
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.template.loader import render_to_string
from .services import actualizar_moras_contrato 
import base64
import os
from django.contrib.staticfiles import finders
# Importamos Modelos
from .models import Cliente, Lote, Contrato, Pago, Cuota, ConfiguracionSistema

# Importamos Servicios (La lógica pesada)
from .services import (
    generar_tabla_amortizacion, 
    registrar_pago_cliente, 
    generar_pdf_contrato
)

# ==========================================
# 1. DASHBOARD (Pantalla Principal)
# ==========================================
@login_required
def dashboard_view(request):
    # Lógica: Mostrar resumen básico
    if request.user.is_superuser:
        contratos = Contrato.objects.all()
    else:
        contratos = Contrato.objects.filter(cliente__vendedor=request.user)

    total_ventas = contratos.count()
    # Sumar pagos realizados hoy
    pagos_hoy = Pago.objects.filter(
        fecha_pago=date.today(),
        contrato__in=contratos
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    context = {
        'total_ventas': total_ventas,
        'pagos_hoy': pagos_hoy,
        'contratos_recientes': contratos.order_by('-id')[:5]
    }
    return render(request, 'dashboard.html', context)

# ==========================================
# 2. NUEVA VENTA (Wizard)
# ==========================================
@login_required
def crear_venta_view(request):
    if request.method == 'POST':
        try:
            with transaction.atomic(): # Transacción segura
                
                # 1. CLIENTE (Datos completos del Excel)
                cliente = Cliente.objects.create(
                    vendedor=request.user,
                    cedula=request.POST.get('cedula'),
                    nombres=request.POST.get('nombres'),
                    apellidos=request.POST.get('apellidos'),
                    celular=request.POST.get('celular'),
                    email=request.POST.get('email'), # Opcional
                    direccion=request.POST.get('direccion')
                )

                # 2. LOTE
                lote_id = request.POST.get('lote_id')
                lote = Lote.objects.get(id=lote_id)
                
                # 3. DATOS ECONÓMICOS Y CONTRATO
                # Nota: Recibimos fecha manual del formulario
                fecha_contrato_str = request.POST.get('fecha_contrato') 
                
                # 3.1. CAPTURAR MÉTODO DE PAGO ENTRADA
                metodo_entrada = request.POST.get('metodo_pago_entrada')
                banco_entrada = request.POST.get('banco_entrada')
                cuenta_entrada = request.POST.get('cuenta_entrada')
                
                # Mapeamos DEPOSITO a TRANSFERENCIA para el modelo, pero guardamos el detalle
                metodo_modelo = 'TRANSFERENCIA' if metodo_entrada in ['TRANSFERENCIA', 'DEPOSITO'] else 'EFECTIVO'
                
                # Construimos la observación con los detalles bancarios
                observacion_pago = f"Pago de Entrada ({metodo_entrada})."
                if metodo_entrada == 'TRANSFERENCIA':
                    observacion_pago += f" Banco: {banco_entrada}. Cuenta/Comp: {cuenta_entrada}."

                contrato = Contrato.objects.create(
                    cliente=cliente,
                    lote=lote,
                    fecha_contrato=fecha_contrato_str, # Usamos la fecha que eligió el usuario
                    precio_venta_final=Decimal(request.POST.get('precio_final')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                    valor_entrada=Decimal(request.POST.get('entrada')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                    saldo_a_financiar=Decimal(request.POST.get('saldo')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                    numero_cuotas=int(request.POST.get('plazo'))
                )
                
                # Guardamos la observación del contrato si existe
                observacion_contrato = request.POST.get('observacion')
                # contrato.observacion = observacion_contrato # Descomentar si se agrega campo al modelo

                # 3.2. REGISTRAR PAGO DE ENTRADA
                if contrato.valor_entrada > 0:
                    Pago.objects.create(
                        contrato=contrato,
                        fecha_pago=fecha_contrato_str,
                        monto=contrato.valor_entrada,
                        metodo_pago=metodo_modelo,
                        comprobante_imagen=request.FILES.get('comprobante'),
                        observacion=observacion_pago,
                        registrado_por=request.user
                    )

                lote.estado = 'VENDIDO'
                lote.save()

                fecha_pago_input = request.POST.get('fecha_primer_pago')

                # 4. GENERAR LÓGICA
                generar_tabla_amortizacion(contrato.id)
                actualizar_moras_contrato(contrato.id)
                generar_pdf_contrato(contrato.id)

                messages.success(request, f'Contrato N° {contrato.id} generado exitosamente.')
                return redirect('detalle_contrato', pk=contrato.id)

        except Exception as e:
            messages.error(request, f"Error al generar la venta: {str(e)}")
            return redirect('crear_venta')

    # GET
    # Filtrar lotes disponibles según el usuario
    if request.user.is_superuser:
        # Superusuarios ven todos los lotes disponibles
        lotes_disponibles = Lote.objects.filter(estado='DISPONIBLE')
    else:
        # Usuarios normales solo ven sus propios lotes
        lotes_disponibles = Lote.objects.filter(estado='DISPONIBLE', creado_por=request.user)
    
    return render(request, 'ventas/nueva_venta.html', {'lotes_disponibles': lotes_disponibles})

# ==========================================
# 3. LISTADO DE CLIENTES
# ==========================================
@login_required
def lista_clientes_view(request):
    # Filtro de seguridad: Vendedor solo ve lo suyo
    if request.user.is_superuser:
        clientes = Cliente.objects.all()
    else:
        clientes = Cliente.objects.filter(vendedor=request.user)
    
    return render(request, 'ventas/lista_clientes.html', {'clientes': clientes})

# ==========================================
# 4. DETALLE CONTRATO (Panel Cliente)
# ==========================================
@login_required
def detalle_contrato_view(request, pk):
    contrato = get_object_or_404(Contrato, pk=pk)
    
    if not request.user.is_superuser and contrato.cliente.vendedor != request.user:
        messages.error(request, "No tiene permisos para acceder a esta información.")
        return redirect('dashboard')

    # 1. Actualizar cálculo matemático al instante
    actualizar_moras_contrato(contrato.id)

    cuotas = contrato.cuotas.all().order_by('numero_cuota')
    
    # 2. Filtrar las vencidas
    cuotas_vencidas = cuotas.filter(estado='VENCIDO')
    
    # NUEVA LÓGICA:
    # ¿Hay alguna fila roja? (True/False)
    hay_vencidas = cuotas_vencidas.exists() 
    
    # Sumar dinero de mora
    total_mora = sum(c.valor_mora for c in cuotas_vencidas)
    
    # Saldo pendiente real
    saldo_pendiente_total = sum(c.total_a_pagar - c.valor_pagado for c in cuotas)

    # Próxima a pagar (La primera PENDIENTE o PARCIAL, excluyendo VENCIDO para el indicador)
    proxima_cuota = cuotas.filter(estado__in=['PENDIENTE', 'PARCIAL']).first()

    context = {
        'contrato': contrato,
        'cuotas': cuotas,
        'total_mora': total_mora,
        'hay_vencidas': hay_vencidas, 
        'proxima_cuota': proxima_cuota,
        'saldo_pendiente_total': saldo_pendiente_total,
        'puede_cerrar': saldo_pendiente_total <= 0 and contrato.estado == 'ACTIVO'
    }
    return render(request, 'ventas/detalle_cliente.html', context)

@login_required
def cerrar_contrato_view(request, pk):
    contrato = get_object_or_404(Contrato, pk=pk)
    
    # Validaciones de seguridad
    saldo_pendiente = sum((c.valor_capital + c.valor_mora) - c.valor_pagado for c in contrato.cuotas.all())
    
    if saldo_pendiente > 0:
        messages.error(request, "Error: No se puede cerrar un contrato con deuda pendiente.")
        return redirect('detalle_contrato', pk=pk)

    if request.method == 'POST':
        contrato.estado = 'CERRADO'
        contrato.save()
        messages.success(request, f"¡Contrato #{contrato.id} finalizado exitosamente!")
    
    return redirect('detalle_contrato', pk=pk)


@login_required
def cancelar_contrato_view(request, pk):
    from datetime import date
    contrato = get_object_or_404(Contrato, pk=pk)
    
    if request.method == 'POST':
        contrato.estado = 'CANCELADO'
        contrato.fecha_cancelacion = date.today()
        contrato.save()
        
        # Liberar el lote
        contrato.lote.estado = 'DISPONIBLE'
        contrato.lote.save()
        
        messages.warning(request, f"¡Contrato #{contrato.id} ha sido cancelado! El lote está disponible nuevamente.")
    
    return redirect('detalle_contrato', pk=pk)


@login_required
def devolucion_contrato_view(request, pk):
    from datetime import date
    contrato = get_object_or_404(Contrato, pk=pk)
    
    if request.method == 'POST':
        contrato.estado = 'DEVOLUCION'
        contrato.fecha_cancelacion = date.today()
        contrato.save()
        
        # Liberar el lote
        contrato.lote.estado = 'DISPONIBLE'
        contrato.lote.save()
        
        # Calculate total paid for feedback
        total_pagado = sum(p.monto for p in contrato.pago_set.all())
        messages.info(request, f"¡Contrato #{contrato.id} en devolución! Total a devolver: ${total_pagado}. El lote está disponible nuevamente.")
    
    return redirect('detalle_contrato', pk=pk)

# ==========================================
# 5. REGISTRAR PAGO
# ==========================================
@login_required
def registrar_pago_view(request, pk):
    contrato = get_object_or_404(Contrato, pk=pk)

    if request.method == 'POST':
        monto = float(request.POST.get('monto'))
        metodo = request.POST.get('metodo_pago')
        imagen = request.FILES.get('comprobante') # Puede ser None si es efectivo

        try:
            # Llamamos al servicio inteligente
            registrar_pago_cliente(
                contrato_id=contrato.id,
                monto=monto,
                metodo_pago=metodo,
                evidencia_img=imagen,
                usuario_vendedor=request.user
            )
            messages.success(request, "Pago registrado con éxito.")
            return redirect('detalle_contrato', pk=contrato.id)
        except Exception as e:
            messages.error(request, f"Error en pago: {str(e)}")
    
    return render(request, 'ventas/form_pago.html', {'contrato': contrato})

# ==========================================
# 6. DESCARGAS Y ARCHIVOS
# ==========================================
@login_required
def descargar_contrato_pdf(request, pk):
    contrato = get_object_or_404(Contrato, pk=pk)
    
    # Regeneramos siempre el PDF para asegurar que tenga los últimos cambios de la plantilla
    generar_pdf_contrato(contrato.id)
    contrato.refresh_from_db()
    
    if contrato.archivo_contrato_pdf:
        return FileResponse(contrato.archivo_contrato_pdf.open(), as_attachment=True, filename=f"Contrato_{contrato.id}.pdf")
    else:
        return HttpResponse("El PDF no se encuentra disponible.", status=404)

@login_required
def descargar_contrato_word(request, pk):
    contrato = get_object_or_404(Contrato, pk=pk)
    # Usamos ConfiguracionSistema en lugar de Empresa
    config = ConfiguracionSistema.objects.first()
    
    # Preparamos un objeto 'empresa' simulado o usamos config directamente, 
    # pero para mantener compatibilidad con el template que espera 'empresa.representante_legal' etc.
    # Si ConfiguracionSistema no tiene esos campos exactos, los ajustamos.
    # Mirando models.py: ConfiguracionSistema tiene nombre_empresa, ruc_empresa. 
    # No tiene representante_legal. Usaremos valores por defecto en el template o agregamos aqui.
    
    empresa_data = {
        'representante_legal': "GUILLERMO UGSHA ILAQUICHE", # Hardcoded si no está en modelo
        'ruc': config.ruc_empresa if config else "050289591-5"
    }
    
    
    # === Lógica de Pagos para Word ===
    pago_entrada = contrato.pago_set.order_by('id').first()
    metodo_real = 'EFECTIVO'
    datos_bancarios = None

    if pago_entrada:
        obs = pago_entrada.observacion or ""
        if 'TRANSFERENCIA' in obs:
            metodo_real = 'TRANSFERENCIA BANCARIA'
            try:
                if "Banco:" in obs and "Cuenta/Comp:" in obs:
                    resto_banco = obs.split("Banco:")[1]
                    if ". Cuenta/Comp:" in resto_banco:
                        parte_banco, _, parte_cuenta = resto_banco.partition(". Cuenta/Comp:")
                        datos_bancarios = {'banco': parte_banco.strip(), 'cuenta': parte_cuenta.rstrip(".").strip()}
                    else:
                        parte_banco = resto_banco.split("Cuenta/Comp:")[0].strip().rstrip(".")
                        parte_cuenta = resto_banco.split("Cuenta/Comp:")[1].strip().rstrip(".")
                        datos_bancarios = {'banco': parte_banco, 'cuenta': parte_cuenta}
            except:
                pass
        elif 'DEPOSITO' in obs:
            metodo_real = 'DEPÓSITO'
        elif pago_entrada.metodo_pago == 'EFECTIVO':
            metodo_real = 'EFECTIVO'

    # Estrategia 3: URL de Archivo Local (file://) para que Word lo busque en disco
    # Esto funciona porque el servidor y el cliente (Word) están en la misma máquina.
    logo_url = ""
    try:
        abs_path = finders.find('img/logo.png')
        if abs_path:
            # Convertir 'C:\ruta\...' a 'file:///C:/ruta/...'
            logo_url = 'file:///' + abs_path.replace('\\', '/')
    except:
        pass

    # Obtener URL base para imágenes en Word (http://127.0.0.1:8000)
    base_url = request.build_absolute_uri('/')[:-1] 

    context = {
        'contrato': contrato,
        'cliente': contrato.cliente,
        'lote': contrato.lote,
        'empresa': empresa_data,
        'cuotas': contrato.cuotas.all().order_by('numero_cuota'),
        'metodo_real_pago': metodo_real,
        'datos_bancarios': datos_bancarios,
        'logo_url': logo_url,
        'base_url': base_url,
        'fecha_actual': date.today()
    }
    
    html_string = render_to_string('reportes/plantilla_contrato.html', context)
    
    response = HttpResponse(html_string, content_type='application/msword')
    response['Content-Disposition'] = f'attachment; filename="Contrato_{contrato.cliente.apellidos}_{contrato.cliente.nombres}.doc"'
    return response

@login_required
def ver_comprobante_view(request, pago_id):
    pago = get_object_or_404(Pago, id=pago_id)
    if pago.comprobante_imagen:
        return FileResponse(pago.comprobante_imagen.open())
    return HttpResponse("No hay imagen asociada.", status=404)

def gestion_lotes_view(request):
    # Lista tipo Excel de todos los lotes
    lotes = Lote.objects.all().order_by('manzana', 'numero_lote')
    return render(request, 'gestion/lotes_lista.html', {'lotes': lotes})

def crear_lote_view(request):
    if request.method == 'POST':
        try:
            lote = Lote.objects.create(
                manzana=request.POST.get('manzana'),
                numero_lote=request.POST.get('numero_lote'),
                dimensiones=request.POST.get('dimensiones'),
                precio_contado=request.POST.get('precio'),
                estado='DISPONIBLE',
                creado_por=request.user, # Asignar el creador
                
                # Campos nuevos (Opcionales)
                ciudad=request.POST.get('ciudad'),
                parroquia=request.POST.get('parroquia'),
                provincia=request.POST.get('provincia'),
                canton=request.POST.get('canton')
            )
            # Handle image upload
            if 'imagen' in request.FILES:
                lote.imagen = request.FILES['imagen']
                lote.save()
            messages.success(request, "Lote creado correctamente en el inventario.")
            return redirect('gestion_lotes')
        except Exception as e:
            messages.error(request, f"Error al crear lote: {e}")
    
    return render(request, 'gestion/lotes_form.html')

@login_required
def editar_lote_view(request, pk):
    lote = get_object_or_404(Lote, pk=pk)
    
    # Verificar permisos: solo el creador o superusuario pueden editar
    # Verificación más estricta comparando IDs
    puede_editar = (
        request.user.is_superuser or 
        (lote.creado_por is not None and lote.creado_por.id == request.user.id)
    )
    
    if not puede_editar:
        messages.error(request, "No tiene permisos para editar este lote. Solo el creador puede modificarlo.")
        return redirect('gestion_lotes')

    if request.method == 'POST':
        try:
            lote.manzana = request.POST.get('manzana')
            lote.numero_lote = request.POST.get('numero_lote')
            lote.dimensiones = request.POST.get('dimensiones')
            lote.precio_contado = request.POST.get('precio')
            
            # Nuevos campos
            lote.ciudad = request.POST.get('ciudad')
            lote.parroquia = request.POST.get('parroquia')
            lote.provincia = request.POST.get('provincia')
            lote.canton = request.POST.get('canton')
            # Handle image upload
            if 'imagen' in request.FILES:
                lote.imagen = request.FILES['imagen']
            lote.save()
            messages.success(request, f"Lote #{lote.id} actualizado correctamente.")
            return redirect('gestion_lotes')
        except Exception as e:
            messages.error(request, f"Error al actualizar lote: {e}")
    
    return render(request, 'gestion/lotes_form.html', {'lote': lote})

# ==========================================
# REPORTE MENSUAL DE INGRESOS Y MORA
# ==========================================
@login_required
def reporte_mensual_view(request):
    from datetime import date
    from decimal import Decimal
    
    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1)
    
    # Filtro de seguridad por vendedor
    if request.user.is_superuser:
        contratos = Contrato.objects.filter(estado='ACTIVO')
        pagos_mes = Pago.objects.filter(fecha_pago__gte=primer_dia_mes, fecha_pago__lte=hoy)
    else:
        contratos = Contrato.objects.filter(estado='ACTIVO', cliente__vendedor=request.user)
        pagos_mes = Pago.objects.filter(
            fecha_pago__gte=primer_dia_mes, 
            fecha_pago__lte=hoy,
            contrato__cliente__vendedor=request.user
        )
    
    # === INGRESOS DEL MES ===
    ingresos_por_cliente = {}
    for pago in pagos_mes:
        cliente = pago.contrato.cliente
        if cliente.id not in ingresos_por_cliente:
            ingresos_por_cliente[cliente.id] = {
                'cliente': cliente,
                'contrato': pago.contrato,
                'total_pagado': Decimal('0.00')
            }
        ingresos_por_cliente[cliente.id]['total_pagado'] += pago.monto
    
    total_ingresos = sum(c['total_pagado'] for c in ingresos_por_cliente.values())
    
    # === CLIENTES EN MORA ===
    clientes_en_mora = {}
    for contrato in contratos.filter(esta_en_mora=True):
        cuotas_vencidas = contrato.cuotas.filter(estado='VENCIDO')
        deuda_total = sum(c.saldo_pendiente for c in cuotas_vencidas)
        
        if deuda_total > 0:
            clientes_en_mora[contrato.cliente.id] = {
                'cliente': contrato.cliente,
                'contrato': contrato,
                'cuotas_vencidas': cuotas_vencidas.count(),
                'deuda_total': deuda_total
            }
    
    total_mora = sum(c['deuda_total'] for c in clientes_en_mora.values())

    # === DEVOLUCIONES DEL MES ===
    # Contratos que cambiaron a estado 'DEVOLUCION' en este rango de fecha
    contratos_devolucion = Contrato.objects.filter(
        estado='DEVOLUCION',
        fecha_cancelacion__gte=primer_dia_mes,
        fecha_cancelacion__lte=hoy
    )
    if not request.user.is_superuser:
        contratos_devolucion = contratos_devolucion.filter(cliente__vendedor=request.user)

    devoluciones_lista = []
    total_devoluciones = Decimal('0.00')

    for c in contratos_devolucion:
        # Sumamos todos los pagos realizados a este contrato
        monto_devuelto = sum(p.monto for p in c.pago_set.all())
        total_devoluciones += monto_devuelto
        
        devoluciones_lista.append({
            'cliente': c.cliente,
            'contrato': c,
            'monto': monto_devuelto
        })

    # Ingreso Neto: (Ingresos Reales) - (Mora Pendiente) - (Devoluciones)
    # Nota: Restar Mora es criterio del usuario, aunque contablemente es solo lo que NO entró.
    # Restar Devoluciones es salida de efectivo.
    ingreso_neto = total_ingresos - total_mora - total_devoluciones
    
    context = {
        'fecha_inicio': primer_dia_mes,
        'fecha_fin': hoy,
        'ingresos_lista': sorted(ingresos_por_cliente.values(), key=lambda x: x['total_pagado'], reverse=True),
        'total_ingresos': total_ingresos,
        'mora_lista': sorted(clientes_en_mora.values(), key=lambda x: x['deuda_total'], reverse=True),
        'total_mora': total_mora,
        'devoluciones_lista': devoluciones_lista,
        'total_devoluciones': total_devoluciones,
        'ingreso_neto': ingreso_neto,
    }
    return render(request, 'reportes/reporte_mensual.html', context)


@login_required
def reporte_general_view(request):
    from datetime import date
    from dateutil.relativedelta import relativedelta
    from decimal import Decimal
    
    # Get date range from GET params
    desde_str = request.GET.get('desde', None)  # Format: YYYY-MM
    hasta_str = request.GET.get('hasta', None)
    solo_activos = request.GET.get('solo_activos', None) == 'on'
    
    # Parse dates
    if desde_str:
        desde_year, desde_month = map(int, desde_str.split('-'))
        desde = date(desde_year, desde_month, 1)
    else:
        desde = date.today().replace(day=1, month=1)  # Default to January this year
    
    if hasta_str:
        hasta_year, hasta_month = map(int, hasta_str.split('-'))
        # Last day of the month
        hasta = date(hasta_year, hasta_month, 1) + relativedelta(months=1) - relativedelta(days=1)
    else:
        hasta = date.today()
    
    # Generate list of months between desde and hasta
    meses_nombres = {
        1: 'Ene', 2: 'Feb', 3: 'Mar', 4: 'Abr', 5: 'May', 6: 'Jun',
        7: 'Jul', 8: 'Ago', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dic'
    }
    
    meses = []
    current = desde
    while current <= hasta:
        meses.append({
            'year': current.year,
            'month': current.month,
            'label': f"{meses_nombres[current.month]} {current.year}"
        })
        current = current + relativedelta(months=1)
    
    # Get contracts
    contratos_qs = Contrato.objects.select_related('cliente', 'lote').prefetch_related('cuotas', 'pago_set')
    
    if not request.user.is_superuser:
        contratos_qs = contratos_qs.filter(cliente__vendedor=request.user)
    
    if solo_activos:
        contratos_qs = contratos_qs.filter(estado='ACTIVO')
    
    # Build report data
    reporte_data = []
    
    # Initialize totals for each month
    totales_mensuales = [Decimal('0.00') for _ in meses]
    total_general = Decimal('0.00')
    total_cuotas = Decimal('0.00')  # Total de todas las cuotas mensuales
    
    for contrato in contratos_qs:
        # Basic data
        row = {
            'contrato': contrato,
            'cliente': contrato.cliente,
            'lote': contrato.lote,
            'pagos_mensuales': [],
            'total_pagado': Decimal('0.00'),
            'es_devolucion': contrato.estado == 'DEVOLUCION'
        }
        
        # Calculate cuota value for totals
        primera_cuota = contrato.cuotas.first()
        if primera_cuota:
            total_cuotas += primera_cuota.valor_capital
        
        # For each month, calculate total paid based on CUOTA due date
        for i, mes in enumerate(meses):
            mes_inicio = date(mes['year'], mes['month'], 1)
            mes_fin = mes_inicio + relativedelta(months=1) - relativedelta(days=1)
            
            # Sum valor_pagado for cuotas that are DUE in this month
            cuotas_mes = contrato.cuotas.filter(
                fecha_vencimiento__gte=mes_inicio,
                fecha_vencimiento__lte=mes_fin
            )
            total_mes = sum(c.valor_pagado for c in cuotas_mes)
            row['pagos_mensuales'].append(total_mes)
            row['total_pagado'] += total_mes
            
            # Add to monthly totals (subtract if devolucion)
            if row['es_devolucion']:
                totales_mensuales[i] -= total_mes
            else:
                totales_mensuales[i] += total_mes
        
        # Add to general total
        if row['es_devolucion']:
            total_general -= row['total_pagado']
        else:
            total_general += row['total_pagado']
        
        reporte_data.append(row)
    
    context = {
        'desde': desde,
        'hasta': hasta,
        'meses': meses,
        'reporte_data': reporte_data,
        'solo_activos': solo_activos,
        'totales_mensuales': totales_mensuales,
        'total_general': total_general,
        'total_cuotas': total_cuotas,
    }
    return render(request, 'reportes/reporte_general.html', context)

@login_required
def reporte_general_pdf_view(request):
    from datetime import datetime, date
    from decimal import Decimal
    from dateutil.relativedelta import relativedelta
    from io import BytesIO
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    from .services import link_callback
    
    # Get filter parameters
    desde_str = request.GET.get('desde')
    hasta_str = request.GET.get('hasta')
    solo_activos = request.GET.get('solo_activos') == 'on'
    
    # Parse dates
    desde = datetime.strptime(desde_str, '%Y-%m').date() if desde_str else date.today().replace(day=1)
    hasta = datetime.strptime(hasta_str, '%Y-%m').date() if hasta_str else date.today()
    
    # Generate month list
    meses = []
    current_date = desde.replace(day=1)
    hasta_mes = hasta.replace(day=1)
    
    while current_date <= hasta_mes:
        meses.append({
            'year': current_date.year,
            'month': current_date.month,
            'label': current_date.strftime('%b %y').upper()
        })
        current_date += relativedelta(months=1)
    
    # Get contracts
    contratos_qs = Contrato.objects.select_related('cliente', 'lote').all()
    
    if not request.user.is_superuser:
        contratos_qs = contratos_qs.filter(cliente__vendedor=request.user)
    
    if solo_activos:
        contratos_qs = contratos_qs.filter(estado='ACTIVO')
    
    # Build report data (same logic as reporte_general_view)
    reporte_data = []
    totales_mensuales = [Decimal('0.00') for _ in meses]
    total_general = Decimal('0.00')
    total_cuotas = Decimal('0.00')
    
    for contrato in contratos_qs:
        row = {
            'contrato': contrato,
            'cliente': contrato.cliente,
            'lote': contrato.lote,
            'pagos_mensuales': [],
            'total_pagado': Decimal('0.00'),
            'es_devolucion': contrato.estado == 'DEVOLUCION'
        }
        
        primera_cuota = contrato.cuotas.first()
        if primera_cuota:
            total_cuotas += primera_cuota.valor_capital
        
        for i, mes in enumerate(meses):
            mes_inicio = date(mes['year'], mes['month'], 1)
            mes_fin = mes_inicio + relativedelta(months=1) - relativedelta(days=1)
            
            cuotas_mes = contrato.cuotas.filter(
                fecha_vencimiento__gte=mes_inicio,
                fecha_vencimiento__lte=mes_fin
            )
            total_mes = sum(c.valor_pagado for c in cuotas_mes)
            row['pagos_mensuales'].append(total_mes)
            row['total_pagado'] += total_mes
            
            if row['es_devolucion']:
                totales_mensuales[i] -= total_mes
            else:
                totales_mensuales[i] += total_mes
        
        if row['es_devolucion']:
            total_general -= row['total_pagado']
        else:
            total_general += row['total_pagado']
        
        reporte_data.append(row)
    
    context = {
        'desde': desde,
        'hasta': hasta,
        'meses': meses,
        'reporte_data': reporte_data,
        'totales_mensuales': totales_mensuales,
        'total_general': total_general,
        'total_cuotas': total_cuotas,
    }
    
    html_string = render_to_string('reportes/reporte_general_pdf.html', context)
    result_file = BytesIO()
    pisa.CreatePDF(html_string, dest=result_file, link_callback=link_callback)
    
    response = HttpResponse(result_file.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Reporte_General_{desde.strftime("%Y-%m")}_to_{hasta.strftime("%Y-%m")}.pdf"'
    return response

@login_required
def reporte_mensual_pdf_view(request):
    from datetime import date
    from decimal import Decimal
    from io import BytesIO
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    from .services import link_callback
    
    hoy = date.today()
    primer_dia_mes = hoy.replace(day=1)
    
    if request.user.is_superuser:
        contratos = Contrato.objects.filter(estado='ACTIVO')
        pagos_mes = Pago.objects.filter(fecha_pago__gte=primer_dia_mes, fecha_pago__lte=hoy)
    else:
        contratos = Contrato.objects.filter(estado='ACTIVO', cliente__vendedor=request.user)
        pagos_mes = Pago.objects.filter(
            fecha_pago__gte=primer_dia_mes, 
            fecha_pago__lte=hoy,
            contrato__cliente__vendedor=request.user
        )
    
    ingresos_por_cliente = {}
    for pago in pagos_mes:
        cliente = pago.contrato.cliente
        if cliente.id not in ingresos_por_cliente:
            ingresos_por_cliente[cliente.id] = {
                'cliente': cliente,
                'contrato': pago.contrato,
                'total_pagado': Decimal('0.00')
            }
        ingresos_por_cliente[cliente.id]['total_pagado'] += pago.monto
    
    total_ingresos = sum(c['total_pagado'] for c in ingresos_por_cliente.values())
    
    clientes_en_mora = {}
    for contrato in contratos.filter(esta_en_mora=True):
        cuotas_vencidas = contrato.cuotas.filter(estado='VENCIDO')
        deuda_total = sum(c.saldo_pendiente for c in cuotas_vencidas)
        if deuda_total > 0:
            clientes_en_mora[contrato.cliente.id] = {
                'cliente': contrato.cliente,
                'contrato': contrato,
                'cuotas_vencidas': cuotas_vencidas.count(),
                'deuda_total': deuda_total
            }
    
    total_mora = sum(c['deuda_total'] for c in clientes_en_mora.values())
    ingreso_neto = total_ingresos - total_mora
    
    context = {
        'fecha_inicio': primer_dia_mes,
        'fecha_fin': hoy,
        'ingresos_lista': sorted(ingresos_por_cliente.values(), key=lambda x: x['total_pagado'], reverse=True),
        'total_ingresos': total_ingresos,
        'mora_lista': sorted(clientes_en_mora.values(), key=lambda x: x['deuda_total'], reverse=True),
        'total_mora': total_mora,
        'ingreso_neto': ingreso_neto,
    }
    
    html_string = render_to_string('reportes/reporte_mensual_pdf.html', context)
    result_file = BytesIO()
    pisa.CreatePDF(html_string, dest=result_file, link_callback=link_callback)
    
    response = HttpResponse(result_file.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Reporte_Mensual_{hoy.strftime("%Y-%m")}.pdf"'
    return response

# ==========================================
# CONTROL MANUAL DE MORA
# ==========================================
@login_required
def toggle_mora_cuota(request, cuota_id):
    """
    Activa o desactiva la exención de mora para una cuota específica.
    """
    cuota = get_object_or_404(Cuota, id=cuota_id)
    contrato = cuota.contrato
    
    # Verificar permisos
    if not request.user.is_superuser and contrato.cliente.vendedor != request.user:
        messages.error(request, "No tienes permisos para modificar esta cuota.")
        return redirect('detalle_contrato', pk=contrato.id)
    
    if request.method == 'POST':
        # Cambiar el estado de exención
        cuota.mora_exenta = not cuota.mora_exenta
        cuota.save()
        
        # Recalcular moras del contrato
        actualizar_moras_contrato(contrato.id)
        
        if cuota.mora_exenta:
            messages.success(request, f"✓ Cuota #{cuota.numero_cuota} exenta de mora.")
        else:
            messages.info(request, f"Cuota #{cuota.numero_cuota} volverá a calcular mora automáticamente.")
    
    return redirect('detalle_contrato', pk=contrato.id)

