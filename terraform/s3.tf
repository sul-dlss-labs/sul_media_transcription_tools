resource "aws_s3_bucket" "speech_to_text_bucket" {
  bucket = "${var.project_name}"
}
