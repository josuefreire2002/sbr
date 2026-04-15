from django.db import models
from django.contrib.auth.models import User
from Aplicaciones.sbr_app.validators import validar_archivo_seguro
import bleach

class CategoriaTransaccion(models.Model):
    TIPO_CHOICES = [
        ('INGRESO', 'Ingreso'),
        ('GASTO', 'Gasto'),
    ]
    nombre = models.CharField(max_length=100)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    
    def __str__(self):
        return f"{self.nombre} ({self.tipo})"

class Transaccion(models.Model):
    TIPO_CHOICES = [
        ('INGRESO', 'Ingreso'),
        ('GASTO', 'Gasto'),
    ]
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    categoria = models.ForeignKey(CategoriaTransaccion, on_delete=models.SET_NULL, null=True, blank=True)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descripcion = models.TextField()
    fecha = models.DateField()
    numero_recibo = models.CharField(max_length=50, blank=True, null=True, help_text="Número de recibo (Opcional)")
    foto_recibo = models.ImageField(upload_to='gestor/recibos/', blank=True, null=True, validators=[validar_archivo_seguro], help_text="Foto del recibo (Opcional)")
    
    registrado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.descripcion:
            self.descripcion = bleach.clean(self.descripcion, tags=[], attributes={}, strip=True)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tipo} - ${self.valor} ({self.fecha})"
