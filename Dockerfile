FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY main.py ./

RUN pip install --no-cache-dir .

ENV XIAOAI_CONFIG=/app/data/config.json
VOLUME ["/app/data"]
EXPOSE 33003

CMD ["python", "main.py"]
