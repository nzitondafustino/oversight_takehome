# syntax=docker/dockerfile:1.7
# Build with the repository root as the context: docker build -f backend/Dockerfile .  (compose.yaml does this)
# .dockerignore admits only the required build and runtime assets. Credentials are supplied at container startup.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Both packages are installed in one pip command from the repository, read through a bind mount: the core's
# source is neither copied into the image nor into the backend package, and pip never looks for the core on an
# index. The mount is writable only so setuptools can build; what it writes is discarded.
RUN --mount=type=bind,source=.,target=/build,rw \
    pip install "/build[langgraph]" /build/backend

# The core's runtime data: config.yaml, the field catalog, the rule logic the compiler builds candidates with,
# the taxonomy and the policy documents. There is no rules/ruleset.json, so no run can ever fall back to the
# repository's ruleset: every run reads a stored version.
COPY config.yaml /srv/core/config.yaml
COPY rules/fields.yaml /srv/core/rules/fields.yaml
COPY rules/rule_logic.yaml /srv/core/rules/rule_logic.yaml
COPY data/category-taxonomy.md /srv/core/data/category-taxonomy.md
COPY data/policy-docs /srv/core/data/policy-docs

# Recorded model answers, one folder per replay set, and the compiler's recorded model readings of the policy
# documents. Each folder can instead be mounted read-only over its path; nothing is read from a source checkout.
COPY evaluation/final/replay /srv/replay/openrouter
COPY evaluation/jev/runs/first-run/replay /srv/replay/jev
COPY evaluation/langgraph/runs/live-claude-jev-1/replay /srv/replay/langgraph
COPY evaluation/compiler/live /srv/recordings/compiler/live

# The folders volumes mount over exist first, so each volume takes their owner. compiler-recorded is where live
# extractions record their model readings, for every later step to replay.
RUN useradd --uid 10001 --no-create-home api && mkdir -p /var/lib/expense_api/live-cache /var/lib/expense_api/compiler-recorded && chown -R api /var/lib/expense_api
USER api

ENV EXPENSE_API_CORE_CONFIG=/srv/core/config.yaml \
    EXPENSE_API_ARTIFACT_DIR=/var/lib/expense_api/rulesets \
    EXPENSE_API_REPLAY_SETS='{"openrouter": "/srv/replay/openrouter", "jev": "/srv/replay/jev", "langgraph": "/srv/replay/langgraph"}' \
    EXPENSE_API_DEFAULT_REPLAY_SET=openrouter \
    EXPENSE_API_COMPILER_RECORDING_SETS='{"live": "/srv/recordings/compiler/live", "recorded": "/var/lib/expense_api/compiler-recorded"}' \
    EXPENSE_API_COMPILER_LIVE_RECORDING_SET=recorded

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "expense_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
