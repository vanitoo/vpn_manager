FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./app.py

RUN useradd --system --uid 10001 mtproto-control \
    && mkdir -p /data \
    && chown -R mtproto-control:mtproto-control /app /data

USER mtproto-control
EXPOSE 8080
CMD ["python", "/app/app.py"]
