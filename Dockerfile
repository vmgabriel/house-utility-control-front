# Dockerfile
FROM python:3.12-slim

# Evitar escritura de bytecode y buffer en logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instalar dependencias del sistema necesarias para compilar paquetes
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instalar Hatch
RUN pip install --upgrade pip hatch

# Copiar solo los archivos de definición primero para aprovechar la caché de Docker
COPY pyproject.toml .
COPY README.md .

# Crear el entorno de Hatch e instalar dependencias
RUN hatch env create

# Copiar el resto del código fuente
COPY src/ ./src/
COPY tests/ ./tests/

# Exponer el puerto interno de Flask
EXPOSE 5001

# Comando de inicio
CMD ["hatch", "run", "flask", "run", "--host=0.0.0.0", "--port=5001"]
