FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN adduser --disabled-password --gecos "" appuser

COPY converza_agent ./converza_agent
COPY converza_mcp ./converza_mcp
COPY converza_bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY converza_bot/ .

USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
