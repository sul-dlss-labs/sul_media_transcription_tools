FROM ubuntu:22.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3-distutils \
    python3-pip \
    ffmpeg

WORKDIR /app

ADD ./whisper_models whisper_models
ADD ./requirements.txt requirements.txt

RUN python3.11 -m pip install --upgrade pip
RUN python3.11 -m pip install -r requirements.txt

ADD ./speech_to_text.py speech_to_text.py
ADD ./error_reporting_wrapper.py error_reporting_wrapper.py

ENTRYPOINT ["python3.11", "error_reporting_wrapper.py", "python3.11", "speech_to_text.py"]
