import os
import sys

# Garante que o diretório raiz do projeto esteja no sys.path para resolução dos módulos
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app import app

class VercelPathMiddleware:
    """
    Middleware WSGI para garantir compatibilidade total com o roteamento da Vercel.
    Trata cenários onde a Vercel passa o caminho interno do handler (/api, /api/index, /api/index.py)
    ou quando envia headers de proxy (x-forwarded-uri, x-original-uri, x-matched-path).
    """
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')

        # Se há header de rota original encaminhada pela CDN/Vercel
        forwarded_uri = (
            environ.get('HTTP_X_FORWARDED_URI') or
            environ.get('HTTP_X_ORIGINAL_URI') or
            environ.get('HTTP_X_REWRITE_URL')
        )

        if forwarded_uri:
            clean_path = forwarded_uri.split('?')[0]
            if clean_path:
                environ['PATH_INFO'] = clean_path
        elif path.startswith('/api/index.py'):
            stripped = path[len('/api/index.py'):]
            environ['PATH_INFO'] = stripped if stripped else '/'
        elif path.startswith('/api/index'):
            stripped = path[len('/api/index'):]
            environ['PATH_INFO'] = stripped if stripped else '/'
        elif path == '/api' or path == '/api/':
            environ['PATH_INFO'] = '/'

        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelPathMiddleware(app.wsgi_app)
