FROM nvidia/cuda:12.2.2-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg python3 python3-pip libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /worker

COPY cloud_render_worker/requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY cloud_render_worker/worker.py .

ENTRYPOINT ["python3", "worker.py"]
