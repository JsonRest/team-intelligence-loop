FROM python:3.11-slim
WORKDIR /app
COPY requirements-frontend.txt .
RUN pip install --no-cache-dir -r requirements-frontend.txt
COPY til_agent/ ./til_agent/
COPY frontend/  ./frontend/
COPY server.py  .
COPY .env*      ./
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
