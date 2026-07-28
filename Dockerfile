# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS web-build
WORKDIR /build
COPY pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN corepack enable && pnpm --dir apps/web install --frozen-lockfile
COPY apps/web apps/web
RUN pnpm --dir apps/web build

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY apps apps
COPY migrations migrations
COPY packages packages
COPY --from=web-build /build/apps/web/dist apps/web/dist
RUN uv sync --frozen --no-dev && \
    useradd --create-home --uid 10001 devpilot && \
    chown -R devpilot:devpilot /app
COPY docker/entrypoint.sh docker/entrypoint.sh
RUN chmod 0555 docker/entrypoint.sh
USER devpilot
EXPOSE 8000
ENTRYPOINT ["./docker/entrypoint.sh"]
CMD ["uv", "run", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
