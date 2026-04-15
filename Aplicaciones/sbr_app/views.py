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
from .models import Cliente, Lote, Contrato, Pago, Cuota, ConfiguracionSistema, DetallePago, MovimientoCaja

# Importamos Servicios (La lógica pesada)
from .services import (
    generar_tabla_amortizacion, 
    registrar_pago_cliente, 
    generar_pdf_contrato,
    generar_recibo_entrada_buffer,
    generar_recibo_pago_buffer
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
                
                # 1. CLIENTE (Buscar o Crear)
                cedula = request.POST.get('cedula')
                
                # Usamos filter().first() para evitar error si hay múltiples clientes con la misma cédula (base de datos sucia)
                cliente = Cliente.objects.filter(cedula=cedula).first()
                
                if cliente:
                    # El cliente existe, actualizamos sus datos
                    created = False
                else:
                    # No existe, lo creamos
                    cliente = Cliente.objects.create(
                        vendedor=request.user,
                        cedula=cedula,
                        nombres=request.POST.get('nombres'),
                        apellidos=request.POST.get('apellidos'),
                        celular=request.POST.get('celular'),
                        email=request.POST.get('email'),
                        direccion=request.POST.get('direccion')
                    )
                    created = True
                
                # Si el cliente ya existe (o acabamos de crearlo, aunque redundante, lo aseguramos), actualizamos
                # Y IMPORTANTE: Lo asignamos al vendedor actual para que le aparezca en su lista
                cliente.vendedor = request.user
                
                if not created:
                    cliente.nombres = request.POST.get('nombres')
                    cliente.apellidos = request.POST.get('apellidos')
                    cliente.celular = request.POST.get('celular')
                    if request.POST.get('email'):
                        cliente.email = request.POST.get('email')
                    cliente.direccion = request.POST.get('direccion')
                    cliente.save()

                # 2. LOTES (Soporte Múltiple)
                # Obtenemos la lista de IDs. El select en el HTML debe ser name="lote_id" y multiple
                lotes_ids = request.POST.getlist('lote_id')
                if not lotes_ids:
                     # Fallback por si acaso viene como string único
                     single_id = request.POST.get('lote_id')
                     if single_id: lotes_ids = [single_id]
                
                lotes_objs = Lote.objects.filter(id__in=lotes_ids)
                
                if not lotes_objs.exists():
                     raise Exception("Debe seleccionar al menos un lote válido.")

                # 3. DATOS ECONÓMICOS Y CONTRATO
                fecha_contrato_str = request.POST.get('fecha_contrato') 
                
                # 3.1. CAPTURAR MÉTODO DE PAGO ENTRADA
                metodo_entrada = request.POST.get('metodo_pago_entrada')
                banco_entrada = request.POST.get('banco_entrada')
                cuenta_entrada = request.POST.get('cuenta_entrada')
                
                metodo_modelo = 'TRANSFERENCIA' if metodo_entrada in ['TRANSFERENCIA', 'DEPOSITO'] else 'EFECTIVO'
                
                observacion_pago = f"Pago de Entrada ({metodo_entrada})."
                if metodo_entrada == 'TRANSFERENCIA':
                    observacion_pago += f" Banco: {banco_entrada}. Cuenta/Comp: {cuenta_entrada}."
                elif metodo_entrada == 'DEPOSITO':
                     observacion_pago += f" Depósito. Banco: {banco_entrada}. Comp: {cuenta_entrada}."

                contrato = Contrato.objects.create(
                    cliente=cliente,
                    fecha_contrato=fecha_contrato_str,
                    precio_venta_final=Decimal(request.POST.get('precio_final')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                    valor_entrada=Decimal(request.POST.get('entrada')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                    saldo_a_financiar=Decimal(request.POST.get('saldo')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                    numero_cuotas=int(request.POST.get('plazo')),
                    observacion=request.POST.get('observacion')
                )
                
                # Asignar relación M2M y FK temporal
                contrato.lotes.set(lotes_objs)
                contrato.lote = lotes_objs.first() # Mantener compatibilidad temporal
                contrato.save() # Guardar cambios FK

                # 3.2. REGISTRAR PAGO DE ENTRADA
                if contrato.valor_entrada > 0:
                    Pago.objects.create(
                        contrato=contrato,
                        fecha_pago=fecha_contrato_str,
                        monto=contrato.valor_entrada,
                        metodo_pago=metodo_modelo,
                        comprobante_imagen=request.FILES.get('comprobante'),
                        observacion=observacion_pago,
                        registrado_por=request.user,
                        es_entrada=True
                    )

                # Marcar lotes como vendidos
                for l in lotes_objs:
                    l.estado = 'VENDIDO'
                    l.save()

                fecha_pago_input = request.POST.get('fecha_primer_pago')

                # 4. GENERAR LÓGICA
                generar_tabla_amortizacion(contrato.id, fecha_inicio_pago_str=fecha_pago_input)
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
        contratos_activos = Contrato.objects.filter(estado='ACTIVO')
        contratos = Contrato.objects.select_related('cliente').prefetch_related('lotes').order_by('fecha_contrato', 'id')
    else:
        clientes = Cliente.objects.filter(vendedor=request.user)
        contratos_activos = Contrato.objects.filter(estado='ACTIVO', cliente__vendedor=request.user)
        contratos = Contrato.objects.filter(cliente__vendedor=request.user).select_related('cliente').prefetch_related('lotes').order_by('fecha_contrato', 'id')
    
    # NUEVA LÓGICA: Forzar recálculo masivo de moras en tiempo real para todos los contratos listados
    from .services import actualizar_moras_masivo
    actualizar_moras_masivo(contratos_activos)
    
    return render(request, 'ventas/lista_clientes.html', {'clientes': clientes, 'contratos': contratos})

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

    from django.db.models import Sum

    # IMPORTANTE: Usar Cuota.objects.filter para evitar caché del ORM de relaciones
    # Se añade prefetch_related('pagos_asociados', 'pagos_asociados__pago') para acceder a los detalles en el template
    # Se añade annotate(total_transaccion=Sum('pagos_asociados__pago__monto')) para mostrar el monto total de los pagos involucrados
    cuotas = Cuota.objects.filter(contrato=contrato).order_by('numero_cuota').prefetch_related(
        'pagos_asociados', 
        'pagos_asociados__pago',
        'pagos_asociados__pago__detalles',
        'pagos_asociados__pago__detalles__cuota'
    ).annotate(total_transaccion=Sum('pagos_asociados__pago__monto'))
    
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

    # --- NUEVA LÓGICA: Historial de Pagos con Detalles ---
    pagos_historial = contrato.pago_set.all().order_by('-fecha_pago', '-id').prefetch_related('detalles', 'detalles__cuota')

    # Lógica para determinar "Cuota Principal" vs "Abastecida"
    # 1. Mapear cada Pago ID a su Cuota Inicial (la del numero_cuota más bajo)
    pago_inicio_map = {} # {pago_id: numero_cuota_minimo}
    
    # Usamos los detalles ya cargados en pagos_historial para mayor eficiencia
    # O mejor, una query directa liviana a DetallePago
    detalles_all = DetallePago.objects.filter(pago__contrato=contrato).select_related('cuota', 'pago')
    
    for d in detalles_all:
        pid = d.pago_id
        q_num = d.cuota.numero_cuota
        if pid not in pago_inicio_map:
            pago_inicio_map[pid] = q_num
        else:
            if q_num < pago_inicio_map[pid]:
                pago_inicio_map[pid] = q_num
                
    # 2. Marcar cada cuota
    for c in cuotas:
        c.es_principal_de_algun_pago = False
        c.monto_real_abonado = Decimal('0.00')
        origenes = set()
        
        # Iterar sobre los pagos que afectaron a esta cuota
        # Gracias al prefetch en 'cuotas', podemos iterar c.pagos_asociados.all()
        for detalle in c.pagos_asociados.all():
             pago_id = detalle.pago_id
             inicio_num = pago_inicio_map.get(pago_id)
             
             # Annotate for template use
             detalle.pago.cuota_inicial_numero = inicio_num
             
             if inicio_num == c.numero_cuota:
                 c.es_principal_de_algun_pago = True
                 # Sumar el monto total de la transacción original radicada en esta cuota
                 c.monto_real_abonado += detalle.pago.monto
             else:
                 # Es secundario (surplus)
                 origenes.add(inicio_num)
        
        c.lista_origenes = sorted(list(origenes))
        
        # Failsafe para pagos antiguos sin DetallePago:
        # Si la cuota tiene dinero pagado pero no registraba pago_raiz,
        # mostramos el dinero pagado directamente para evitar que salga $0 en la tabla.
        if c.monto_real_abonado == 0 and c.valor_pagado > 0:
            c.monto_real_abonado = c.valor_pagado


    context = {
        'contrato': contrato,
        'cuotas': cuotas,
        'total_mora': total_mora,
        'hay_vencidas': hay_vencidas, 
        'proxima_cuota': proxima_cuota,
        'saldo_pendiente_total': saldo_pendiente_total,
        'puede_cerrar': saldo_pendiente_total <= 0 and contrato.estado == 'ACTIVO',
        'pagos_historial': pagos_historial
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
        contrato.fecha_fin_contrato = date.today()
        contrato.save()
        
        # Liberar TODOS los lotes asociados al contrato
        for lote in contrato.lotes.all():
            lote.estado = 'DISPONIBLE'
            lote.save()
        
        cant_lotes = contrato.lotes.count()
        lotes_text = "lote" if cant_lotes == 1 else "lotes"
        messages.warning(request, f"¡Contrato #{contrato.id} ha sido cancelado! {cant_lotes} {lotes_text} ahora disponible(s).")
    
    return redirect('detalle_contrato', pk=pk)


@login_required
def devolucion_contrato_view(request, pk):
    from datetime import date
    contrato = get_object_or_404(Contrato, pk=pk)
    
    if request.method == 'POST':
        contrato.estado = 'DEVOLUCION'
        contrato.fecha_fin_contrato = date.today()
        contrato.save()
        
        # Liberar TODOS los lotes asociados al contrato
        for lote in contrato.lotes.all():
            lote.estado = 'DISPONIBLE'
            lote.save()
        
        # Calculate total paid for feedback
        total_pagado = sum(p.monto for p in contrato.pago_set.all())
        cant_lotes = contrato.lotes.count()
        lotes_text = "lote" if cant_lotes == 1 else "lotes"
        messages.info(request, f"¡Contrato #{contrato.id} en devolución! Total a devolver: ${total_pagado}. {cant_lotes} {lotes_text} ahora disponible(s).")
    
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
        
        # Campos opcionales nuevos
        fecha_pago = request.POST.get('fecha_pago')
        cuota_id = request.POST.get('cuota_id')

        try:
            # Llamamos al servicio inteligente
            registrar_pago_cliente(
                contrato_id=contrato.id,
                monto=monto,
                metodo_pago=metodo,
                evidencia_img=imagen,
                usuario_vendedor=request.user,
                fecha_pago=fecha_pago,
                cuota_origen_id=cuota_id
            )
            messages.success(request, "Pago registrado con éxito.")
            return redirect('detalle_contrato', pk=contrato.id)
        except Exception as e:
            messages.error(request, f"Error en pago: {str(e)}")
    
    # Pasamos las cuotas pendientes para el selector
    cuotas_pendientes = contrato.cuotas.filter(
        estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
    ).order_by('numero_cuota')

    return render(request, 'ventas/form_pago.html', {
        'contrato': contrato,
        'cuotas_pendientes': cuotas_pendientes,
        'hoy': date.today()
    })

# ==========================================
# 5.1 GESTIÓN DE CUOTAS (Editar/Eliminar)
# ==========================================
@login_required
def editar_cuota_view(request, pk):
    cuota = get_object_or_404(Cuota, pk=pk)
    contrato = cuota.contrato
    
    if not request.user.is_superuser:
        messages.error(request, "Acceso denegado. Solo administradores pueden editar transacciones.")
        return redirect('detalle_contrato', pk=contrato.id)
        
    # Buscar cuál es el Pago principal que afectó a esta cuota
    detalle_principal = DetallePago.objects.filter(cuota=cuota).order_by('-monto_aplicado').first()
    
    if not detalle_principal:
        messages.error(request, f"La Cuota #{cuota.numero_cuota} no tiene ningún pago asociado para editar. Si desea registrar uno nuevo, use el botón Pagar del contrato.")
        return redirect('detalle_contrato', pk=contrato.id)
        
    pago = detalle_principal.pago
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                nuevo_monto = Decimal(request.POST.get('monto', '0').replace(',', '.') or '0')
                nueva_fecha = request.POST.get('fecha_pago')
                nuevo_metodo = request.POST.get('metodo_pago')
                
                # Opcional: Actualizar el comprobante si suben uno nuevo
                if 'comprobante' in request.FILES:
                    pago.comprobante_imagen = request.FILES['comprobante']
                
                pago.monto = nuevo_monto
                if nueva_fecha:
                     pago.fecha_pago = nueva_fecha
                if nuevo_metodo:
                     pago.metodo_pago = nuevo_metodo
                
                # Eliminar el rastro del saldo a favor de la observación para que el recálculo lo regenere limpio
                if pago.observacion and " | Saldo a favor remanente:" in pago.observacion:
                    pago.observacion = pago.observacion.split(" | Saldo a favor remanente:")[0]
                    
                pago.save()
                
                # EL MOTOR MAGICO DE RECALCULO QUE REPARAMOS ANTERIORMENTE
                from .services import recalcular_deuda_contrato
                recalcular_deuda_contrato(contrato.id)
                
                messages.success(request, f"¡Pago #{pago.id} actualizado exitosamente! La deuda y las distribuciones han sido recalculadas.")
                return redirect('detalle_contrato', pk=contrato.id)
                
        except Exception as e:
            messages.error(request, f"Error al modificar el pago: {str(e)}")
            return redirect('detalle_contrato', pk=contrato.id)

    # Si es GET, mostramos el formulario
    cuotas_pendientes = contrato.cuotas.filter(
        estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
    ).order_by('numero_cuota')
    
    return render(request, 'ventas/form_pago.html', {
        'contrato': contrato,
        'cuotas_pendientes': cuotas_pendientes,
        'hoy': pago.fecha_pago, # Pre-cargar fecha
        'pago': pago, # Pasar el objeto pago para pre-llenar inputs
        'modo_edicion': True
    })

@login_required
def editar_pago_view(request, pago_id):
    """Edita directamente un Pago específico (por su pago_id), independientemente de la cuota."""
    from .models import Pago
    pago = get_object_or_404(Pago, pk=pago_id)
    contrato = pago.contrato

    if not request.user.is_superuser and contrato.cliente.vendedor != request.user:
        messages.error(request, "No tiene permisos para editar este pago.")
        return redirect('detalle_contrato', pk=contrato.id)

    if request.method == 'POST':
        try:
            with transaction.atomic():
                nuevo_monto = Decimal(request.POST.get('monto', '0').replace(',', '.') or '0')
                nueva_fecha = request.POST.get('fecha_pago')
                nuevo_metodo = request.POST.get('metodo_pago')

                if 'comprobante' in request.FILES:
                    pago.comprobante_imagen = request.FILES['comprobante']

                pago.monto = nuevo_monto
                if nueva_fecha:
                    pago.fecha_pago = nueva_fecha
                if nuevo_metodo:
                    pago.metodo_pago = nuevo_metodo

                # Limpiar rastro de saldo a favor para que el recalculo lo regenere limpio
                if pago.observacion and " | Saldo a favor remanente:" in pago.observacion:
                    pago.observacion = pago.observacion.split(" | Saldo a favor remanente:")[0]

                pago.save()

                from .services import recalcular_deuda_contrato
                recalcular_deuda_contrato(contrato.id)

                messages.success(request, f"¡Pago #{pago.id} actualizado exitosamente! La deuda ha sido recalculada.")
                return redirect('detalle_contrato', pk=contrato.id)

        except Exception as e:
            messages.error(request, f"Error al modificar el pago: {str(e)}")
            return redirect('detalle_contrato', pk=contrato.id)

    # GET: mostrar formulario con datos del pago pre-cargados
    return render(request, 'ventas/form_pago.html', {
        'contrato': contrato,
        'cuotas_pendientes': contrato.cuotas.filter(estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']).order_by('numero_cuota'),
        'hoy': pago.fecha_pago,
        'pago': pago,
        'modo_edicion': True,
    })

@login_required
def eliminar_cuota_view(request, pk):
    cuota = get_object_or_404(Cuota, pk=pk)
    contrato_id = cuota.contrato.id
    numero_cuota = cuota.numero_cuota
    
    # Solo superusuarios pueden eliminar cuotas
    if not request.user.is_superuser:
        messages.error(request, "Acceso denegado. Solo administradores pueden eliminar cuotas.")
        return redirect('detalle_contrato', pk=contrato_id)
        
    if request.method == 'POST':
        try:
            with transaction.atomic():
                cuota.delete()
                
                # Recalcular toda la deuda del contrato
                from .services import recalcular_deuda_contrato
                recalcular_deuda_contrato(contrato_id)
                
            messages.success(request, f"Cuota #{numero_cuota} eliminada. Deuda recalculada.")
        except Exception as e:
            messages.error(request, f"Error al eliminar cuota: {e}")
            
    return redirect('detalle_contrato', pk=contrato_id)


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
def descargar_recibo_entrada_pdf(request, pk):
    """
    Genera y descarga el recibo de pago de entrada al vuelo.
    Compatible con móviles (iOS/Android).
    """
    buffer = generar_recibo_entrada_buffer(pk)
    if not buffer:
        return HttpResponse("Error al generar el recibo PDF.", status=500)
    
    # Usar HttpResponse con headers explícitos para compatibilidad móvil
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Recibo_Entrada_{pk}.pdf"'
    response['Content-Length'] = len(buffer.getvalue())
    return response

@login_required
def descargar_contrato_word(request, pk):
    return generar_contrato_word(request, pk)

@login_required
def descargar_recibo_pago_pdf(request, cuota_id):
    """
    Genera y descarga el recibo de pago mensual de una cuota en PDF.
    Compatible con móviles (iOS/Android).
    """
    buffer = generar_recibo_pago_buffer(cuota_id)
    if not buffer:
        from .models import Cuota
        # Si falla, verificar si es porque no está pagado
        c = Cuota.objects.get(id=cuota_id)
        if c.valor_pagado <= 0:
            return HttpResponse("Esta cuota no tiene pagos registrados.", status=400)
        return HttpResponse("Error al generar el recibo PDF.", status=500)
    
    # Usar HttpResponse con headers explícitos para compatibilidad móvil
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Recibo_Cuota_{cuota_id}.pdf"'
    response['Content-Length'] = len(buffer.getvalue())
    return response

# ==========================================
# VISTAS DE PREVIEW PARA MÓVILES
# ==========================================
@login_required
def preview_recibo_entrada(request, pk):
    """
    Muestra página de acciones para el recibo de entrada (compatible con móviles).
    """
    contrato = get_object_or_404(Contrato, pk=pk)
    
    context = {
        'titulo': 'Recibo de Entrada',
        'subtitulo': f'{contrato.cliente.apellidos} {contrato.cliente.nombres}',
        'pdf_url': request.build_absolute_uri(f'/contrato/{pk}/descargar-recibo-entrada/'),
        'url_volver': request.build_absolute_uri(f'/contrato/{pk}/detalle/'),
    }
    return render(request, 'reportes/pdf_acciones.html', context)

@login_required
def preview_recibo_pago(request, cuota_id):
    """
    Muestra página de acciones para el recibo de pago mensual (compatible con móviles).
    """
    cuota = get_object_or_404(Cuota, pk=cuota_id)
    contrato = cuota.contrato
    
    context = {
        'titulo': f'Recibo Cuota #{cuota.numero_cuota}',
        'subtitulo': f'{contrato.cliente.apellidos} {contrato.cliente.nombres}',
        'pdf_url': request.build_absolute_uri(f'/cuota/{cuota_id}/descargar-recibo/'),
        'url_volver': request.build_absolute_uri(f'/contrato/{contrato.pk}/detalle/'),
    }
    return render(request, 'reportes/pdf_acciones.html', context)

@login_required
def preview_contrato_pdf(request, pk):
    """
    Muestra página de acciones para el contrato PDF (compatible con móviles).
    """
    contrato = get_object_or_404(Contrato, pk=pk)
    
    context = {
        'titulo': 'Contrato de Compraventa',
        'subtitulo': f'{contrato.cliente.apellidos} {contrato.cliente.nombres} - {contrato.lotes_display}',
        'pdf_url': request.build_absolute_uri(f'/contrato/{pk}/descargar-pdf/'),
        'url_volver': request.build_absolute_uri(f'/contrato/{pk}/detalle/'),
    }
    return render(request, 'reportes/pdf_acciones.html', context)

def generar_contrato_word(request, pk):
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

@login_required
def gestion_lotes_view(request):
    # Lista tipo Excel de todos los lotes
    lotes = Lote.objects.all().order_by('manzana', 'numero_lote')
    return render(request, 'gestion/lotes_lista.html', {'lotes': lotes})

@login_required
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
            # Handle image uploads
            if 'imagen' in request.FILES:
                lote.plano = request.FILES['imagen']
            if 'foto_lista' in request.FILES:
                lote.foto_lista = request.FILES['foto_lista']
                
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
            # Handle image uploads
            if 'imagen' in request.FILES:
                lote.plano = request.FILES['imagen']
            if 'foto_lista' in request.FILES:
                lote.foto_lista = request.FILES['foto_lista']
                
            lote.save()
            messages.success(request, f"Lote #{lote.id} actualizado correctamente.")
            return redirect('gestion_lotes')
        except Exception as e:
            messages.error(request, f"Error al actualizar lote: {e}")
    
    return render(request, 'gestion/lotes_form.html', {'lote': lote})

# ==========================================
# REPORTE MENSUAL DE INGRESOS Y MORA
# ==========================================
def _obtener_datos_mensuales(user, mes_str, anio_str):
    from datetime import date
    from dateutil.relativedelta import relativedelta
    from decimal import Decimal
    
    hoy = date.today()
    es_anual = False
    
    mes = hoy.month
    anio = hoy.year
    
    try:
        if anio_str:
            anio = int(anio_str)
            if mes_str == 'anual':
                es_anual = True
                mes = 'anual'
            elif mes_str:
                mes = int(mes_str)
    except ValueError:
        pass
        
    if es_anual:
        primer_dia_mes = date(anio, 1, 1)
        ultimo_dia_mes = date(anio, 12, 31)
    else:
        if not isinstance(mes, int):
            mes = hoy.month
        primer_dia_mes = date(anio, mes, 1)
        ultimo_dia_mes = primer_dia_mes + relativedelta(months=1) - relativedelta(days=1)
    
    if user.is_superuser:
        contratos_todos = Contrato.objects.all().prefetch_related('pago_set__detalles__cuota', 'cliente', 'lote')
        contratos_activos = Contrato.objects.filter(estado='ACTIVO').prefetch_related('pago_set__detalles__cuota', 'cliente', 'lote')
    else:
        contratos_todos = Contrato.objects.filter(cliente__vendedor=user).prefetch_related('pago_set__detalles__cuota', 'cliente', 'lote')
        contratos_activos = Contrato.objects.filter(estado='ACTIVO', cliente__vendedor=user).prefetch_related('pago_set__detalles__cuota', 'cliente', 'lote')
        
    es_mes_pasado = ultimo_dia_mes < hoy

    # 1. Ingresos Cash Flow: TODO pago recibido en el mes (incluyendo abono inicial y de contratos que ahora estén inactivos)
    #    Una fila por TRANSACCION (no por cliente), con fecha y cuotas cubiertas
    cobros_lista_raw = []   # lista de transacciones individuales
    total_cobrado_mes = Decimal('0.00')
    total_entradas    = Decimal('0.00')
    total_ingresos    = Decimal('0.00')

    for contrato in contratos_todos:
        cliente = contrato.cliente
        pago_entrada = contrato.pago_set.order_by('id').first()

        for pago in contrato.pago_set.all().order_by('fecha_pago'):
            # Solo pagos cuya fecha real de cobro cae en el período (Cash Flow)
            if not (primer_dia_mes <= pago.fecha_pago <= ultimo_dia_mes):
                continue

            es_entrada = pago.es_entrada or (pago == pago_entrada and not pago.detalles.exists())

            # Cuotas que cubre este pago (números de cuota)
            if es_entrada:
                cuotas_cubiertas = ['Entrada']
            else:
                numeros = list(
                    pago.detalles.select_related('cuota')
                    .values_list('cuota__numero_cuota', flat=True)
                    .order_by('cuota__numero_cuota')
                )
                cuotas_cubiertas = [f'#{n}' for n in numeros] if numeros else ['—']

            cobros_lista_raw.append({
                'cliente':          cliente,
                'contrato':         contrato,
                'fecha_pago':       pago.fecha_pago,
                'metodo':           pago.metodo_pago,
                'cuotas_cubiertas': ', '.join(cuotas_cubiertas),
                'es_entrada':       es_entrada,
                'monto_cuotas':     Decimal('0.00') if es_entrada else pago.monto,
                'monto_entrada':    pago.monto if es_entrada else Decimal('0.00'),
                'total_cobrado':    pago.monto,
            })

            total_cobrado_mes += pago.monto
            if es_entrada:
                total_entradas += pago.monto
            else:
                total_ingresos += pago.monto

    # Ordenar: por fecha_pago, luego por apellido
    cobros_lista_raw.sort(key=lambda x: (x['fecha_pago'], x['cliente'].apellidos))

    # 2. Proyección Restante (Cuotas venciendo este mes, aún no pagadas totalmente)
    #    SOLO DE CONTRATOS ACTIVOS
    proyeccion_por_cliente = {}
    if not es_mes_pasado:
        for contrato in contratos_activos:
            cuotas_proyeccion = contrato.cuotas.filter(
                fecha_vencimiento__gte=primer_dia_mes,
                fecha_vencimiento__lte=ultimo_dia_mes,
                estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
            )
            deuda = sum(c.saldo_pendiente for c in cuotas_proyeccion)
            if deuda > 0:
                proyeccion_por_cliente[contrato.id] = {
                    'cliente': contrato.cliente,
                    'contrato': contrato,
                    'cuotas_count': cuotas_proyeccion.count(),
                    'deuda_total': deuda
                }
    total_proyeccion = sum(p['deuda_total'] for p in proyeccion_por_cliente.values())

    # 3. Mora Histórica (Cuotas vencidas ANTES de este mes, no pagadas)
    #    SOLO DE CONTRATOS ACTIVOS
    mora_historica = {}
    for contrato in contratos_activos:
        if es_mes_pasado:
            # Si es mes pasado, TODO lo impago vencido hasta el fin de ESE mes se considera mora actual acumulada a la fecha de ese mes.
            cuotas_mora = contrato.cuotas.filter(
                fecha_vencimiento__lte=ultimo_dia_mes,
                estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
            )
        else:
            cuotas_mora = contrato.cuotas.filter(
                fecha_vencimiento__lt=primer_dia_mes,
                estado__in=['PENDIENTE', 'PARCIAL', 'VENCIDO']
            )
        deuda = sum(c.saldo_pendiente for c in cuotas_mora)
        if deuda > 0:
            mora_historica[contrato.id] = {
                'cliente': contrato.cliente,
                'contrato': contrato,
                'cuotas_count': cuotas_mora.count(),
                'deuda_total': deuda
            }
    total_mora_historica = sum(m['deuda_total'] for m in mora_historica.values())

    # 4. Devoluciones del mes (REMOVIDO A PETICION DEL USUARIO)

    return {
        'fecha_inicio': primer_dia_mes,
        'fecha_fin': ultimo_dia_mes,
        'mes_actual': mes,
        'anio_actual': anio,
        'es_anual': es_anual,
        'es_mes_pasado': es_mes_pasado,
        # --- Cobros: una fila por transacción (fecha + cuota + monto) ---
        'cobros_lista': cobros_lista_raw,
        'total_cobrado_mes': total_cobrado_mes,
        'total_entradas': total_entradas,
        'total_ingresos': total_ingresos,
        # --- Para backward-compat ---
        'entradas_lista': [c for c in cobros_lista_raw if c['es_entrada']],
        'ingresos_lista': [c for c in cobros_lista_raw if not c['es_entrada']],
        # --- Proyeccion y mora ---
        'proyeccion_lista': sorted(proyeccion_por_cliente.values(), key=lambda x: x['deuda_total'], reverse=True),
        'total_proyeccion': total_proyeccion,
        'mora_historica_lista': sorted(mora_historica.values(), key=lambda x: x['deuda_total'], reverse=True),
        'total_mora_historica': total_mora_historica,
    }

@login_required
def reporte_mensual_view(request):
    mes = request.GET.get('mes')
    anio = request.GET.get('anio')
    context = _obtener_datos_mensuales(request.user, mes, anio)
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
    total_vtotal = Decimal('0.00')  # Suma de precio_venta_final
    total_entrada = Decimal('0.00')  # Suma de valor_entrada
    total_saldo = Decimal('0.00')  # Suma de saldos pendientes
    
    for contrato in contratos_qs:
        # Actualizar moras para que el saldo pendiente sea exacto al del detalle_cliente
        actualizar_moras_contrato(contrato.id)
        
        # Basic data
        row = {
            'contrato': contrato,
            'cliente': contrato.cliente,
            'lote': contrato.lote,
            'pagos_mensuales': [],
            'total_pagado': Decimal('0.00'),
            'es_devolucion': contrato.estado == 'DEVOLUCION',
            'saldo_pendiente': Decimal('0.00')  # Se calculará después
        }
        
        # Sumar totales de VTotal y Entrada
        total_vtotal += contrato.precio_venta_final or Decimal('0.00')
        total_entrada += contrato.valor_entrada or Decimal('0.00')
        
        # Calculate cuota value for totals
        primera_cuota = contrato.cuotas.first()
        if primera_cuota:
            total_cuotas += primera_cuota.valor_capital
        
        cuotas_en_rango = contrato.cuotas.filter(
            fecha_vencimiento__gte=desde,
            fecha_vencimiento__lte=hasta
        )
        
        # Calcular saldo pendiente y Total Pagado desde las cuotas (fuente de verdad)
        # Total Pagado = V.Entrada + sum(cuota.valor_pagado) → independiente del rango de fechas
        todas_cuotas = list(contrato.cuotas.all())
        row['saldo_pendiente'] = sum(
            c.total_a_pagar - c.valor_pagado
            for c in todas_cuotas
        )
        row['total_pagado'] = contrato.valor_entrada or Decimal('0.00')

        # Inicializar columnas mensuales (se llenarán con pagos del rango seleccionado)
        row['pagos_mensuales'] = [Decimal('0.00')] * len(meses)

        # Ubicar la entrada en el mes correspondiente (COMENTADO: El usuario no quiere ver la entrada en la matriz de meses)
        # Esto evita que aparezca como una "cuota gigante" en el reporte
        """
        pago_entrada_obj = contrato.pago_set.filter(es_entrada=True).order_by('id').first()
        if not pago_entrada_obj:
            pago_entrada_obj = contrato.pago_set.order_by('id').first()
        if pago_entrada_obj:
            fecha_entrada = pago_entrada_obj.fecha_pago
            for i, mes in enumerate(meses):
                mes_inicio = date(mes['year'], mes['month'], 1)
                mes_fin = mes_inicio + relativedelta(months=1) - relativedelta(days=1)
                if mes_inicio <= fecha_entrada <= mes_fin:
                    monto_e = contrato.valor_entrada or Decimal('0.00')
                    row['pagos_mensuales'][i] += monto_e
                    if not row['es_devolucion']:
                        totales_mensuales[i] += monto_e
                    else:
                        totales_mensuales[i] -= monto_e
                    break
        """
        # IDs de pagos de entrada para excluirlos de la lógica "Legacy" (sin detalles)
        ids_entradas = set(contrato.pago_set.filter(es_entrada=True).values_list('id', flat=True))
        if contrato.valor_entrada > 0 and not ids_entradas:
            # Fallback: Solo si hay deuda de entrada pero no hay flag, tomamos el primer pago como entrada
            pago_entrada_obj_fallback = contrato.pago_set.order_by('id').first()
            if pago_entrada_obj_fallback:
                ids_entradas.add(pago_entrada_obj_fallback.id)

        # Procesar cuotas
        for pago in contrato.pago_set.all():
            # Saltar el pago de entrada en ambas ramas: ya fue sumado en la línea de valor_entrada
            if pago.id in ids_entradas:
                continue

            detalles = pago.detalles.select_related('cuota')
            
            if detalles.exists():
                # SIEMPRE procesar detalles (son cuotas/mora reales)
                for detalle in detalles:

                    fecha_pago_real = pago.fecha_pago
                    monto = detalle.monto_aplicado
                    
                    # Distribuir en meses (Cash Flow: usando fecha_pago_real)
                    for i, mes in enumerate(meses):
                        mes_inicio = date(mes['year'], mes['month'], 1)
                        mes_fin = mes_inicio + relativedelta(months=1) - relativedelta(days=1)
                        if mes_inicio <= fecha_pago_real <= mes_fin:
                            row['pagos_mensuales'][i] += monto
                            if not row['es_devolucion']:
                                totales_mensuales[i] += monto
                            else:
                                totales_mensuales[i] -= monto
                            break # Ya lo encontró
                            
                    # Sumar SIEMPRE al histórico de Total Pagado
                    row['total_pagado'] += monto
            else:
                # Pago legacy sin detalles

                fecha_ref = pago.fecha_pago
                monto = pago.monto
                
                # Distribuir en meses
                for i, mes in enumerate(meses):
                    mes_inicio = date(mes['year'], mes['month'], 1)
                    mes_fin = mes_inicio + relativedelta(months=1) - relativedelta(days=1)
                    if mes_inicio <= fecha_ref <= mes_fin:
                        row['pagos_mensuales'][i] += monto
                        if not row['es_devolucion']:
                            totales_mensuales[i] += monto
                        else:
                            totales_mensuales[i] -= monto
                        break
                        
                # Sumar SIEMPRE al histórico de Total Pagado
                row['total_pagado'] += monto
        # Add to general total
        if row['es_devolucion']:
            total_general -= row['total_pagado']
        else:
            total_general += row['total_pagado']
        
        total_saldo += row['saldo_pendiente']
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
        'total_vtotal': total_vtotal,
        'total_entrada': total_entrada,
        'total_saldo': total_saldo,
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
    total_vtotal = Decimal('0.00')
    total_entrada = Decimal('0.00')
    total_saldo = Decimal('0.00')

    
    for contrato in contratos_qs:
        # Actualizar moras para exactitud financiera
        actualizar_moras_contrato(contrato.id)
        
        hasta_fin_de_mes = hasta.replace(day=1) + relativedelta(months=1) - relativedelta(days=1)
        cuotas_en_rango = contrato.cuotas.filter(
            fecha_vencimiento__gte=desde.replace(day=1),
            fecha_vencimiento__lte=hasta_fin_de_mes
        )
        
        total_vtotal += contrato.precio_venta_final or Decimal('0.00')
        total_entrada += contrato.valor_entrada or Decimal('0.00')
        
        row = {

            'contrato': contrato,
            'cliente': contrato.cliente,
            'lote': contrato.lote,
            'pagos_mensuales': [],
            'total_pagado': Decimal('0.00'),
            'es_devolucion': contrato.estado == 'DEVOLUCION',
            # Deuda pendiente TOTAL del contrato (igual que "Deuda Pendiente" en detalle_cliente)
            'saldo_pendiente': sum(
                c.total_a_pagar - c.valor_pagado
                for c in contrato.cuotas.all()
            )
        }
        
        primera_cuota = contrato.cuotas.first()
        if primera_cuota:
            total_cuotas += primera_cuota.valor_capital
        
        # Inicializar los totales de este contrato
        row['pagos_mensuales'] = [Decimal('0.00')] * len(meses)
        # Sumar el valor de entrada siempre, sin importar el rango de fechas
        row['total_pagado'] = contrato.valor_entrada or Decimal('0.00')
        hasta_fin_de_mes_range = hasta.replace(day=1) + relativedelta(months=1) - relativedelta(days=1)

        # Ubicar la entrada en el mes correspondiente (COMENTADO: El usuario no quiere ver la entrada en la matriz de meses)
        """
        pago_entrada_obj = contrato.pago_set.filter(es_entrada=True).order_by('id').first()
        if not pago_entrada_obj:
            pago_entrada_obj = contrato.pago_set.order_by('id').first()
        if pago_entrada_obj:
            fecha_entrada = pago_entrada_obj.fecha_pago
            for i, mes in enumerate(meses):
                mes_inicio = date(mes['year'], mes['month'], 1)
                mes_fin = mes_inicio + relativedelta(months=1) - relativedelta(days=1)
                if mes_inicio <= fecha_entrada <= mes_fin:
                    monto_e = contrato.valor_entrada or Decimal('0.00')
                    row['pagos_mensuales'][i] += monto_e
                    if not row['es_devolucion']:
                        totales_mensuales[i] += monto_e
                    else:
                        totales_mensuales[i] -= monto_e
                    break
        """
        # IDs de pagos de entrada para excluirlos de la lógica "Legacy"
        ids_entradas = set(contrato.pago_set.filter(es_entrada=True).values_list('id', flat=True))
        if contrato.valor_entrada > 0 and not ids_entradas:
            pago_entrada_obj_fallback = contrato.pago_set.order_by('id').first()
            if pago_entrada_obj_fallback:
                ids_entradas.add(pago_entrada_obj_fallback.id)

        # Procesar cuotas
        for pago in contrato.pago_set.all():
            detalles = pago.detalles.select_related('cuota')
            
            if detalles.exists():
                # SIEMPRE procesar detalles
                for detalle in detalles:
                    fecha_pago_real = pago.fecha_pago
                    monto = detalle.monto_aplicado
                    
                    # Distribuir en meses
                    for i, mes in enumerate(meses):
                        mes_inicio = date(mes['year'], mes['month'], 1)
                        mes_fin = mes_inicio + relativedelta(months=1) - relativedelta(days=1)
                        if mes_inicio <= fecha_pago_real <= mes_fin:
                            row['pagos_mensuales'][i] += monto
                            if not row['es_devolucion']:
                                totales_mensuales[i] += monto
                            else:
                                totales_mensuales[i] -= monto
                            break
                            
                    # Sumar SIEMPRE al histórico de Total Pagado
                    row['total_pagado'] += monto
            elif pago.id not in ids_entradas:
                # Pago legacy

                fecha_ref = pago.fecha_pago
                monto = pago.monto
                
                # Distribuir en meses
                for i, mes in enumerate(meses):
                    mes_inicio = date(mes['year'], mes['month'], 1)
                    mes_fin = mes_inicio + relativedelta(months=1) - relativedelta(days=1)
                    if mes_inicio <= fecha_ref <= mes_fin:
                        row['pagos_mensuales'][i] += monto
                        if not row['es_devolucion']:
                            totales_mensuales[i] += monto
                        else:
                            totales_mensuales[i] -= monto
                        break
                        
                # Sumar SIEMPRE al histórico de Total Pagado
                row['total_pagado'] += monto
        
        if row['es_devolucion']:
            total_general -= row['total_pagado']
        else:
            total_general += row['total_pagado']
            
        total_saldo += row['saldo_pendiente']
        
        reporte_data.append(row)
    
    context = {
        'desde': desde,
        'hasta': hasta,
        'meses': meses,
        'reporte_data': reporte_data,
        'totales_mensuales': totales_mensuales,
        'total_general': total_general,
        'total_cuotas': total_cuotas,
        'total_entrada': total_entrada,
        'total_vtotal': total_vtotal,
        'total_saldo': total_saldo,
    }

    
    html_string = render_to_string('reportes/reporte_general_pdf.html', context)
    result_file = BytesIO()
    pisa.CreatePDF(html_string, dest=result_file, link_callback=link_callback)
    
    response = HttpResponse(result_file.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Reporte_General_{desde.strftime("%Y-%m")}_to_{hasta.strftime("%Y-%m")}.pdf"'
    return response

@login_required
def reporte_mensual_pdf_view(request):
    from io import BytesIO
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    from .services import link_callback
    
    mes = request.GET.get('mes')
    anio = request.GET.get('anio')
    context = _obtener_datos_mensuales(request.user, mes, anio)
    
    html_string = render_to_string('reportes/reporte_mensual_pdf.html', context)
    result_file = BytesIO()
    pisa.CreatePDF(html_string, dest=result_file, link_callback=link_callback)
    
    response = HttpResponse(result_file.getvalue(), content_type='application/pdf')
    fecha_str = context['fecha_inicio'].strftime("%Y-%m")
    response['Content-Disposition'] = f'attachment; filename="Reporte_Mensual_{fecha_str}.pdf"'
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


@login_required
def visualizar_contrato_view(request, pk):
    contrato = get_object_or_404(Contrato, pk=pk)
    config = ConfiguracionSistema.objects.first()
    
    context = {
        'contrato': contrato,
        'empresa': config,
        'fecha_actual': date.today(),
    }
    return render(request, 'ventas/visualizar_contrato.html', context)


@login_required
def descargar_contrato_pdf(request, pk):
    from django.template.loader import render_to_string
    from io import BytesIO
    from xhtml2pdf import pisa
    from .services import link_callback
    from django.contrib.staticfiles import finders
    import base64
    
    contrato = get_object_or_404(Contrato, pk=pk)
    
    # 1. Obtener Logo y convertirlo a base64
    logo_base64 = ""
    try:
        abs_path = finders.find('img/logo_bellavista.png')
        if abs_path:
            with open(abs_path, 'rb') as img_file:
                logo_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                # Data URI completo para PNG
                logo_base64 = f"data:image/png;base64,{logo_base64}"
    except Exception as e:
        print(f"Error cargando logo: {e}")

    # Contexto similar a visualizar pero para PDF
    saldo_pendiente = contrato.precio_venta_final - contrato.valor_entrada
    context = {
        'contrato': contrato,
        'fecha_actual': date.today(),
        'logo_url': logo_base64,
        'saldo_pendiente': saldo_pendiente,
        # Datos de empresa si se requieran
    }
    
    html_string = render_to_string('reportes/plantilla_contrato_pdf.html', context)
    result_file = BytesIO()
    
    # Generar PDF
    pisa_status = pisa.CreatePDF(html_string, dest=result_file, link_callback=link_callback)
    
    if pisa_status.err:
        return HttpResponse('Error al generar PDF: ' + str(pisa_status.err), status=500)
        
    response = HttpResponse(result_file.getvalue(), content_type='application/pdf')
    filename = f"Contrato_{contrato.cliente.apellidos}_{contrato.cliente.nombres}.pdf"
    # Cambiamos a 'inline' para que se abra en el navegador y el usuario imprima desde ahí
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response

@login_required
def preview_recibo_transaccion(request, pago_id):
    """
    Vista para previsualizar el recibo de una transacción (con botones de acción).
    """
    from .models import Pago, ConfiguracionSistema
    from datetime import date
    
    config = ConfiguracionSistema.objects.first()
    pago = get_object_or_404(Pago, pk=pago_id)
    contrato = pago.contrato
    
    # Calcular datos para el preview (similares al PDF)
    monto_pagado = pago.monto
    fecha_pago = pago.fecha_pago
    cuotas_cubiertas = [str(d.cuota.numero_cuota) for d in pago.detalles.all().order_by('cuota__numero_cuota')]
    cuotas_str = ", ".join(cuotas_cubiertas) if cuotas_cubiertas else "Abono General"
    
    saldo_pendiente = sum(c.total_a_pagar - c.valor_pagado for c in contrato.cuotas.all())

    # Determinar método de pago y detalles
    metodo_real = 'EFECTIVO'
    datos_bancarios = None
    
    obs = pago.observacion or ""
    if 'TRANSFERENCIA' in obs or pago.metodo_pago == 'TRANSFERENCIA':
        metodo_real = 'TRANSFERENCIA BANCARIA'
    elif 'DEPOSITO' in obs:
        metodo_real = 'DEPÓSITO'
        
    context = {
        'contrato': contrato,
        'cliente': contrato.cliente,
        'pago': pago,
        'empresa': config,
        'monto_pagado': monto_pagado,
        'fecha_pago': fecha_pago,
        'cuotas_str': cuotas_str,
        'saldo_pendiente': saldo_pendiente,
        'metodo_real': metodo_real,
        'fecha_actual': date.today(),
        'pago_id': pago.id, # Para el boton de descargar
    }
    return render(request, 'reportes/preview_recibo_transaccion.html', context)

@login_required
def descargar_recibo_transaccion_pdf(request, pago_id):
    """
    Genera y descarga el PDF del recibo de una transacción.
    """
    from .services import generar_recibo_transaccion_buffer
    from .models import Pago
    
    buffer = generar_recibo_transaccion_buffer(pago_id)
    
    if not buffer:
        messages.error(request, "No se pudo generar el recibo.")
        # Fallback redirect if something goes wrong
        return redirect('dashboard')
        
    response = HttpResponse(buffer, content_type='application/pdf')
    pago = Pago.objects.get(pk=pago_id)
    filename = f"Recibo_Pago_{pago.numero_transaccion}_{pago.contrato.cliente.apellidos}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

# ==========================================
# GESTOR DE GASTOS Y FLUJO DE CAJA
# ==========================================
@login_required
def gestor_gastos_view(request):
    """
    Vista principal del dashboard del gestor de gastos.
    Muestra los KPIs (Saldo, Ingresos, Gastos) e historial.
    """
    # Obtener todos los movimientos ordenados por fecha de registro (más reciente primero)
    movimientos = MovimientoCaja.objects.all().order_by('-fecha_registro', '-id')
    
    # Calcular KPIs
    total_ingresos = sum(m.monto for m in movimientos if m.tipo == 'INGRESO')
    total_gastos = sum(m.monto for m in movimientos if m.tipo == 'GASTO')
    saldo_actual = total_ingresos - total_gastos
    
    context = {
        'movimientos': movimientos,
        'total_ingresos': total_ingresos,
        'total_gastos': total_gastos,
        'saldo_actual': saldo_actual,
        'hoy': date.today()
    }
    return render(request, 'gestion/gestor_gastos.html', context)

@login_required
def registrar_movimiento_view(request):
    """
    Vista que procesa el formulario modalidad POST unicamente
    para registrar un nuevo Ingreso o Gasto en la caja.
    """
    if request.method == 'POST':
        tipo = request.POST.get('tipo', '')
        monto_str = request.POST.get('monto', '0')
        fecha = request.POST.get('fecha', '')
        descripcion = request.POST.get('descripcion', '')
        
        try:
            monto = Decimal(monto_str.replace(',', '.'))
            
            if monto <= 0:
                messages.error(request, "El monto debe ser mayor a 0.")
                return redirect('gestor_gastos')
                
            if tipo not in ['INGRESO', 'GASTO']:
                messages.error(request, "Tipo de movimiento no válido.")
                return redirect('gestor_gastos')
                
            # Validar que si es un GASTO, no deje la caja en negativo (opcional/regla de negocio)
            # if tipo == 'GASTO':
            #    ingresos = sum(m.monto for m in MovimientoCaja.objects.filter(tipo='INGRESO'))
            #    gastos = sum(m.monto for m in MovimientoCaja.objects.filter(tipo='GASTO'))
            #    if (ingresos - gastos) < monto:
            #        messages.error(request, "⚠️ No hay suficientes fondos en caja para este gasto.")
            #        return redirect('gestor_gastos')

            MovimientoCaja.objects.create(
                tipo=tipo,
                monto=monto,
                fecha=fecha,
                descripcion=descripcion,
                registrado_por=request.user
            )
            
            messages.success(request, f"¡{tipo.capitalize()} por ${monto:.2f} registrado con éxito!")
            
        except Exception as e:
            messages.error(request, f"Error al registrar movimiento: {str(e)}")
            
    return redirect('gestor_gastos')
