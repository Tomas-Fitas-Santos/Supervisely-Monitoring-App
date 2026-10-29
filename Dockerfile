FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 libglib2.0-0 libgl1 libgomp1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /tmp/monitoring-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/monitoring-requirements.txt
COPY monitoring /app/monitoring
COPY examples/organiser-demo /app/examples/organiser-demo
RUN useradd --uid 10001 --create-home monitor && mkdir -p /app/data && chown monitor:monitor /app/data
USER monitor
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).close()"
CMD ["python", "-m", "monitoring.run", "--production"]
