FROM python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89
WORKDIR /app
COPY relay.py /app/relay.py
RUN chmod 644 /app/relay.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
USER nobody
CMD ["python3", "/app/relay.py"]
