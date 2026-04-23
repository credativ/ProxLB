FROM debian:trixie-slim AS BUILD

RUN apt-get update && apt-get install -y python3-venv
RUN python3 -m venv /opt/venv
COPY . /src
RUN /opt/venv/bin/pip3 install /src

FROM debian:trixie-slim

# Labels
LABEL maintainer="credativ GmbH"
LABEL org.label-schema.name="ProxLB"
LABEL org.label-schema.description="ProxLB - An advanced load balancer for Proxmox clusters."
LABEL org.label-schema.vendor="gyptazy"
LABEL org.label-schema.url="https://proxlb.de"
LABEL org.label-schema.vcs-url="https://github.com/credativ/ProxLB"

COPY --from=BUILD /opt/venv /opt/venv

RUN apt-get update && apt-get -y install python3-minimal && apt clean

USER nobody

ENTRYPOINT ["/opt/venv/bin/python", "-m", "proxlb"]
