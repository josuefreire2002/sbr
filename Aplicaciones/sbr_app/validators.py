
import os
import magic
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

def validar_archivo_seguro(archivo):
    # 1. Validación de Tamaño (Max 5MB)
    limite_mb = 5
    if archivo.size > limite_mb * 1024 * 1024:
        raise ValidationError(f"El archivo pesa demasiado (Máximo {limite_mb}MB)")

    # 2. Validación de Extensión
    ext = os.path.splitext(archivo.name)[1].lower()
    extensiones_validas = ['.pdf', '.jpg', '.jpeg', '.png']
    if ext not in extensiones_validas:
        raise ValidationError(f"Extensión no permitida. Use: {', '.join(extensiones_validas)}")

    # 3. Validación de Magic Bytes (Contenido Real)
    # Leemos un poco del archivo para detectar qué es realmente
    archivo.seek(0)
    mime_type = magic.from_buffer(archivo.read(2048), mime=True)
    archivo.seek(0) # Regresamos el puntero al inicio

    mimes_validos = [
        'application/pdf', 
        'image/jpeg', 
        'image/png'
    ]
    
    if mime_type not in mimes_validos:
        raise ValidationError(f"Archivo corrupto o inseguro. Detectado: {mime_type}")
