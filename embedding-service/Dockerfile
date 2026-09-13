FROM python:3.12-slim AS model-builder

RUN pip install --no-cache-dir huggingface_hub==0.36.0 PyYAML==6.0.2
WORKDIR /build
COPY intel.yaml /build/intel.yaml
COPY embedding-service/prepare_model.py /build/prepare_model.py
RUN python /build/prepare_model.py

FROM ghcr.io/ggml-org/llama.cpp:server@sha256:ce6b9639bea61c87e641d0f5622229c23400fb84dc4d91075240e8983d7c3b47

COPY --from=model-builder /build/model.gguf /models/model.gguf
COPY --from=model-builder /build/model.env /models/model.env
COPY embedding-service/start.sh /usr/local/bin/start-embedding-server
RUN chmod +x /usr/local/bin/start-embedding-server

ENV PORT=10000
EXPOSE 10000

ENTRYPOINT ["/usr/local/bin/start-embedding-server"]
