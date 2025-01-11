#!/usr/bin/env python3

import argparse
import datetime
import json
import logging
import os
import sys
import uuid
import shutil
import subprocess
import traceback
from pathlib import Path
from typing import Optional, Dict

import boto3
import dotenv
import torch
import whisper
from honeybadger import honeybadger
from whisper.utils import get_writer
from mypy_boto3_s3.service_resource import Bucket, S3ServiceResource
from mypy_boto3_sqs.service_resource import Queue


def main(job: Dict) -> None:
    try:
        if job is None:
            logging.info("no jobs waiting in the todo queue")
        else:
            logging.info(f"starting job {job}")
            job = download_media(job)
            job = run_whisper(job)
            job = upload_results(job)
            job = finish_job(job)
            logging.info(f"finished job {job}")
    except SpeechToTextException as e:
        report_error(f"Unexpected error while processing job: {e}", job, e)
    except Exception as e:
        report_error(f"Unexpected error: {e}", job, e)


def download_media(job: dict) -> dict:
    bucket = get_bucket()

    output_dir = get_output_dir(job)
    if not output_dir.is_dir():
        output_dir.mkdir()

    for media in job["media"]:
        # note the media_file is expected to be the full path in the bucket
        # e.g. pg879tb2706-v2/video_1.mp4
        media_file = media["name"]
        bucket.download_file(media_file, media_file)

        media_info = inspect_media(media_file)
        logging.info(f"downloaded {media_file}: {json.dumps(media_info)}")

    return job


def run_whisper(job: dict) -> dict:
    # the code for interacting with whisper here was adapted from
    # https://github.com/openai/whisper/blob/main/whisper/transcribe.py

    options = job.get("options", {}).copy()

    model_name = options.get("model", "large")
    model = load_whisper_model(model_name)

    # configure the whisper writer that will take the whisper JSON output and
    # convert to other formats like vtt, txt, etc
    output_dir = get_output_dir(job)
    writer = get_writer(output_format="all", output_dir=output_dir)

    # accumulate the options that were used for transcription and writing
    runs = []

    for media in job["media"]:
        media_file = media["name"]

        whisper_options = {**options, **media.get("options", {})}

        writer_options = whisper_options.get("writer", {})
        if len(writer_options) > 0:
            whisper_options["word_timestamps"] = True

        # remove model and writer from options that are passed to whisper
        whisper_options.pop("model", None)
        whisper_options.pop("writer", None)

        try:
            logging.info(
                f"running whisper on {media_file} with model={model_name} options={whisper_options}"
            )
            result = whisper.transcribe(
                audio=media_file, model=model, **whisper_options
            )
            logging.info(f"whisper result: {result}")

            logging.info(f"writing output using writer_options: {writer_options}")
            writer(result, media_file, writer_options)
        except Exception as e:
            raise SpeechToTextException(str(e))

        runs.append(
            {
                "media": media_file,
                "transcribe": {"model": model_name, **whisper_options},
                "write": writer_options,
            }
        )

    job["finished"] = now()

    job["log"] = {
        "name": "whisper",
        "version": whisper.version.__version__,
        "runs": runs,
    }

    return job


def upload_results(job: dict) -> dict:
    """
    Upload the Whisper output to S3, and put the job file there too. The job
    file will have the output key added to it, which will contain a list of
    bucket path names for the results.
    """
    bucket = get_bucket()

    job["output"] = []
    output_dir = get_output_dir(job)
    for path in output_dir.iterdir():
        # ignore non output files
        if path.suffix not in [".vtt", ".srt", ".json", ".txt", ".tsv"]:
            continue

        key = f"{job['id']}/output/{path.name}"
        logging.info(f"wrote whisper result to s3://{bucket.name}/{key}")
        bucket.upload_file(str(path), key)
        job["output"].append(key)

    bucket.put_object(Key=f"{job['id']}/job.json", Body=json.dumps(job, indent=2))

    # the files have landed in s3 so the local copies can be deleted so they
    # don't accumulate in the docker container over time
    logging.info(f"deleting local files for job: {output_dir}")
    shutil.rmtree(output_dir)

    return job


def finish_job(job: dict) -> dict:
    queue = get_done_queue()
    logging.info(f"sending message to done queue: {job}")
    queue.send_message(MessageBody=json.dumps(job))

    return job


def get_s3() -> S3ServiceResource:
    return boto3.resource("s3", **get_session())


def get_session() -> dict:
    # This would be a lot easier if boto3 read AWS_ROLE_ARN like it does other
    # environment variables:
    #
    # see: https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_switch-role-api.html
    session = {}

    role = os.environ.get("AWS_ROLE_ARN")

    if role:
        sts_client = boto3.client("sts")
        response = sts_client.assume_role(
            RoleArn=role, RoleSessionName="speech-to-text"
        )
        session = {
            "aws_access_key_id": response["Credentials"]["AccessKeyId"],
            "aws_secret_access_key": response["Credentials"]["SecretAccessKey"],
            "aws_session_token": response["Credentials"]["SessionToken"],
        }

    return session


def get_bucket() -> Bucket:
    s3 = get_s3()
    bucket_name = os.environ.get("SPEECH_TO_TEXT_S3_BUCKET", "")
    return s3.Bucket(bucket_name)


def get_done_queue() -> Queue:
    sqs = boto3.resource("sqs", **get_session())
    return sqs.get_queue_by_name(
        QueueName=os.environ.get("SPEECH_TO_TEXT_DONE_SQS_QUEUE", "")
    )


def report_error(message: str, job: Optional[Dict], e: Exception) -> None:
    """
    Add the job to the done queue with an error.
    """
    stacktrace = traceback.format_exc()
    full_message = message + "\n" + stacktrace
    logging.exception(full_message)

    # it's possible that we are reporting an error without a job
    # we can only send a message to the DONE queue if we have a job!
    if job is not None:
        job["error"] = full_message

        queue = get_done_queue()
        logging.error(f"sending error message to done queue: {job}")
        queue.send_message(MessageBody=json.dumps(job))

    hb_key = os.environ.get("HONEYBADGER_API_KEY", "")
    hb_env = os.environ.get("HONEYBADGER_ENV", "stage")
    honeybadger.configure(api_key=hb_key, environment=hb_env)

    honeybadger.notify(
        error_class="SpeechToTextError",
        error_message="Whisper AWS process \n" + message,
        context={"job": job, "traceback": stacktrace},
    )

    raise e


def check_env() -> None:
    names = ["SPEECH_TO_TEXT_S3_BUCKET", "SPEECH_TO_TEXT_DONE_SQS_QUEUE"]
    for name in names:
        if os.environ.get(name) is None:
            sys.exit(f"{name} is not defined in the environment")


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def get_output_dir(job) -> Path:
    return Path(job["id"])


def inspect_media(path) -> dict:
    try:
        output = subprocess.check_output(
            ["ffprobe", "-show_format", "-print_format", "json", "-v", "quiet", path]
        )
        result = json.loads(output)
        return {
            "duration": float(result["format"]["duration"]),
            "format": result["format"]["format_name"],
            "size": int(result["format"]["size"]),
        }
    except subprocess.CalledProcessError:
        raise SpeechToTextException(f"Invalid media file {path}")


def load_whisper_model(model_name) -> whisper.model.Whisper:
    if torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"

    logging.info(f"loading {model_name} Whisper model for {device}")

    return whisper.load_model(model_name, download_root="whisper_models", device=device)


# the functions below are for initiating a speech-to-text job
# this functionality is here mostly for testing but the idea is that
# you would upload files to s3 and initiate the batch job using
# an AWS client library


def create(media_path: Path, options={}):
    """
    Create a job for a given media file by placing the media file in S3 and then
    performing the transcription using the supplied options.
    """
    job_id = str(uuid.uuid4())

    add_media(media_path, job_id)

    batch = boto3.client("batch", **get_session())

    job = {
        "id": job_id,
        "media": [{"name": f"{job_id}/{Path(media_path).name}"}],
        "options": {
            "model": "large",
            "word_timestamps": True,
            "condition_on_previous_text": False,
            "writer": {"max_line_width": 42, "max_line_count": 1},
        },
    }

    result = batch.submit_job(
        jobName=job_id,
        jobQueue=os.environ.get("SPEECH_TO_TEXT_BATCH_JOB_QUEUE"),
        jobDefinition=os.environ.get("SPEECH_TO_TEXT_BATCH_JOB_DEFINITION"),
        parameters={"job": str(json.dumps(job))},
    )

    logging.info(f"started batch job: {json.dumps(result)}")


def add_media(media_path, job_id) -> str:
    """
    Upload a media file to the bucket, and return the filename.
    """
    path = Path(media_path)
    key = f"{job_id}/{path.name}"

    bucket = get_bucket()
    logging.info(f"uploading {media_path} to s3://{bucket.name}/{key}")
    bucket.upload_file(media_path, key)

    return path.name


def get_done() -> None:
    """
    Fetch a message form the done SQS queue and print it. Note this will remove
    it from the queue.

    This is mostly here just for testing.
    """
    queue = get_done_queue()
    messages = queue.receive_messages(MaxNumberOfMessages=1, WaitTimeSeconds=1)
    if len(messages) == 0:
        return

    msg = messages[0]
    msg.delete()  # prevent it from being picked up again
    job = json.loads(msg.body)
    print(json.dumps(job, indent=2))

    bucket = get_bucket()
    for key in job.get("output", []):
        local_file = Path(key).name
        bucket.download_file(key, local_file)
        print(f"downloaded {local_file}")


class SpeechToTextException(Exception):
    pass


if __name__ == "__main__":
    dotenv.load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s :: %(levelname)s :: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    check_env()

    parser = argparse.ArgumentParser(prog="speech_to_text")
    parser.add_argument(
        "-j",
        "--job",
        help="A JSON string for a job, or path to a JSON file",
    )
    parser.add_argument("-c", "--create", help="Create the Job in the Batch queue")
    parser.add_argument(
        "-d",
        "--done",
        help="Look for completed jobs and download the results",
        action="store_true",
    )
    args = parser.parse_args()

    # get the job either from a JSON string or file
    try:
        if args.job and Path(args.job).is_file():
            job = json.load(open(args.job))
        elif args.job:
            job = json.loads(args.job)
        else:
            job = {}
    except json.decoder.JSONDecodeError as e:
        sys.exit(f"Invalid job {e} for JSON {args.job}")

    if args.create:
        create(args.create)
    elif args.done:
        get_done()
    else:
        main(job)
