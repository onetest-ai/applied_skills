# Brain MCP server image — provider-neutral.
#
# Works for BOTH local runs (`docker build` / `docker run`) and remote deploy
# (the deploy adapters under `deploy/` call `az acr build` etc. with this file).
#
# Build context layout (assemble this before building; see deploy/README.md).
# Everything project-specific is staged into these fixed paths so this Dockerfile
# stays generic — Docker forbids build ARGs in COPY *source* paths, so the
# convention is the layout below, not per-path variables:
#
#   <context>/
#     server/                    # fastmcp_server.py, semantic_core.py, requirements.txt
#                                #   (vendored from the plugin's mcp/brain/)
#     data/knowledge.sqlite      # the built, immutable knowledge store
#     schema/metrics.json        # governed metric catalog (BRAIN_CATALOG)
#     assets/                    # page images / table evidence (optional; keep dir even if empty)
#     skills/knowledge-index/            # BRAIN_SKILLS lane
#     skills/corpus-taxonomy-extraction/ # BRAIN_SKILLS lane
#
# Local quickstart:
#   docker build -f mcp/brain/Dockerfile -t brain-mcp .
#   docker run --rm -p 8000:8000 -e BRAIN_API_KEY=dev-key brain-mcp
#   curl localhost:8000/healthz

ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}

# Parameterizable at build time (these ARE allowed by Docker).
ARG APP_UID=10001
ARG EMBED_MODEL=BAAI/bge-small-en-v1.5

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    BRAIN_MCP_TRANSPORT=http \
    BRAIN_MCP_PATH=/mcp \
    BRAIN_DB=/app/data/knowledge.sqlite \
    BRAIN_CATALOG=/app/schema/metrics.json \
    BRAIN_SKILLS=/app/skills \
    BRAIN_ASSETS=/app/assets \
    FASTEMBED_CACHE_PATH=/app/models

WORKDIR /app

COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY server/semantic_core.py server/fastmcp_server.py /app/
COPY skills/knowledge-index /app/skills/knowledge-index
COPY skills/corpus-taxonomy-extraction /app/skills/corpus-taxonomy-extraction
COPY schema/metrics.json /app/schema/metrics.json
COPY data/knowledge.sqlite /app/data/knowledge.sqlite
COPY assets /app/assets

# Warm the query embedding model into the image so production startup needs no
# outbound network access.
RUN python -c "from fastembed import TextEmbedding; next(TextEmbedding('${EMBED_MODEL}', cache_dir='/app/models').embed(['container readiness']))"

# Non-root, read-only store.
RUN useradd --create-home --uid ${APP_UID} appuser \
    && chown -R appuser:appuser /app \
    && chmod 0444 /app/data/knowledge.sqlite
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["python", "fastmcp_server.py"]
