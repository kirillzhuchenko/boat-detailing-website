FROM python:3.12-slim



RUN useradd --create-home dbuser

WORKDIR /app

COPY --chown=dbuser:dbuser . .

USER dbuser

ENTRYPOINT ["python", "database.py"]