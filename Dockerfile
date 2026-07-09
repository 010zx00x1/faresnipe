FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY src/ ./src/
COPY config/origins.example.toml ./config/origins.example.toml

RUN pip install --no-cache-dir .

ENV FARESNIPE_DATABASE=/app/data/faresnipe.sqlite3

RUN useradd -m faresnipe && chown -R faresnipe:faresnipe /app
USER faresnipe

EXPOSE 8765

ENTRYPOINT ["faresnipe"]
# First use needs `faresnipe init` and `faresnipe run` to populate data.
CMD ["serve", "--host", "0.0.0.0", "--port", "8765"]
