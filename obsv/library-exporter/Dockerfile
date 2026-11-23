FROM python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89
WORKDIR /app
RUN apk add --no-cache tzdata \
    && pip install --no-cache-dir pyyaml
COPY exporter.py /app/exporter.py
RUN chmod 644 /app/exporter.py
ENV PYTHONUNBUFFERED=1
EXPOSE 9103
CMD ["python3", "/app/exporter.py"]
