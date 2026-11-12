FROM debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4 AS base

# Configuration
ENV DEBIAN_FRONTEND=noninteractive
USER root

# Install dependencies
RUN apt update && apt upgrade -y \
    && apt install --no-install-recommends -y \
        ca-certificates \
        bash \
        make \
        curl \
    && apt clean \
    && apt autoremove \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 user \
    && useradd \
        --uid 1000 \
        --gid user \
        --create-home \
        --home-dir /home/user \
        --shell /bin/bash \
        --skel /etc/skel \
        user \
    && passwd -d user \
    && chown -R user:user /home/user

# Switch to non-root user
USER user
WORKDIR /home/user

COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy

# Reset frontend to interactive
ENV DEBIAN_FRONTEND=dialog

