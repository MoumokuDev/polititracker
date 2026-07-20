FROM python:3.12-slim

WORKDIR /app

# dependencies in their own layer: cached until pyproject.toml changes, so
# source-only rebuilds don't reinstall torch and friends
COPY pyproject.toml README.md ./
RUN python -c 'import tomllib; print("\n".join(tomllib.load(open("pyproject.toml","rb"))["project"]["dependencies"]))' > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY src ./src
RUN pip install --no-cache-dir --no-deps .

COPY alembic.ini ./
COPY alembic ./alembic
COPY seeds ./seeds

EXPOSE 8000
CMD ["uvicorn", "polititracker.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
