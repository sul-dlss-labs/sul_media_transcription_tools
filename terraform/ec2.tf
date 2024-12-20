# VPC Resource
resource "aws_vpc" "batch_vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
}

# Internet Gateway
resource "aws_internet_gateway" "batch_igw" {
  vpc_id = aws_vpc.batch_vpc.id
}

# Public Subnet
resource "aws_subnet" "batch_subnet_public" {
  vpc_id                  = aws_vpc.batch_vpc.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = true
}

# Route Table
resource "aws_route_table" "batch_rt_public" {
  vpc_id = aws_vpc.batch_vpc.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.batch_igw.id
  }
}

# Route Table Association
resource "aws_route_table_association" "batch_rta_public" {
  subnet_id      = aws_subnet.batch_subnet_public.id
  route_table_id = aws_route_table.batch_rt_public.id
}

# Security Group
resource "aws_security_group" "batch_sg" {
  name        = "batch-security-group"
  description = "Security group for AWS Batch compute environment"
  vpc_id      = aws_vpc.batch_vpc.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Launch template to ensure that there is enough storage
resource "aws_launch_template" "batch" {
  name = "batch-launch-template"

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 500
    }
  }
}
