FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

COPY app ./app
COPY obras.json .

ENV PORT=8000
EXPOSE 8000

CMD gunicorn -k uvicorn.workers.UvicornWorker -w 1 --timeout 500 --bind 0.0.0.0:${PORT} app.main:app
