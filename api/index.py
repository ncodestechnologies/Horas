import os
import sys

# Garante que o diretório raiz do projeto esteja no sys.path para resolução dos módulos
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app import app

# Handler WSGI exportado para o runtime Serverless da Vercel
app = app
