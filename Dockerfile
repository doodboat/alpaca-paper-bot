FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 bot && mkdir /state && chown bot:bot /state
COPY paperbot /app/paperbot
USER bot
ENV PYTHONUNBUFFERED=1 PAPERBOT_STATE_DIR=/state
ENTRYPOINT ["python", "-m", "paperbot"]
CMD ["run"]
