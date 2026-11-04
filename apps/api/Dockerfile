FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY apps/api/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
RUN python -c "import soundfile; print(soundfile.__libsndfile_version__)"
COPY apps/api /app/apps/api
RUN pip install --no-deps -e /app/apps/api && useradd -m -u 10001 deepvocal
COPY scripts/configure_storage.py /app/scripts/configure_storage.py
RUN mkdir -p /app/datasets/working /app/datasets/indexes /app/runtime/audio && chown -R deepvocal:deepvocal /app/datasets /app/runtime
USER deepvocal
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
