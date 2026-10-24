# Stage 1: Build dependencies
FROM python:3.12-slim AS build

# Setting a working directory
WORKDIR /app

# Create a clean Python virtual environment in the /opt/venv directory.
RUN python -m venv /opt/venv

# Update the system PATH so that anytime python or pip is ran, it automatically uses the virtual environment you just created.
ENV PATH="/opt/venv/bin:$PATH"

# Copy local requirements.txt file into the container's /app directory.
COPY requirements.txt .

# Install Python dependencies. The --no-cache-dir flag prevents pip from saving downloaded installation files, saving space.
RUN pip install --no-cache-dir -r requirements.txt


# Stage 2: Runtime
# Use a brand-new, clean Python 3.12 image. Everything from Stage 1 is left behind except what has been copied over.
FROM python:3.12-slim

# Creating a new USER. From here an app belongs to this user
RUN useradd --create-home appuser

# Set /app as the default directory for this new final image. It belongs to root for now
WORKDIR /app

# Copy the fully populated virtual environment (with all installed packages) from the 'build' stage into this final image.
COPY --from=build /opt/venv /opt/venv

# Copy the rest of your application's source code from a local machine into the container's /app directory.
COPY . .

# Setting appuser as the only owner of the WORKDIR
COPY --chown=appuser:appuser . .

# Login as an appuser
USER appuser

# Activate the copied virtual environment in this new stage by updating the PATH.
# Also forces Python to send output directly to the terminal without holding it in a buffer (PYTHONUNBUFFERED=1), ensuring logs appear immediately in Docker.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Execute the script  when the container is started.
#ENTRYPOINT ["python", "app/src/backend/main.py"]

CMD ["ls", "-lha", "/app"]

