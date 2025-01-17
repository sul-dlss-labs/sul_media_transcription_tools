FROM nvidia/cuda:12.1.0-devel-ubuntu20.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 \
    python3-dev \
    python3-distutils \
    python3-pip \
    ffmpeg

WORKDIR /app

ADD ./whisper_models whisper_models
ADD ./requirements.txt requirements.txt

RUN python3 -m pip install --upgrade pip
RUN python3 -m pip install -r requirements.txt

ADD ./speech_to_text.py speech_to_text.py

ENTRYPOINT ["python3", "speech_to_text.py"]
