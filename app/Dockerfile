FROM python:3.12-slim
RUN pip install --no-cache-dir "flask>=3,<4" "prometheus-client>=0.21"
COPY app.py /app.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app.py"]
