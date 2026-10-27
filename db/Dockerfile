FROM ubuntu:latest
LABEL authors="kirill"



RUN useradd --create-home dbuser

WORKDIR /app

COPY --chown=appuser:appuser . .

USER dbuser

ENTRYPOINT ["top", "-b"]