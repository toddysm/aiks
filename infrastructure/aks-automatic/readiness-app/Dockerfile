FROM python:3.12-alpine@sha256:4c47124a8391cb7a9f571164147d154777cf012a4ece5f86097130d7a4478111
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY . .
RUN pip install --no-cache-dir '.[readiness]' && \
    addgroup -g 10001 -S aiks && \
    adduser -u 10001 -S -D -H -G aiks aiks
USER 10001:10001
ENTRYPOINT ["uvicorn"]
CMD ["aiks.readiness:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]