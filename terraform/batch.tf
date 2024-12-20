resource "aws_placement_group" "placement" {
  name     = var.project_name
  strategy = "cluster"
}

resource "aws_batch_compute_environment" "compute_environment" {
  compute_environment_name_prefix = var.project_name

  compute_resources {
    instance_role = aws_iam_instance_profile.ecs_instance_role.arn

    instance_type = [
      "g4dn.xlarge"
    ]

    allocation_strategy = "BEST_FIT_PROGRESSIVE"

    ec2_configuration {
      image_type = "ECS_AL2_NVIDIA"
    }

    min_vcpus = 0
    desired_vcpus = 4
    max_vcpus = 4

    placement_group = aws_placement_group.placement.name

    security_group_ids = [
      aws_security_group.batch_sg.id
    ]

    subnets = [
      aws_subnet.batch_subnet_public.id 
    ]

    launch_template {
      launch_template_id = aws_launch_template.batch.id
    }

    type = "EC2"
  }

  service_role = aws_iam_role.aws_batch_service_role.arn

  type         = "MANAGED"
  depends_on   = [
    aws_iam_role_policy_attachment.aws_batch_service_role
  ]

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_batch_job_definition" "job_definition" {
  name = var.project_name
  type = "container"

  container_properties = jsonencode({
    command = ["python3", "run.py"],
    image   = aws_ecr_repository.speech_to_text.repository_url

    logConfiguration = {
      logDriver = "awslogs"
    }

    resourceRequirements = [
      {
        type  = "VCPU"
        value = "4"
      },
      {
        type  = "MEMORY"
        value = "10000"
      },
      {
        type = "GPU"
        value = "1"
      }
    ]

    environment = [
      {
        name  = "SPEECH_TO_TEXT_S3_BUCKET"
        value = aws_s3_bucket.speech_to_text_bucket.id
      },
      {
        name = "SPEECH_TO_TEXT_DONE_SQS_QUEUE"
        value = aws_sqs_queue.done_queue.name
      },
      {
        name = "AWS_DEFAULT_REGION"
        value = var.region
      }
    ]

    command = [
      "--job",
      "Ref::job"
    ]
  })

  platform_capabilities = [
    "EC2"
  ]
}

resource "aws_batch_job_queue" "job_queue" {
  name     = var.project_name
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.compute_environment.arn
  }
}
