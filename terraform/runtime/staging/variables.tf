variable "region" {
  type        = string
  description = "AWS region"
  default     = "ap-southeast-2"
}

variable "allowed_account_id" {
  type        = string
  description = "AWS account ID approved for this environment"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be a 12-digit AWS account ID."
  }
}

variable "ecr_repository_name" {
  type        = string
  description = "Approved ECR repository name"
  default     = "austenancy-staging-agent"
  validation {
    condition     = var.ecr_repository_name == "austenancy-staging-agent"
    error_message = "Only the approved staging repository austenancy-staging-agent may be used."
  }
}

variable "image_uri" {
  type        = string
  description = "Immutable ECR image URI ending in @sha256:<digest>"
  validation {
    condition = can(regex(
      "^${var.allowed_account_id}\\.dkr\\.ecr\\.${var.region}\\.amazonaws\\.com/${var.ecr_repository_name}@sha256:[0-9a-f]{64}$",
      var.image_uri
    ))
    error_message = "image_uri must reference the approved account, region, repository, and immutable SHA-256 digest."
  }
}

variable "source_git_sha" {
  type        = string
  description = "Full 40-character Git SHA of the deployed source"
  validation {
    condition     = can(regex("^[a-f0-9]{40}$", var.source_git_sha))
    error_message = "source_git_sha must be a full 40-character lowercase hexadecimal Git SHA."
  }
}

variable "bedrock_model_id" {
  type        = string
  description = "Bedrock foundation model ID (staging: amazon.nova-pro-v1:0)"
  default     = "amazon.nova-pro-v1:0"
  validation {
    condition     = var.bedrock_model_id == "amazon.nova-pro-v1:0"
    error_message = "Only amazon.nova-pro-v1:0 is approved for staging."
  }
}

variable "lambda_memory_mb" {
  type        = number
  description = "Lambda memory in MB"
  default     = 1024
  validation {
    condition     = var.lambda_memory_mb >= 512 && var.lambda_memory_mb <= 10240
    error_message = "lambda_memory_mb must be between 512 and 10240."
  }
}

variable "timeout_seconds" {
  type        = number
  description = "Lambda function timeout in seconds"
  default     = 60
  validation {
    condition     = var.timeout_seconds >= 1 && var.timeout_seconds <= 60
    error_message = "timeout_seconds must be between 1 and 60."
  }
}

variable "ephemeral_storage_mb" {
  type        = number
  description = "Lambda /tmp ephemeral storage in MB"
  default     = 2048
  validation {
    condition     = var.ephemeral_storage_mb >= 512 && var.ephemeral_storage_mb <= 10240
    error_message = "ephemeral_storage_mb must be between 512 and 10240."
  }
}

variable "log_retention_days" {
  type        = number
  description = "CloudWatch Logs retention in days"
  default     = 30
  validation {
    condition = contains(
      [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653],
      var.log_retention_days
    )
    error_message = "log_retention_days must be a valid CloudWatch Logs retention value."
  }
}

variable "lambda_duration_alarm_threshold_ms" {
  type        = number
  description = "Lambda duration alarm threshold in ms (approaching HTTP API 30 s timeout)"
  default     = 28000
  validation {
    condition = (
      var.lambda_duration_alarm_threshold_ms >= 1000 &&
      var.lambda_duration_alarm_threshold_ms < 30000
    )
    error_message = "lambda_duration_alarm_threshold_ms must be between 1000 and 29999."
  }
}

variable "alarm_action_arns" {
  type        = list(string)
  description = "SNS topic ARNs for CloudWatch alarm notifications (default [])"
  default     = []
}
