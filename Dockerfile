# Imagen única: web (FastAPI) + scrapper (se ejecuta como subproceso desde el botón de la pantalla Estado).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    INMO_SRC=/srv/scrapper_src INMO_DB=/data/inmo.sqlite INMO_CONFIG=/config/profiles.yaml \
    INMO_CONFIG_EXAMPLE=/srv/defaults/profiles.example.yaml
WORKDIR /srv
COPY web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scrapper/src/inmo /srv/scrapper_src/inmo
COPY web/app /srv/app
COPY scrapper/config/profiles.example.yaml /srv/defaults/profiles.example.yaml
RUN useradd -u 1000 -m app && mkdir -p /data /config && chown app /data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
