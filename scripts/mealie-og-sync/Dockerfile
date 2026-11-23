FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

WORKDIR /app

RUN pip install --no-cache-dir ourgroceries aiohttp

COPY sync_mealie_ourgroceries.py \
     mealie_og_sync_server.py \
     og_resolver.py \
     minyaml.py \
     /app/scripts/

ENV MEALIE_OG_SYNC_HOST=0.0.0.0 \
    MEALIE_OG_SYNC_PORT=9926 \
    MEALIE_OG_SYNC_CONFIG=/config/mealie-og-sync.yaml \
    PYTHONUNBUFFERED=1

EXPOSE 9926

CMD ["python3", "/app/scripts/mealie_og_sync_server.py"]
