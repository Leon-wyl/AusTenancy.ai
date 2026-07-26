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

variable "repository_name" {
  type        = string
  description = "ECR repository name"
  default     = "austenancy-staging-agent"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]*[a-z0-9]$", var.repository_name))
    error_message = "repository_name must be a valid ECR repo name (lowercase, hyphens allowed)."
  }
}
