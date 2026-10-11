# One image for every process: the web app and the worker run the same code with different commands.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so this layer is cached when only source code changes.
COPY pyproject.toml README.md ./
# (A placeholder package is installed to pull in the dependencies, then removed along with its build
# artifacts so the real install below starts clean. Third-party packages also ship their own test
# suites, tens of MB for scipy alone, which the app never uses, so those are deleted too.)
RUN mkdir -p src/verimind && touch src/verimind/__init__.py \
    && pip install --upgrade pip \
    && pip install . \
    && pip uninstall -y verimind \
    && rm -rf build src ./*.egg-info \
    && find /usr/local/lib/python3.12/site-packages -mindepth 2 -depth -type d -name tests -exec rm -rf {} +

COPY src ./src
RUN pip install --no-deps .

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 verimind \
    && mkdir -p /data/storage && chown -R verimind /data
USER verimind

ENV VERIMIND_STORAGE_DIR=/data/storage
EXPOSE 8000

CMD ["verimind", "serve"]
