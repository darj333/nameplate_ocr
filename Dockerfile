FROM python:3.11-slim

# System deps for EasyOCR (OpenCV, libGL) and psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1 \
    libgomp1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download EasyOCR English model at build time so container startup is fast
RUN python -c "import easyocr; easyocr.Reader(['en'], gpu=False, download_enabled=True)"

COPY . .

# Railway Volume is mounted at /data; create fallback for local runs
RUN mkdir -p /data/uploads

EXPOSE 8000

# Run Alembic migrations then start the server
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000
