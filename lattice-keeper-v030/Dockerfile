# syntax=docker/dockerfile:1.7
# Lattice Keeper — multi-stage build. Copyright (c) 2026 Jay Vallea. All Rights Reserved.
ARG PYTHON_VERSION=3.12
ARG LIBOQS_VERSION=0.16.0

# ---------------------------------------------------------------- builder
FROM python:${PYTHON_VERSION}-slim AS builder
ARG LIBOQS_VERSION
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake ninja-build git ca-certificates libssl-dev \
 && rm -rf /var/lib/apt/lists/*

# liboqs (C library) — pinned release, portable (DIST) build, shared lib only.
RUN git clone --depth 1 --branch "${LIBOQS_VERSION}" https://github.com/open-quantum-safe/liboqs.git /tmp/liboqs \
 && cmake -S /tmp/liboqs -B /tmp/liboqs/build -GNinja \
      -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON -DOQS_BUILD_ONLY_LIB=ON \
      -DOQS_DIST_BUILD=ON -DCMAKE_INSTALL_PREFIX=/opt/oqs \
 && cmake --build /tmp/liboqs/build --parallel \
 && cmake --install /tmp/liboqs/build \
 && rm -rf /tmp/liboqs

# Declared after the liboqs layer so changing EXTRAS doesn't rebuild liboqs.
ARG EXTRAS=pqc
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[${EXTRAS}]"
# Fail the build if liboqs-python cannot load the library we just built.
RUN OQS_INSTALL_PATH=/opt/oqs python -c "import oqs; assert 'ML-DSA-65' in oqs.get_enabled_sig_mechanisms(); assert 'ML-KEM-768' in oqs.get_enabled_kem_mechanisms(); print('liboqs', oqs.oqs_version())"

# ---------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime
LABEL org.opencontainers.image.title="lattice-keeper" \
      org.opencontainers.image.version="0.30.0" \
      org.opencontainers.image.licenses="LicenseRef-All-Rights-Reserved"
RUN groupadd --system --gid 10001 lattice \
 && useradd --system --uid 10001 --gid lattice --home-dir /data --shell /usr/sbin/nologin lattice \
 && mkdir -p /data/keys /data/rag && chown -R lattice:lattice /data && chmod 700 /data/keys
COPY --from=builder /opt/oqs/lib /opt/oqs/lib
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    OQS_INSTALL_PATH=/opt/oqs \
    LD_LIBRARY_PATH=/opt/oqs/lib \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    KEYS_DIR=/data/keys API_HOST=0.0.0.0 API_PORT=8765
USER 10001:10001
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).status == 200 else 1)"
STOPSIGNAL SIGTERM
ENTRYPOINT ["lattice-keeper"]
CMD ["serve"]
