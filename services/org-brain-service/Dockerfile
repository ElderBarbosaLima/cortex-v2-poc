# org-brain-service (Cortex). Same shape as the monorepo's Dockerfile:
# shared editable libs land at /libraries/<name>, so the Pipfile's
# ../../libraries/<name> (relative to /app/Pipfile) resolves inside the image.
# Two targets: `runtime` (what `make dev` runs) and `dev` (tests, lint, typecheck).

FROM python:3.12.16-slim-trixie AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WORKON_HOME=/opt/venvs \
    PIPENV_NOSPIN=1

WORKDIR /app
RUN pip install pipenv==2026.10.6

COPY libraries/arbitr-test-utils /libraries/arbitr-test-utils
COPY services/org-brain-service/Pipfile services/org-brain-service/Pipfile.lock /app/

FROM base AS dev
RUN pipenv install --deploy --dev
COPY services/org-brain-service/ /app/

FROM base AS runtime
RUN pipenv install --deploy \
    && useradd --system --uid 10001 --no-create-home app
COPY services/org-brain-service/ /app/
USER app
EXPOSE 5008
CMD ["pipenv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5008"]
