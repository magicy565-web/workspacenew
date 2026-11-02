FROM python:3.12-slim

ARG INSTALL_MCP=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    STUDIO_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt requirements-mcp.txt /app/
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_MCP" = "1" ]; then pip install --no-cache-dir -r requirements-mcp.txt; fi \
    && groupadd --gid 10001 studio \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app studio \
    && mkdir -p /data \
    && chown studio:studio /data

COPY studio /app/studio
COPY engine /app/engine
COPY examples /app/examples
COPY docs /app/docs
COPY README.md AGENTS.md /app/

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
CMD ["python", "-m", "studio.server", "--host", "0.0.0.0", "--port", "8000"]
