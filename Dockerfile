FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

# Keep the archive (including security updates) fixed along with the base image.
# To update dependencies, change this date and the base image digest together.
RUN printf '%s\n' \
    'Types: deb' \
    'URIs: http://archive.ubuntu.com/ubuntu' \
    'Suites: noble noble-updates' \
    'Components: main universe' \
    'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
    'Snapshot: 20261001T000000Z' \
    '' \
    'Types: deb' \
    'URIs: http://security.ubuntu.com/ubuntu' \
    'Suites: noble-security' \
    'Components: main universe' \
    'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \
    'Snapshot: 20261001T000000Z' \
    > /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git \
        make \
        python3 \
        python3-gi \
        gir1.2-gtk-4.0 \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
COPY . .

CMD ["sh", "-c", "make test && make test-ui"]
