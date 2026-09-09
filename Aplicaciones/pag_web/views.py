from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse
from .models import Servicio, Testimonio, ContactoMensaje

# Importamos el modelo Lote de sbr_app
from Aplicaciones.sbr_app.models import Lote, ConfiguracionSistema


def get_context_base():
    """
    Contexto base compartido por todas las vistas.
    """
    config = ConfiguracionSistema.objects.first()
    return {
        'config': config,
    }


def index_view(request):
    """
    Landing page principal con todas las secciones.
    """
    context = get_context_base()
    
    # Catálogo exclusivo de lotes en estado DISPONIBLE de sbr_app
    context['lotes'] = Lote.objects.filter(estado='DISPONIBLE').order_by('manzana', 'numero_lote')
    
    # Todos los lotes disponibles para el simulador interactivo
    context['lotes_simulador'] = context['lotes']
    
    # Servicios activos
    context['servicios'] = Servicio.objects.filter(activo=True)
    
    # Testimonios activos
    context['testimonios'] = Testimonio.objects.filter(activo=True)[:6]
    
    return render(request, 'pag_web/index.html', context)


def lotes_view(request):
    """
    Página de todos los lotes disponibles.
    """
    context = get_context_base()
    context['lotes'] = Lote.objects.filter(estado='DISPONIBLE').order_by('numero_lote')
    return render(request, 'pag_web/pages/lotes.html', context)


def lote_detalle_view(request, pk):
    """
    Página de detalle de un lote específico.
    """
    context = get_context_base()
    context['lote'] = get_object_or_404(Lote, pk=pk, estado='DISPONIBLE')
    return render(request, 'pag_web/pages/lote_detalle.html', context)


def servicios_view(request):
    """
    Página de servicios.
    """
    context = get_context_base()
    context['servicios'] = Servicio.objects.filter(activo=True)
    return render(request, 'pag_web/pages/servicios.html', context)


def simulador_view(request):
    """
    Página dedicada exclusivamente al simulador interactivo de financiamiento.
    """
    context = get_context_base()
    context['lotes_simulador'] = Lote.objects.filter(estado='DISPONIBLE').order_by('manzana', 'numero_lote')
    return render(request, 'pag_web/pages/simulador.html', context)


def nosotros_view(request):
    """
    Página de información de la empresa.
    """
    context = get_context_base()
    return render(request, 'pag_web/pages/nosotros.html', context)


def testimonios_view(request):
    """
    Página de todos los testimonios.
    """
    context = get_context_base()
    context['testimonios'] = Testimonio.objects.filter(activo=True)
    return render(request, 'pag_web/pages/testimonios.html', context)


def contacto_view(request):
    """
    Página de contacto con formulario y recepción de leads.
    """
    context = get_context_base()
    # Lotes disponibles para mostrar en la página de contacto
    context['lotes'] = Lote.objects.filter(estado='DISPONIBLE').order_by('manzana', 'numero_lote')[:6]

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        email = request.POST.get('email', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        mensaje = request.POST.get('mensaje', '').strip()
        origen = request.POST.get('origen', 'contacto')
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')

        if nombre and (email or telefono):
            contacto = ContactoMensaje.objects.create(
                nombre=nombre,
                email=email or 'no-indicado@ugshainmobiliarios.com',
                telefono=telefono,
                mensaje=mensaje or 'Interés general en terrenos y lotes.'
            )
            success_msg = f'¡Muchas gracias {nombre}! Su consulta fue registrada exitosamente. Un asesor se comunicará al {telefono or email} a la brevedad posible.'

            if is_ajax:
                return JsonResponse({
                    'status': 'success',
                    'message': success_msg,
                    'lead_id': contacto.id
                })

            messages.success(request, success_msg)
            if origen == 'index':
                # BUG-003 fix: usar reverse() para construir la URL con ancla
                return redirect(reverse('pag_web:index') + '#contact')
            return redirect('pag_web:contacto')
        else:
            error_msg = 'Por favor complete su nombre y al menos un método de contacto (teléfono o correo electrónico).'
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': error_msg}, status=400)

            messages.error(request, error_msg)
            if origen == 'index':
                return redirect(reverse('pag_web:index') + '#contact')
            return redirect('pag_web:contacto')

    return render(request, 'pag_web/pages/contacto.html', context)
