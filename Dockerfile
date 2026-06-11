FROM python:3.12-slim

# OpenCV headless still needs these at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch keeps the image a few GB smaller than the CUDA default.
# ultralytics drags in full opencv-python (needs X11); swap it for headless.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu \
 && pip uninstall -y opencv-python \
 && pip install --no-cache-dir --force-reinstall opencv-python-headless

COPY solar_scout/ solar_scout/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Hugging Face cache in a world-readable path so a non-root runtime user can
# read the baked weights; bake them so the first analysis is not slow.
ENV HF_HOME=/opt/hf
RUN python -c "from huggingface_hub import hf_hub_download; \
    hf_hub_download('finloop/yolov8s-seg-solar-panels', 'best.pt')" \
 && chmod -R a+rX /opt/hf

# Run as a non-root user (defence in depth: a container escape lands as an
# unprivileged uid). The volume mountpoints are pre-owned so named volumes
# inherit writable ownership on first mount.
RUN useradd --uid 10001 --create-home appuser \
 && mkdir -p /data /cache \
 && ln -sfn /cache /home/appuser/.cache \
 && chown -R appuser:appuser /data /cache /home/appuser
ENV HOME=/home/appuser
USER appuser

EXPOSE 8080
# PORT is honoured (Cloud Run sets it); demo data seeds itself on first boot
CMD ["/entrypoint.sh"]
