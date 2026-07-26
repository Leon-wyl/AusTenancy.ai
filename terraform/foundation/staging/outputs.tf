output "repository_url" {
  description = "ECR repository URL for docker push"
  value       = aws_ecr_repository.agent.repository_url
}

output "repository_arn" {
  description = "ECR repository ARN"
  value       = aws_ecr_repository.agent.arn
}

output "repository_name" {
  description = "ECR repository name"
  value       = aws_ecr_repository.agent.name
}
