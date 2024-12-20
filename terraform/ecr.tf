resource "aws_ecr_repository" "speech_to_text" {
  name = var.project_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}
