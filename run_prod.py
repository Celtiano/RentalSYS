# run_prod.py (en la raíz de tu proyecto, al mismo nivel que run.py)
import os
from dotenv import load_dotenv
from myapp import create_app
import webbrowser
import threading
import time

# Cargar variables de entorno desde .env en el directorio del script
# Esto es importante para que el .exe encuentre el .env
script_dir = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(script_dir, '.env')
if os.path.exists(dotenv_path):
    print(f"Cargando variables de entorno desde: {dotenv_path}")
    load_dotenv(dotenv_path)
else:
    print(f"ADVERTENCIA: Archivo .env no encontrado en {script_dir}")

from myapp import create_app

# Configuración para producción (o la que desees para el ejecutable)
HOST = '127.0.0.1'
PORT = 5000 # Puedes cambiarlo si quieres
# No usar debug=True en un ejecutable distribuido
# El modo debug de Werkzeug no es adecuado para algo que se va a ejecutar fuera de tu entorno de desarrollo.

app = create_app()

def open_browser():
    """Abre el navegador después de un breve retraso."""
    time.sleep(1) # Espera 1 segundo para que el servidor arranque
    webbrowser.open_new(f"http://{HOST}:{PORT}/")

if __name__ == '__main__':
    print(f"Iniciando servidor en http://{HOST}:{PORT}/")
    print("Puedes cerrar esta ventana para detener el servidor.")

    # Abrir el navegador en un hilo separado para no bloquear el servidor
    # threading.Thread(target=open_browser, daemon=True).start()

    # Ejecutar la aplicación Flask
    # Usa un servidor WSGI más robusto si esto fuera para "producción real"
    # Pero para uso local en otro equipo, el servidor de desarrollo de Flask es suficiente.
    from waitress import serve # Alternativa al servidor de desarrollo
    # O puedes seguir usando app.run, pero sin debug
    # app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
    serve(app, host=HOST, port=PORT, threads=4) # Ejemplo con Waitress