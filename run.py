# run.py

from myapp import create_app

app = create_app()

if __name__ == '__main__':
    # Puedes cambiar el puerto o quitar debug=True en producción
    app.run(debug=True, port=5000)
