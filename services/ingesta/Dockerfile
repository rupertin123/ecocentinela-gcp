# Se construye desde la raíz del repositorio: docker build -f services/ingesta/Dockerfile .
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/common PORT=8080
WORKDIR /app
COPY services/ingesta/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/ common/
COPY services/ingesta/ .
RUN useradd --uid 10001 app
USER app
CMD exec uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'
