# Lambda MicroVMs snapshots this image, so the base registry is fixed by AWS
# (arbitrary base images aren't supported)
FROM public.ecr.aws/lambda/microvms:al2023-minimal

RUN dnf install -y python3 python3-pip shadow-utils && dnf clean all \
  && useradd -u 1001 -m -s /bin/bash sandbox

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY server.py .
RUN mkdir -p /workspace && chown -R sandbox:sandbox /workspace

USER sandbox
EXPOSE 8080 9000

CMD ["python3", "server.py"]
