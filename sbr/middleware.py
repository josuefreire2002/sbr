
class ForceCSPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Obtenemos la respuesta primero (Procesamos la vista)
        response = self.get_response(request)

        # Política de Seguridad de Contenidos (CSP) Permisiva
        csp_policy = (
            "default-src 'self' https: data:; "
            "script-src 'self' https: 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' https: 'unsafe-inline'; "
            "img-src 'self' https: data: blob:; "
            "font-src 'self' https: data:;"
        )
        
        # 2. Ahora sí podemos modificar las cabeceras de la RESPUESTA
        if 'Content-Security-Policy' not in response:
            response['Content-Security-Policy'] = csp_policy
            
        return response
