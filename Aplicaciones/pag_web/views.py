from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
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
    
    # Lotes disponibles para la sección de propiedades
    context['lotes'] = Lote.objects.filter(estado='DISPONIBLE').order_by('-id')[:6]
    
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
    Página de contacto con formulario.
    """
    context = get_context_base()
    
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        email = request.POST.get('email', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        mensaje = request.POST.get('mensaje', '').strip()
        
        if nombre and email and mensaje:
            ContactoMensaje.objects.create(
                nombre=nombre,
                email=email,
                telefono=telefono,
                mensaje=mensaje
            )
            messages.success(request, '¡Mensaje enviado correctamente! Nos pondremos en contacto pronto.')
            return redirect('pag_web:contacto')
        else:
            messages.error(request, 'Por favor complete todos los campos requeridos.')
    
    return render(request, 'pag_web/pages/contacto.html', context)
