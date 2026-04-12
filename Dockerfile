FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure the data directory exists at build time;
# in production it is replaced by a bind-mount or named volume.
RUN mkdir -p data

EXPOSE 5000

# Single worker + 4 threads:
#   - SQLite is single-writer; multiple workers would race on the DB.
#   - APScheduler must run in exactly one process.
#   - 4 threads handles concurrent API requests without blocking.
# Timeout 120 s covers the synchronous /api/span/refresh endpoint.
CMD ["gunicorn", "run:app", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
