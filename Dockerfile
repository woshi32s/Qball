FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8600

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY server.py sw.js manifest.webmanifest ./
COPY agora-demo.html showcase.html ./
COPY js ./js
COPY fonts ./fonts
COPY icons ./icons

EXPOSE 8600

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8600/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "server.py", "--no-browser"]
