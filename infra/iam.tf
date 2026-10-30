# The bench instance's role: SSM (so there's no SSH), plus exactly what the measurements need on
# the two lab buckets. Nothing account-wide.

resource "aws_iam_role" "bench" {
  name = "zenzele-tiering-bench-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "bench_ssm" {
  role       = aws_iam_role.bench.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "bench_buckets" {
  name = "zenzele-tiering-lab-buckets"
  role = aws_iam_role.bench.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "LabBucketObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:RestoreObject"]
        Resource = ["${aws_s3_bucket.lab.arn}/*"]
      },
      {
        Sid      = "LabBucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.lab.arn]
      },
      {
        # Directory buckets authorise data requests through a session: one IAM action covers
        # read and write sessions on this bucket only.
        Sid      = "ExpressSession"
        Effect   = "Allow"
        Action   = ["s3express:CreateSession"]
        Resource = [aws_s3_directory_bucket.express.arn]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "bench" {
  name = "zenzele-tiering-bench-profile"
  role = aws_iam_role.bench.name
}
