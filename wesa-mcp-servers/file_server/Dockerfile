FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV FILE_SERVER_ROOT=/workspace

EXPOSE 8001

CMD ["python", "server.py"]
