FROM python:3.12-slim

# Offline voice for pronunciation audio. Other options: en_US-amy-medium,
# en_US-ryan-medium, en_GB-alba-medium (British). Build with
#   --build-arg WITH_TTS=0   to skip it (the app then uses the browser voice).
ARG WITH_TTS=1
ARG PIPER_VOICE=en_US-lessac-medium

WORKDIR /app
RUN if [ "$WITH_TTS" = "1" ]; then \
      pip install --no-cache-dir piper-tts && \
      python -m piper.download_voices --download-dir /voices "$PIPER_VOICE"; \
    fi

COPY server.py ./
COPY static ./static

ENV PORT=8080 \
    DB_PATH=/data/phrasedeck.db \
    PIPER_MODEL=/voices/${PIPER_VOICE}.onnx \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8080
CMD ["python", "server.py"]
