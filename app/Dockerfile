# Stage 1: Build dependencies (-dev variant has a shell and pip)
FROM dhi.io/python:3.12-dev AS build

WORKDIR /app

COPY requirements.txt .

# Install dependencies into a plain directory instead of a venv
RUN pip install --no-cache-dir --target /app/deps -r requirements.txt


# Stage 2: Runtime (minimal, nonroot, no shell)
FROM dhi.io/python:3.12

WORKDIR /app

# Copy installed packages from the build stage
COPY --from=build /app/deps /app/deps

# Copy application source
COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/deps:/app

# Launcher runs database.py, then main.py
# nosemgrep dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["python", "start.py"]
