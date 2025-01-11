resource "aws_sqs_queue" "done_queue" {
  name                       = "${var.project_name}-done"
  visibility_timeout_seconds = "30"
}
