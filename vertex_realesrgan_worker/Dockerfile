FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    wget \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /worker

COPY vertex_realesrgan_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY vertex_realesrgan_worker/worker.py .

ENTRYPOINT ["python", "worker.py"]
