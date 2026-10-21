FROM eclipse-temurin:21-jre-jammy@sha256:affcab5356e1270ac35d2361ee83135af2f03e8d580eca78a6d5d6fdbf048db0
ARG FUSEKI_VERSION=6.2.0
ADD https://dlcdn.apache.org/jena/binaries/apache-jena-fuseki-6.2.0.tar.gz /tmp/fuseki.tar.gz
RUN echo 'ba65f5867d2d4741b2ed9e2af5a0d4fbb447909894ab2a0c6bc4dac8997f4fe339c87b13c48d45d054977769f0f8bf763ea346b1f7792d5cdc458041bd43a132  /tmp/fuseki.tar.gz' | sha512sum -c - \
    && mkdir -p /opt/fuseki /fuseki/databases \
    && tar -xzf /tmp/fuseki.tar.gz --strip-components=1 -C /opt/fuseki \
    && rm /tmp/fuseki.tar.gz \
    && useradd --uid 10001 --home-dir /fuseki fuseki \
    && chown -R fuseki:fuseki /fuseki
COPY database/fuseki/config.ttl /fuseki/config.ttl
ENV FUSEKI_BASE=/fuseki
WORKDIR /fuseki
USER fuseki
EXPOSE 3030
CMD ["java", "-Xmx1g", "-jar", "/opt/fuseki/fuseki-server.jar", "--config=/fuseki/config.ttl", "--update", "--port=3030"]
