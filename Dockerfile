FROM python:3.12-slim

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Tehran

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 araz
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY db ./db
COPY scripts ./scripts

# The default image stays small: faster-whisper (the 'local' extra) is not
# installed here. Switch transcription.backend to 'local' only after
# rebuilding with `pip install .[local]`.
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data/audio && chown -R araz:araz /app
USER araz

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
