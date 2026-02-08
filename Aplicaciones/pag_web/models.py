from django.db import models


class Servicio(models.Model):
    """
    Servicios que ofrece la empresa para mostrar en la web pública.
    """
    titulo = models.CharField(max_length=100, verbose_name='Título')
    descripcion = models.TextField(verbose_name='Descripción')
    icono = models.CharField(
        max_length=50, 
        default='bi-house', 
        help_text='Clase de icono Bootstrap (ej: bi-house, bi-geo-alt)',
        verbose_name='Icono'
    )
    orden = models.PositiveIntegerField(default=0, verbose_name='Orden de aparición')
    activo = models.BooleanField(default=True, verbose_name='Activo')
    
    class Meta:
        ordering = ['orden']
        verbose_name = 'Servicio'
        verbose_name_plural = 'Servicios'
    
    def __str__(self):
        return self.titulo


class Testimonio(models.Model):
    """
    Testimonios de clientes satisfechos para mostrar en la web pública.
    """
    nombre_cliente = models.CharField(max_length=100, verbose_name='Nombre del Cliente')
    cargo_ubicacion = models.CharField(
        max_length=100, 
        blank=True, 
        verbose_name='Cargo/Ubicación',
        help_text='Ej: Propietario en Lote #15'
    )
    testimonio = models.TextField(verbose_name='Testimonio')
    foto = models.ImageField(
        upload_to='testimonios/', 
        blank=True, 
        null=True, 
        verbose_name='Foto del Cliente'
    )
    calificacion = models.PositiveIntegerField(
        default=5, 
        choices=[(i, f'{i} estrellas') for i in range(1, 6)],
        verbose_name='Calificación'
    )
    activo = models.BooleanField(default=True, verbose_name='Activo')
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Testimonio'
        verbose_name_plural = 'Testimonios'
    
    def __str__(self):
        return f'{self.nombre_cliente} - {self.calificacion}★'


class ContactoMensaje(models.Model):
    """
    Mensajes recibidos desde el formulario de contacto.
    """
    nombre = models.CharField(max_length=100, verbose_name='Nombre')
    email = models.EmailField(verbose_name='Correo Electrónico')
    telefono = models.CharField(max_length=20, blank=True, verbose_name='Teléfono')
    mensaje = models.TextField(verbose_name='Mensaje')
    fecha_envio = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de Envío')
    leido = models.BooleanField(default=False, verbose_name='Leído')
    
    class Meta:
        ordering = ['-fecha_envio']
        verbose_name = 'Mensaje de Contacto'
        verbose_name_plural = 'Mensajes de Contacto'
    
    def __str__(self):
        return f'{self.nombre} - {self.fecha_envio.strftime("%d/%m/%Y")}'
