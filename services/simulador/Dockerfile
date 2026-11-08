# Se construye desde la raíz del repositorio: docker build -f services/simulador/Dockerfile .
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/common
WORKDIR /app/simulador
COPY services/simulador/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/ /app/common/
COPY services/ingesta/main.py /app/ingesta/main.py
COPY services/simulador/ .
RUN useradd --uid 10001 app
USER app
ENTRYPOINT ["python", "main.py"]
