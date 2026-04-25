FROM python:3.12-slim AS backend-base

WORKDIR /app

# Install Node.js 22 for the gateway
RUN apt-get update && apt-get install -y curl git && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g pnpm && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

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
  cd gateway && pnpm start & \
  cd frontend && pnpm start & \
  wait"]
