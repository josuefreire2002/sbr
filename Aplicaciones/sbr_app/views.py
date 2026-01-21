from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Q
from django.http import FileResponse, HttpResponse
from datetime import date
from django.db import transaction
from .services import actualizar_moras_contrato 

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
                
                contrato = Contrato.objects.create(
                    cliente=cliente,
                    lote=lote,
                    fecha_contrato=fecha_contrato_str, # Usamos la fecha que eligió el usuario
                    precio_venta_final=float(request.POST.get('precio_final')),
                    valor_entrada=float(request.POST.get('entrada')),
                    saldo_a_financiar=float(request.POST.get('saldo')),
                    numero_cuotas=int(request.POST.get('plazo'))
                )
                
                # Guardamos la observación si existe (agregamos campo a modelo o usamos uno auxiliar)
                observacion = request.POST.get('observacion')
                # Si no agregaste el campo 'observacion' al modelo Contrato, puedes omitirlo o agregarlo ahora.
                # contrato.observacion = observacion 
                # contrato.save()

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
    if contrato.archivo_contrato_pdf:
        return FileResponse(contrato.archivo_contrato_pdf.open(), as_attachment=True, filename=f"Contrato_{contrato.id}.pdf")
    else:
        # Intentar regenerar si no existe
        url = generar_pdf_contrato(contrato.id)
        if url:
             return redirect(url)
        return HttpResponse("El PDF no se encuentra disponible.", status=404)

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
            Lote.objects.create(
                manzana=request.POST.get('manzana'),
                numero_lote=request.POST.get('numero_lote'),
                dimensiones=request.POST.get('dimensiones'),
                precio_contado=request.POST.get('precio'),
                estado='DISPONIBLE',
                creado_por=request.user  # Asignar el creador
            )
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
            # Estado is usually not edited here manually unless requested, sticking to basic fields
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
    
    for contrato in contratos_qs:
        # Basic data
        row = {
            'contrato': contrato,
            'cliente': contrato.cliente,
            'lote': contrato.lote,
            'pagos_mensuales': [],
            'total_pagado': Decimal('0.00')
        }
        
        # For each month, calculate total paid based on CUOTA due date
        for mes in meses:
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
        
        reporte_data.append(row)
    
    context = {
        'desde': desde,
        'hasta': hasta,
        'meses': meses,
        'reporte_data': reporte_data,
        'solo_activos': solo_activos,
    }
    return render(request, 'reportes/reporte_general.html', context)

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
