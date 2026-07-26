terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.allowed_account_id]

  default_tags {
    tags = local.tags
  }
}

locals {
  project        = "austenancy"
  environment    = "staging"
  api_stage_name = "$default"
  tags = {
    Project     = local.project
    Environment = local.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  name                        = "/aws/lambda/austenancy-staging-agent"
  retention_in_days           = var.log_retention_days
  deletion_protection_enabled = true
  tags                        = local.tags
}

resource "aws_cloudwatch_log_group" "api" {
  name                        = "/aws/apigateway/austenancy-staging-agent"
  retention_in_days           = var.log_retention_days
  deletion_protection_enabled = true
  tags                        = local.tags
}

resource "aws_iam_role" "lambda_execution" {
  name = "austenancy-staging-agent-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "lambda_execution" {
  name = "austenancy-staging-agent-execution"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.lambda.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
        ]
        Resource = "arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_model_id}"
      },
    ]
  })
}

resource "aws_lambda_function" "agent" {
  function_name = "austenancy-staging-agent"
  role          = aws_iam_role.lambda_execution.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = ["x86_64"]
  memory_size   = var.lambda_memory_mb
  timeout       = var.timeout_seconds

  ephemeral_storage {
    size = var.ephemeral_storage_mb
  }

  environment {
    variables = {
      LLM_PROVIDER         = "bedrock"
      BEDROCK_MODEL_ID     = var.bedrock_model_id
      BEDROCK_TEMPERATURE  = "0"
      QDRANT_PATH          = "/tmp/qdrant_storage"
      FASTEMBED_CACHE_PATH = "/var/task/assets/fastembed_cache"
      HF_HUB_OFFLINE       = "1"
      SOURCE_GIT_SHA       = var.source_git_sha
    }
  }

  tags = local.tags

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda_execution,
  ]
}

resource "aws_apigatewayv2_api" "agent" {
  name          = "austenancy-staging-agent"
  protocol_type = "HTTP"
  tags          = local.tags
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.agent.id
  name        = local.api_stage_name
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId          = "$context.requestId"
      routeKey           = "$context.routeKey"
      status             = "$context.status"
      integrationStatus  = "$context.integrationStatus"
      integrationLatency = "$context.integrationLatency"
      responseLatency    = "$context.responseLatency"
      responseLength     = "$context.responseLength"
    })
  }

  tags = local.tags
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.agent.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.agent.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "health" {
  api_id             = aws_apigatewayv2_api.agent.id
  route_key          = "GET /health"
  authorization_type = "NONE"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "invoke" {
  api_id             = aws_apigatewayv2_api.agent.id
  route_key          = "POST /api/agent/invoke"
  authorization_type = "AWS_IAM"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_lambda_permission" "health" {
  statement_id  = "AllowApiGatewayHealthInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = join("/", [
    aws_apigatewayv2_api.agent.execution_arn,
    local.api_stage_name,
    "GET",
    "health",
  ])
}

resource "aws_lambda_permission" "agent_invoke" {
  statement_id  = "AllowApiGatewayAgentInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = join("/", [
    aws_apigatewayv2_api.agent.execution_arn,
    local.api_stage_name,
    "POST",
    "api/agent/invoke",
  ])
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "austenancy-staging-agent-errors"
  alarm_description   = "Lambda invocation errors > 0"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.agent.function_name
  }

  alarm_actions = var.alarm_action_arns
  ok_actions    = var.alarm_action_arns
  tags          = local.tags
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name          = "austenancy-staging-agent-throttles"
  alarm_description   = "Lambda throttles > 0"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.agent.function_name
  }

  alarm_actions = var.alarm_action_arns
  ok_actions    = var.alarm_action_arns
  tags          = local.tags
}

resource "aws_cloudwatch_metric_alarm" "lambda_duration" {
  alarm_name          = "austenancy-staging-agent-duration"
  alarm_description   = "Lambda duration approaching HTTP API integration timeout"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Maximum"
  threshold           = var.lambda_duration_alarm_threshold_ms
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.agent.function_name
  }

  alarm_actions = var.alarm_action_arns
  ok_actions    = var.alarm_action_arns
  tags          = local.tags
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "austenancy-staging-agent-api-5xx"
  alarm_description   = "HTTP API 5xx errors > 0"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "5xx"
  namespace           = "AWS/ApiGateway"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiId = aws_apigatewayv2_api.agent.id
    Stage = local.api_stage_name
  }

  alarm_actions = var.alarm_action_arns
  ok_actions    = var.alarm_action_arns
  tags          = local.tags
}
