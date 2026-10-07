FROM python:3.12-slim

# ffmpeg with libass for burned-in captions, DejaVu as caption font
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY schnittwerk ./schnittwerk
COPY web ./web

ENV SCHNITTWERK_DATA_DIR=/data \
    HF_HOME=/data/.models \
    PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000

CMD ["uvicorn", "schnittwerk.app:app", "--host", "0.0.0.0", "--port", "8000"]
