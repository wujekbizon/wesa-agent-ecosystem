FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
# shell_config.json is mounted at runtime — not baked into image

EXPOSE 8002

CMD ["python", "server.py"]
