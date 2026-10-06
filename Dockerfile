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

# Playwright's Chromium and its OS libraries.
#
# Required by `make test-e2e`, which runs the browser suite inside this image.
# Baking the browser in costs image size but means the suite needs no extra step
# and no network at test time -- the previous Hatch-based `make setup` had to
# download it on every fresh machine.
#
# The version is taken from the installed Playwright package rather than pinned
# here, so a `playwright>=` bump in pyproject.toml cannot leave the image with a
# browser the client refuses to drive.
RUN hatch run playwright install --with-deps chromium

# Copiar el resto del código fuente
COPY src/ ./src/
COPY tests/ ./tests/

# Configuration templates the test suite asserts against.
#
# `tests/integration/rentals/test_nextcloud_same_origin.py` reads `.env.example`
# to assert this application ships no Nextcloud credential. Without it here the
# suite fails in the container for a reason that has nothing to do with the code
# under test. This is the *example* file; the real `.env` stays on the host and
# reaches the container through Compose's variable substitution.
COPY .env.example ./.env.example

# Exponer el puerto interno de Flask. This is the container-internal port; the
# published host port is 5001 (see docker-compose.yml). Port 5000 on the host
# belongs to the `caddy` service behind the `proxy` profile.
EXPOSE 5001

# Comando de inicio
CMD ["hatch", "run", "flask", "run", "--host=0.0.0.0", "--port=5001"]
