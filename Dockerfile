FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml server.py kicad_helpers.py ./
RUN pip install --no-cache-dir .
VOLUME /data
CMD ["python", "server.py"]
