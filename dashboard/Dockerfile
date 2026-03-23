FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e .
COPY . .
EXPOSE 3000
CMD ["uvicorn","api.main:app","--host","0.0.0.0","--port","3000"]
