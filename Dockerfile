##################
# NOTE: This dockerfile is designed for quick builds to test locally or on the test machines, it is not the one used for prod deployments (see cactus-deploy repo)
##################

FROM python:3.12-slim
WORKDIR /app/

RUN apt-get update && \
    apt-get install --no-install-recommends -y git postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# Setup the git config for private repos
RUN --mount=type=secret,id=github_pat,uid=50000 git config --global url."https://ssh:$(cat /run/secrets/github_pat)@github.com/".insteadOf "ssh://git@github.com/"
RUN --mount=type=secret,id=github_pat,uid=50000 git config --global url."https://git:$(cat /run/secrets/github_pat)@github.com/".insteadOf "git@github.com:"

# Python conf
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_HOST='0.0.0.0'
ENV APP_PORT='8080'

# Copy src
COPY . /app

# Install deps
RUN --mount=type=secret,id=github_pat,uid=50000 pip install --no-cache-dir -e /app gunicorn

# Setup directories
RUN mkdir -p /shared

WORKDIR /app

# Entrypoint
CMD ["sh", "-c", "exec gunicorn cactus_runner.app.main:app --bind ${APP_HOST}:${APP_PORT} --worker-class aiohttp.GunicornWebWorker"]