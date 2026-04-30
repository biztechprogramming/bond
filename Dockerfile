FROM node:22-bookworm-slim AS node-base

FROM python:3.12-slim AS backend-base

WORKDIR /app

# Install git and copy Node.js 22 + npm from the official Node image for the gateway
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
COPY --from=node-base /usr/local/bin /usr/local/bin
COPY --from=node-base /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node-base /usr/local/include /usr/local/include
COPY --from=node-base /usr/local/share /usr/local/share
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    npm install -g pnpm

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Python dependencies
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

# Gateway dependencies
RUN mkdir -p /app/gateway
COPY gateway/package.json /app/gateway/package.json
RUN cd /app/gateway && pnpm install --frozen-lockfile 2>/dev/null || cd /app/gateway && pnpm install

# Frontend dependencies and build
RUN mkdir -p /app/frontend
COPY frontend/package.json /app/frontend/package.json
RUN cd /app/frontend && pnpm install --frozen-lockfile 2>/dev/null || cd /app/frontend && pnpm install

# Copy source
COPY . .

# Build frontend
ARG BOND_SPACETIMEDB_URL=http://172.17.0.1:18787
ENV BOND_SPACETIMEDB_URL=${BOND_SPACETIMEDB_URL}
RUN cd frontend && pnpm build

# Create bond home
RUN mkdir -p /home/bond/.bond/data /home/bond/.bond/logs /home/bond/.bond/cache /home/bond/.bond/workspace

ENV BOND_HOME=/home/bond/.bond
ENV PYTHONPATH=/app

EXPOSE 18788 18789 18790

# Simple process manager: first-run credential display, then start services
CMD ["sh", "-c", "\
  bash scripts/first-run.sh && \
  uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 18790 & \
  cd gateway && NODE_OPTIONS='--experimental-global-webcrypto' pnpm exec tsx src/index.ts & \
  cd frontend && pnpm start & \
  wait"]
