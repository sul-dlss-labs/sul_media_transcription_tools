import os
import boto3
import json
import re
import uuid
from pathlib import Path

import moto
import pytest
import speech_to_text
from unittest.mock import patch

BUCKET = "bucket"
DONE_QUEUE = "done"


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["SPEECH_TO_TEXT_DONE_SQS_QUEUE"] = DONE_QUEUE
    os.environ["SPEECH_TO_TEXT_S3_BUCKET"] = BUCKET


@pytest.fixture
def honeybadger_env():
    """Mocked Honeybadger key."""
    os.environ["HONEYBADGER_API_KEY"] = "abc123"


@pytest.fixture
def sts(aws_credentials):
    with moto.mock_aws():
        yield boto3.client("sts")


@pytest.fixture
def s3(aws_credentials):
    with moto.mock_aws():
        yield boto3.client("s3")


@pytest.fixture
def sqs(aws_credentials):
    with moto.mock_aws():
        yield boto3.resource("sqs")


@pytest.fixture
def bucket(s3):
    return s3.create_bucket(Bucket=BUCKET)


@pytest.fixture
def queues(sqs):
    sqs.create_queue(QueueName=DONE_QUEUE)


# ignore utcnow warning until https://github.com/boto/boto3/issues/3889 is resolved
@pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow")
def test_happy_path(bucket, queues):
    job_id = str(uuid.uuid4())

    speech_to_text.add_media("tests/data/en.wav", job_id)

    job = {
        "id": job_id,
        "media": [{"name": f"{job_id}/en.wav"}],
        "options": {"model": "tiny", "writer": {"max_line_width": 42}},
    }

    # run the job
    speech_to_text.main(job=job)

    # is the whisper output in s3?
    test_bucket = speech_to_text.get_bucket()
    vtt_file = test_bucket.Object(f"{job_id}/output/en.vtt").get()
    assert vtt_file, "vtt file created"
    assert "This is a test" in vtt_file["Body"].read().decode("utf-8"), "vtt content"

    # is the job file in the bucket and does it list the output files?
    job_file = test_bucket.Object(f"{job_id}/job.json").get()
    assert job_file, "job file created"
    job = json.loads(job_file["Body"].read().decode("utf-8"))
    assert len(job["output"]) == 5
    assert f"{job_id}/output/en.vtt" in job["output"]
    assert f"{job_id}/output/en.srt" in job["output"]
    assert f"{job_id}/output/en.txt" in job["output"]
    assert f"{job_id}/output/en.tsv" in job["output"]
    assert f"{job_id}/output/en.json" in job["output"]

    # did the max_line_width option take effect?
    assert job["log"]["runs"][0]["media"] == f"{job_id}/en.wav"
    assert job["log"]["runs"][0]["transcribe"]["word_timestamps"] is True
    assert job["log"]["runs"][0]["write"]["max_line_width"] == 42

    # is there a message in the "done" queue?
    queue = speech_to_text.get_done_queue()
    msgs = queue.receive_messages(MaxNumberOfMessages=1, WaitTimeSeconds=10)
    assert len(msgs) == 1, "found a done message"
    assert msgs[0].delete(), "delete the message from the queue"

    # does the sqs job look ok?
    job = json.loads(msgs[0].body)
    assert job["id"] == job_id
    assert job["finished"]
    assert len(job["output"]) == 5
    assert f"{job_id}/output/en.vtt" in job["output"]
    assert f"{job_id}/output/en.srt" in job["output"]
    assert f"{job_id}/output/en.txt" in job["output"]
    assert f"{job_id}/output/en.tsv" in job["output"]
    assert f"{job_id}/output/en.json" in job["output"]

    # is the "done" queue now empty
    jobs = queue.receive_messages(MaxNumberOfMessages=1)
    assert len(jobs) == 0, "queue empty"


# ignore utcnow warning until https://github.com/boto/boto3/issues/3889 is resolved
@pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow")
def test_file_options(bucket, queues):
    media_path = Path("tests/data/en.wav")
    job_id = str(uuid.uuid4())

    speech_to_text.add_media(media_path, job_id)

    job = {
        "id": job_id,
        "media": [
            {
                "name": f"{job_id}/{media_path.name}",
                "options": {"language": "en", "writer": {"max_line_width": 20}},
            }
        ],
        "options": {"model": "tiny", "writer": {"max_line_width": 42}},
    }

    # run the job
    speech_to_text.main(job)

    # look at the done job to see if whisper ran with language=fr
    queue = speech_to_text.get_done_queue()
    msgs = queue.receive_messages(MaxNumberOfMessages=1, WaitTimeSeconds=10)
    assert len(msgs) == 1, "found a done message"

    job = json.loads(msgs[0].body)
    assert len(job["log"]["runs"]) == 1
    assert job["log"]["runs"][0]["media"] == f"{job_id}/{media_path.name}"
    assert job["log"]["runs"][0]["transcribe"]["language"] == "en"
    assert job["log"]["runs"][0]["transcribe"]["model"] == "tiny"
    assert job["log"]["runs"][0]["write"]["max_line_width"] == 20


# ignore utcnow warning until https://github.com/boto/boto3/issues/3889 is resolved
@pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow")
def test_exception_handling(bucket, queues):
    # ensure that an invalid job won't cause the processing to fail
    media_path = Path("tests/data/en.wav")
    job_id = str(uuid.uuid4())
    speech_to_text.add_media(media_path, job_id)
    job = {"id": job_id, "media": ["foo"]}

    with pytest.raises(Exception):
        speech_to_text.main(job)

    # look at the done job to see if whisper ran with language=fr
    queue = speech_to_text.get_done_queue()
    msgs = queue.receive_messages(MaxNumberOfMessages=1, WaitTimeSeconds=10)
    assert len(msgs) == 1, "found a done message"

    job = json.loads(msgs[0].body)
    assert "error" in job, "it's an error message"
    assert re.match("^Unexpected error.*", job["error"]), "error message"


# ignore utcnow warning until https://github.com/boto/boto3/issues/3889 is resolved
@pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow")
@patch("speech_to_text.honeybadger")  # Mock the honeybadger module
def test_honeybadger_notification(mock_honeybadger, bucket, queues, honeybadger_env):
    # ensure that honeybadger is notified when an exception occurs
    media_path = Path("tests/data/en.wav")
    job_id = str(uuid.uuid4())
    speech_to_text.add_media(media_path, job_id)

    # Create an invalid job to trigger an exception
    job = {"id": job_id, "media": ["foo"]}

    # run the job, expecting an exception
    with pytest.raises(Exception):
        speech_to_text.main(job)

    # Assert that honeybadger.notify was called
    mock_honeybadger.notify.assert_called_once()
    args, kwargs = mock_honeybadger.notify.call_args

    assert kwargs["error_class"] == "SpeechToTextError"
    assert "Whisper AWS process" in kwargs["error_message"]
    assert "job" in kwargs["context"]
    assert "traceback" in kwargs["context"]
