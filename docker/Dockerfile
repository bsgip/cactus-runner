FROM python:3.12-slim

RUN apt-get update; apt-get install -y git

# Copy app + dependencies
COPY ./src /app/src
COPY ./config /app/config
COPY ./pyproject.toml /app/pyproject.toml
COPY ./setup.cfg /app/setup.cfg

# Configure environment
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create logs dir
RUN mkdir /app/logs

# Use a personal access token to access private repo
RUN --mount=type=secret,id=github_pat,uid=50000 git config --global url."https://ssh:$(cat /run/secrets/github_pat)@github.com/".insteadOf "ssh://git@github.com/"

# Install app / dependencies
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e /app

# Defaults
WORKDIR /app
ENTRYPOINT ["python"]
CMD ["src/cactus_runner/app/main.py"]
