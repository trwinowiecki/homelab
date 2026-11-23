FROM timberio/vector:0.50.0-debian@sha256:8e81f992197125f736e1fe5d73117ca6b69a0bb69cf3633f82b9233c9769c9c1
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# vector.yaml and health_poll.py are bind-mounted at runtime from this repo.
CMD ["--config", "/etc/vector/vector.yaml"]
