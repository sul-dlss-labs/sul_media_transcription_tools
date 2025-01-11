# speech-to-text

[![Test](https://github.com/sul-dlss/speech-to-text/actions/workflows/test.yml/badge.svg)](https://github.com/sul-dlss/speech-to-text/actions/workflows/test.yml)

This repository contains a Docker configuration for performing speech-to-text processing with [Whisper](https://openai.com/index/whisper/) using Amazon Web Services (AWS) to provision GPU resources on demand, and to tear them down when there is no more remaining work. It uses:

* [AWS S3](https://aws.amazon.com/s3/): to store media in need of transcription and the transcription results
* [AWS Batch](https://aws.amazon.com/batch/): which manages w work queue and provisioning EC2 instances.
* [AWS SQS](https://aws.amazon.com/sqs/): to receive a notification when work is completed
* [AWS ECR](https://aws.amazon.com/ecr/): to store the speech-to-text Docker image

## Configure AWS

A [Terraform](https://www.terraform.io/) configuration is included to help you configure AWS. Once you have installed Terraform you can set up resources you need to configure your `project_name` which is used to name resources in AWS:

```shell
cd terraform
cp variables.example variables.tf
# edit variables.tf with your text editor
```

Now you can validate and (if everything looks ok) apply your changes:

```shell
cd terraform
terraform validate
terraform apply
```

## Build Docker Image

To build the container you will need to first download the pytorch models that Whisper uses. This is about 13GB of data and can take some time! The idea here is to bake the models into Docker image so they don't need to be fetched dynamically every time the container runs (which will add to the runtime). If you know you only need one size model, and want to just include that then edit the `whisper_models/urls.txt` file accordingly before running the `wget` command.

```shell
wget --directory-prefix whisper_models --input-file whisper_models/urls.txt
```

Then you can build the image:

```shell
docker build --tag sul-speech-to-text .
```

## Push Docker Image

You will need to push your Docker image to the ECR repository that Terraform created. You can ask Terraform for the repository URL that it created. For example mine is:

```shell
terraform output docker_repository
"482101366956.dkr.ecr.us-east-1.amazonaws.com/edsu-speech-to-text-qa"
```

Tag your Docker image with the ECR URL:

```shell
docker tag speech-to-text YOUR-ECR-URL
```

Ensure your Docker client is logged in:

```shell
aws ecr get-login-password | docker login --username AWS --password-stdin YOUR-ECR-URL
```

And then you can push the Docker image:

```shell
docker push YOUR-ECR-URL
```

## Run

### Create a Job

#### The Job Message Structure

The speech-to-text job is a JSON object that contains information about how to run Whisper. Minimally it contains the Job ID and a list of S3 bucket file paths, which will be used to locate media files in S3 that need to be processed.

```json
{
  "id": "gy983cn1444",
  "media": [
    { "name": "gy983cn1444/media.mp4" }
  ]
}
```

The job id must be a unique identifier like a UUID. In some use cases a natural key could be used, as is the case in the SDR where druid-version is used.

#### Whisper Options

You can also pass in options for Whisper, note that any options for how the transcript is serialized with a writer are given using the `writer` key:

```json
{
  "id": "gy983cn1444",
  "media": [
    { "name": "gy983cn1444/media.mp4" },
  ],
  "options": {
    "model": "large",
    "beam_size": 10,
    "writer": {
      "max_line_width": 80
    }
  }
}
```

If you are passing in multiple files and would like to specify different options for each file you can override at the file level. For example here two files are being transcribed, the first using French and the second in Spanish:

```json
{
  "id": "gy983cn1444",
  "media": [
    {
      "name": "gy983cn1444/media-fr.mp4",
      "options": {
        "language": "fr"
      }
    },
    {
      "name": "gy983cn1444/media-es.mp4",
      "options": {
        "language": "es"
      }
    }
  ],
  "options": {
    "model": "large",
    "beam_size": 10,
    "writer": {
      "max_line_width": 80
    }
  }
}
```

## Receiving Jobs

When a job completes you will receive a message on the DONE SQS queue which will contain JSON that looks something like:

```json
{
  "id": "gy983cn1444",
  "media": [
    {
      "name": "gy983cn1444/cat_videocat_video.mp4"
    },
    {
      "name": "gy983cn1444/The_Sea_otter.mp4",
      "language": "fr"
    }
  ],
  "options": {
    "model": "large",
    "beam_size": 10,
    "writer": {
      "max_line_count": 80
    }
  },
  "output": [
    "gy983cn1444/cat_video.vtt",
    "gy983cn1444/cat_video.srt",
    "gy983cn1444/cat_video.json",
    "gy983cn1444/cat_video.txt",
    "gy983cn1444/cat_video.tsv",
    "gy983cn1444/The_Sea_otter.vtt",
    "gy983cn1444/The_Sea_otter.srt",
    "gy983cn1444/The_Sea_otter.json",
    "gy983cn1444/The_Sea_otter.txt",
    "gy983cn1444/The_Sea_otter.tsv"
  ],
  "log": {
    "name": "whisper",
    "version": "20240930",
    "runs": [
      {
        "media": "gy983cn1444/cat_video.mp4",
        "transcribe": {
          "model": "large"
        },
        "write": {
          "max_line_count": 80,
          "word_timestamps": true
        }
      },
      {
        "media": "gy983cn1444/The_Sea_otter.mp4",
        "transcribe": {
          "model": "large",
          "language": "fr"
        },
        "write": {
          "max_line_count": 80,
          "word_timestamps": true
        }
      }
    ]
  }
}
```

You can then use an AWS S3 client to download the transcripts given in the `output` JSON stanza.

If there was an error during processing the `output` will be an empty list, and an `error` property will be set to a message indicating what went wrong.

```json
{
  "id": "gy983cn1444",
  "media": [
    "gy983cn1444/cat_videocat_video.mp4",
    "gy983cn1444/The_Sea_otter.mp4"
  ],
  "options": {
    "model": "large",
    "beam_size": 10,
    "writer": {
      "max_line_count": 80
    }
  },
  "output": [],
  "error": "Invalid media file gy983cn1444/The_Sea_otter.mp4"
}
```

## Manually Running a Job

The speech-to-text service has been designed so that software (in our case [common-accessioning](https://github.com/sul-dlss/common-accessioning)) can upload media files to S3 and then execute the AWS Batch job using an AWS client, and then listen for the "done" message. If you would like to simulate these steps yourself you can run the `speech_to_text.py` with the `--create` and `--done` flags.

First you will need a `.env` file that tells `speech_to_text.py` your AWS credentials and some of the resources that Terraform configured for you.

```
cp env-example .env
```

Then replace the `CHANGE_ME` values in the `.env` file. You can use `terraform output` to determine the names for AWS resources like the S3 bucket, the region, and the queues.

Then you are ready to create a job. Here a job is being created for the `file.mp4` media file:

```shell
python speech_to_text.py --create file.mp4
```

This will:

1. Mint a Job ID.
2. Upload `file.mp4` to the S3 bucket.
3. Send the job to AWS Batch using some default Whisper options.

Then you can check periodically to see if the job is completed by running:

```shell
python speech_to_text.py --done
```

This will:

1. Look for a done message in the SQS queue.
2. Delete the message from the queue so it isn't picked up again.
3. Print the received finished job JSON.
3. Download the generated transcript files from the S3 bucket to the current working directory.

## Testing

To run the tests it is probably easiest to create a virtual environment and run the tests with pytest:

```shell
python -mvenv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

Note: the tests use the [moto](https://docs.getmoto.org/en/latest/) library to mock out AWS resources. If you want to test live AWS you can follow the steps above to create a job, run, and then receive the done message.

You may need to install `ffmpeg` on your laptop in order to run the tests.  On a Mac, see if you have the dependency installed:

`which ffprobe`

If you get no result, install with:

`brew install ffmpeg`

## Updating Docker Image

When updating the base Docker image, in order to prevent random segmentation faults you will want to make sure that:

1. You are using an [nvidia/cuda](https://hub.docker.com/r/nvidia/cuda) base Docker image.
2. The version of CUDA you are using in the Docker container aligns with the version of CUDA that is installed in the host operating system that is running Docker.

## Linting and Type Checking

You may notice your changes fail in CI if they require reformatting or fail type checking. We use [ruff](https://docs.astral.sh/ruff/) for formatting Python code, and [mypy](https://mypy-lang.org/) for type checking. Both of those should be present in your virtual environment.

Check your code:

```shell
ruff check
```

If you want to reformat your code you can:

```shell
ruff format .
```

If you would prefer to see what would change you can:

```shell
ruff format --check .
```

Similarly if you would like to see if there are any type checking errors you can:

```shell
mypy .
```
