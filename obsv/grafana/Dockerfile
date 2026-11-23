FROM grafana/grafana:13.1.3@sha256:ab5cb380e3ff3172d6c8bd2e7cfd31cce977d2881b260e1f5bc089bf0b759b43
USER root
ENV GF_PATHS_PLUGINS=/opt/grafana-plugins
RUN mkdir -p /opt/grafana-plugins \
    && (grafana cli --pluginsDir /opt/grafana-plugins plugins install victoriametrics-logs-datasource \
        || grafana-cli --pluginsDir /opt/grafana-plugins plugins install victoriametrics-logs-datasource) \
    && chown -R 472:472 /opt/grafana-plugins
USER grafana
