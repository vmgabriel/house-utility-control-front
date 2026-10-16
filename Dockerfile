# Dockerfile
FROM python:3.12-slim

# Evitar escritura de bytecode y buffer en logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instalar dependencias del sistema necesarias para compilar paquetes.
# `curl` is required by the production container healthcheck (see
# docker-compose.prod.yml), which probes /healthz from inside the container.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar Hatch
RUN pip install --upgrade pip hatch

# Contenedor sin privilegios. La aplicación nunca necesita escribir fuera de
# /app y su HOME, y un proceso comprometido no debe ser root dentro del
# contenedor ni poder tocar los bind mounts como tal.
RUN useradd -m -u 10001 appuser

# Hatch stores its virtual environments and caches under HOME (or the XDG data
# dir). Point HOME at the unprivileged user's home now so the environment built
# below lands somewhere `appuser` can read at runtime -- otherwise `hatch run`
# as `appuser` would not find it and would try to rebuild it at container start.
ENV HOME=/home/appuser

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
# browser the client refuses to drive. `--with-deps` needs root for apt, so this
# runs before dropping to `appuser`; the browsers go to $HOME/.cache and are
# chowned below.
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

# Everything under /app and the appuser home was created as root; hand it to the
# unprivileged user so the runtime process can read the Hatch env and write its
# own caches.
RUN chown -R appuser:appuser /app /home/appuser

USER appuser

# Exponer el puerto interno de Flask. This is the container-internal port; the
# published host port is 5001 (see docker-compose.yml). Port 5000 on the host
# belongs to the `caddy` service behind the `proxy` profile.
EXPOSE 5001

# Comando de inicio. Production WSGI server: 4 `gthread` workers x 2 threads run
# the async views under WSGI without the Flask development server.
CMD ["hatch", "run", "gunicorn", "--bind", "0.0.0.0:5001", "--workers", "4", "--threads", "2", "--worker-class", "gthread", "src.interfaces.web.app:create_app()"]
