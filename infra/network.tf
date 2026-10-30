# One VPC with one public subnet, pinned to the AZ ID that the S3 Express One Zone directory
# bucket uses: the bench instance and the directory bucket sit in the same AZ, which is how
# Express One Zone is meant to be used. No inbound rules at all: commands arrive through SSM.

data "aws_availability_zone" "lab" {
  zone_id = var.az_id
}

resource "aws_vpc" "lab" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "zenzele-tiering-vpc" }
}

resource "aws_subnet" "lab" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 1)
  availability_zone_id    = var.az_id
  map_public_ip_on_launch = true
  tags                    = { Name = "zenzele-tiering-public-${var.az_id}" }
}

resource "aws_internet_gateway" "lab" {
  vpc_id = aws_vpc.lab.id
  tags   = { Name = "zenzele-tiering-igw" }
}

resource "aws_route_table" "lab" {
  vpc_id = aws_vpc.lab.id
  tags   = { Name = "zenzele-tiering-rt" }
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.lab.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.lab.id
}

resource "aws_route_table_association" "lab" {
  subnet_id      = aws_subnet.lab.id
  route_table_id = aws_route_table.lab.id
}

# S3 traffic (general purpose buckets) stays on the AWS network through a gateway endpoint:
# no charge for the endpoint or for data through it.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.lab.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.lab.id]
  tags              = { Name = "zenzele-tiering-s3-gateway" }
}

resource "aws_security_group" "bench" {
  name        = "zenzele-tiering-bench-sg"
  description = "Bench instance - no inbound at all, outbound for SSM, package repos and S3"
  vpc_id      = aws_vpc.lab.id
  tags        = { Name = "zenzele-tiering-bench-sg" }
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.bench.id
  description       = "Outbound: SSM, dnf repositories, S3 and S3 Express"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
