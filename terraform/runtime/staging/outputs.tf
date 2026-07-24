output "lambda_function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.agent.function_name
}

output "lambda_function_arn" {
  description = "Lambda function ARN"
  value       = aws_lambda_function.agent.arn
}

output "api_endpoint" {
  description = "HTTP API endpoint URL"
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "health_url" {
  description = "GET /health endpoint URL (public)"
  value       = "${aws_apigatewayv2_stage.default.invoke_url}/health"
}

output "invoke_route" {
  description = "POST /api/agent/invoke route (AWS_IAM required)"
  value       = "${aws_apigatewayv2_api.agent.api_endpoint}/api/agent/invoke"
}

output "log_group_name" {
  description = "Lambda CloudWatch Logs log group name"
  value       = aws_cloudwatch_log_group.lambda.name
}

output "execution_role_arn" {
  description = "Lambda execution IAM role ARN"
  value       = aws_iam_role.lambda_execution.arn
}

output "deployed_image_uri" {
  description = "Image URI deployed to Lambda (immutable digest)"
  value       = var.image_uri
}
