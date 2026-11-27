# Build context is the repo root so the shared contract can be copied in.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/processor

COPY processor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY docs/contracts /app/docs/contracts
COPY processor/*.py processor/scoring.json ./

RUN useradd --system app
USER app
CMD ["python", "worker.py"]
