# Runs on Hugging Face Spaces (Docker SDK), Render or any container host.
FROM python:3.11-slim

RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

ENV PORT=7860
EXPOSE 7860
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
