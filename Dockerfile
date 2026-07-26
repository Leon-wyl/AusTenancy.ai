# ── Builder: install deps, pre-warm FastEmbed models ──────────────────────
FROM public.ecr.aws/lambda/python:3.12 AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt ${LAMBDA_TASK_ROOT}/

RUN pip install --no-cache-dir --target "${LAMBDA_TASK_ROOT}" -r requirements.txt

# Pre-warm FastEmbed models to read-only cache (same path used at runtime)
ENV FASTEMBED_CACHE_PATH=/var/task/assets/fastembed_cache
RUN python -c "\
from fastembed import TextEmbedding, SparseTextEmbedding; \
TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/var/task/assets/fastembed_cache'); \
SparseTextEmbedding(model_name='Qdrant/bm25', cache_dir='/var/task/assets/fastembed_cache'); \
print('FastEmbed models cached successfully')"

# ── Runtime: copy deps, cache, code, Qdrant seed, manifest ────────────────
FROM public.ecr.aws/lambda/python:3.12 AS runtime

# Pre-warmed FastEmbed cache (read-only under /var/task/)
COPY --from=builder /var/task/assets/fastembed_cache /var/task/assets/fastembed_cache

# Installed packages
COPY --from=builder ${LAMBDA_TASK_ROOT}/ ${LAMBDA_TASK_ROOT}/

# Application code
COPY src/ ${LAMBDA_TASK_ROOT}/src/

# Qdrant seed at repo root → image assets/ (build fails if either missing)
COPY qdrant_storage/ ${LAMBDA_TASK_ROOT}/assets/qdrant_storage/
COPY assets/qdrant_index_manifest.json ${LAMBDA_TASK_ROOT}/assets/qdrant_index_manifest.json

# Runtime environment: Bedrock-only, offline FastEmbed
ENV LLM_PROVIDER=bedrock
ENV BEDROCK_TEMPERATURE=0
ENV FASTEMBED_CACHE_PATH=/var/task/assets/fastembed_cache
ENV HF_HUB_OFFLINE=1
ENV PYTHONPATH=/var/task
ENV PYTHONUNBUFFERED=1

CMD ["src.api.handler.handler"]

# ── PoC target: includes container verification scripts ───────────────────
FROM runtime AS poc
COPY tests/container_checks/ ${LAMBDA_TASK_ROOT}/tests/container_checks/
