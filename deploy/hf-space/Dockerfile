# Live planner API for the web dashboard, run as a free Hugging Face Docker Space.
FROM python:3.13-slim

RUN useradd -m -u 1000 user
WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .
USER user
ENV HOME=/home/user PYTHONUNBUFFERED=1 EV_WORK_DIR=/tmp/ev-planner

EXPOSE 7860
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "7860"]
