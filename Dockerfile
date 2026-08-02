# Linux image for the browser path. xvfb lets patchright run *headed* (which is
# much harder to detect than headless) on a machine with no display.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    LPS_BROWSER_HEADLESS=false

RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb curl ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Chrome + its system libraries.
RUN uv run patchright install --with-deps chrome

EXPOSE 8000
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1920x1080x24", \
     "uv", "run", "lps", "serve", "--host", "0.0.0.0", "--port", "8000"]
