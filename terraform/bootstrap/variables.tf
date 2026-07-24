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

variable "bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name for Terraform state (e.g. austenancy-terraform-abc123)"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]*[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be a valid S3 bucket name (lowercase, no underscores)."
  }
  validation {
    condition     = length(var.bucket_name) >= 3 && length(var.bucket_name) <= 63
    error_message = "bucket_name must be between 3 and 63 characters."
  }
}
